# coding: utf-8
"""Hands-off proposals and confirmed copies use generated samples only."""
import hashlib
import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import time
from unittest import mock
from urllib.parse import urlsplit

import test_photo_bundle as fixtures
import library_server
from file_operations import FileOperations
from local_movie_metadata import parse_nfo
from media_actions import MediaActions, media_id
from simple_organize import movie_region, propose_simple_copy
from test_library_server import MemoryServer, request
from test_support import make_symlink


class SimpleOrganizeTests(unittest.TestCase):
    setUp = fixtures.PhotoBundleTests.setUp
    file = fixtures.PhotoBundleTests.file
    scan = fixtures.PhotoBundleTests.scan

    def wait(self, ops, job):
        # A real 200-file batch flushes and verifies every file and journal.
        # Windows runner storage can take over 30 seconds; do not weaken copies
        # or race TemporaryDirectory cleanup against a still-running worker.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = ops.snapshot(job['id'])
            if result['status'] != 'running':
                return result
            time.sleep(.05)
        ops.request_stop(job['id'])
        stop_deadline = time.monotonic() + 15
        while time.monotonic() < stop_deadline:
            result = ops.snapshot(job['id'])
            if result['status'] != 'running':
                break
            time.sleep(.05)
        self.fail('Generated simple copy timed out: ' + str(result))

    def sample(self):
        self.file('album/20210506_IMG.JPG')
        self.file('album/20210506_IMG.DNG')
        self.file('album/20210506_IMG.MOV')
        self.file('album/20210506_IMG.xmp')
        self.file('film/ABC-123.mp4')
        nfo = self.file('film/ABC-123.nfo')
        nfo.write_text('<movie><title>样例电影</title><country>日本</country></movie>', encoding='utf-8')
        self.file('film/ABC-123.zh.srt')
        self.file('film/poster.png')
        self.file('film/extrafanart/scene.png')
        return self.scan()

    def test_pending_items_copy_by_photo_month_and_nfo_region_with_all_companions(self):
        report, document, plan, ops = self.sample()
        before = {item['path']: (Path(item['path']).read_bytes(), Path(item['path']).stat().st_mtime_ns)
                  for item in document['files'] + document['sidecars']}
        snapshot = plan.snapshot()
        preview = ops.quick_preview(str(self.destination))
        self.assertEqual(len(preview['items']), 9, preview)
        self.assertEqual({item['state'] for item in snapshot['items']}, {'pending'})
        self.assertEqual(plan.snapshot(), snapshot)
        self.assertFalse((report / 'organization-plan.json').exists())
        self.assertFalse(list(self.destination.iterdir()))
        for item in preview['items']:
            self.assertTrue(item['relative'].startswith('照片/2021/05/' if '/album/' in item['path'].replace('\\', '/') else '电影/日韩/'), item)
            self.assertEqual(Path(item['target']).name, Path(item['path']).name)
        self.assertTrue(any('/extrafanart/scene.png' in item['relative'] for item in preview['items']))
        result = self.wait(ops, ops.start(preview['token']))
        self.assertEqual(result['status'], 'complete', result)
        self.assertTrue(result['quick'])
        for item in result['items']:
            self.assertEqual(Path(item['target']).read_bytes(), before[item['path']][0])
            self.assertEqual(item['sha256'], hashlib.sha256(before[item['path']][0]).hexdigest())
        self.assertEqual(before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in before})
        self.assertEqual(plan.snapshot(), snapshot)
        reopened = FileOperations(report, MediaActions(document), lambda: plan, document=document)
        self.assertTrue(reopened.snapshot(result['id'])['quick'])
        second = reopened.quick_preview(str(self.destination))
        self.assertIsNone(second['token'])
        self.assertFalse(second['items'])
        self.assertTrue(all('成功处理' in item['reason'] for item in second['skipped']))

    def test_country_aliases_coproduction_unknown_and_explicit_folder_fallback(self):
        for countries, expected in [(['中国大陆', '香港'], '国产'), (['Japan / South Korea'], '日韩'),
                                    (['United States', 'France'], '欧美'), (['印度'], '其他'),
                                    (['中国', 'Japan'], '待确认地区')]:
            metadata = parse_nfo(('<movie>'+''.join('<country>'+value+'</country>' for value in countries)+'</movie>').encode())
            self.assertEqual(metadata['countries'], countries)
            self.assertEqual(movie_region([{'metadata': metadata, 'files': []}])[0], expected)
        for directory, expected in [('国产', '国产'), ('日韩电影', '日韩'), ('日本', '日韩'),
                                    ('USA', '欧美'), ('some-Japan-trip', '待确认地区'), ('其他', '待确认地区')]:
            self.assertEqual(movie_region([{'files': [{'path': str(self.source / directory / 'movie.mp4')}]}])[0], expected)
        self.assertEqual(movie_region([{'metadata': {'countries': ['日本']}, 'files': [{'path': str(self.source / '国产' / 'm.mp4')}]}])[0], '日韩')

    def test_missing_region_is_visible_pending_folder_not_filename_guess(self):
        self.file('Japan-trip.mp4')
        _, _, _, ops = self.scan()
        preview = ops.quick_preview(str(self.destination))
        self.assertIn('电影/待确认地区/', preview['items'][0]['relative'])
        self.assertIn('没有唯一地区依据', preview['groups'][0]['notes'][0])

    def test_same_named_photos_keep_pair_names_in_stable_distinct_folders(self):
        for folder in ('a', 'b'):
            self.file(folder+'/20210506_IMG.jpg')
            self.file(folder+'/20210506_IMG.xmp')
        _, _, _, ops = self.scan()
        preview = ops.quick_preview(str(self.destination))
        self.assertEqual(len(preview['items']), 4)
        parents = {str(Path(item['target']).parent) for item in preview['items']}
        self.assertEqual(len(parents), 2)
        self.assertTrue(all(item['relative'].startswith('照片/2021/05/') for item in preview['items']))
        self.assertEqual(self.wait(ops, ops.start(preview['token']))['status'], 'complete')

    def test_prefers_capture_date_from_jpeg_over_raw_mtime_without_rereading(self):
        self.file('IMG.dng'); jpg = self.file('IMG.jpg')
        _, document, plan, _ = self.scan()
        record = next(item for item in document['files'] if item['path'] == str(jpg))
        record.update(suggested_path='照片/2019/02/IMG.jpg', date_source='EXIF 拍摄日期')
        proposal = propose_simple_copy(document, plan.snapshot(), {'jobs': []})
        self.assertEqual({item['target'] for unit in proposal['units'] for item in unit['items']}, {'照片/2019/02/IMG.jpg', '照片/2019/02/IMG.dng'})

    def test_hold_and_manual_targets_are_preserved_and_ambiguities_are_not_guessed(self):
        a = self.file('a.jpg'); b = self.file('b.jpg')
        for name in ('IMG.jpg', 'IMG.jpeg', 'IMG.mov', 'IMG.mp4', 'IMG.xmp'):
            self.file(name)
        _, _, plan, ops = self.scan()
        plan.set_states([media_id(str(a))], 'hold')
        plan.set_target(media_id(str(b)), '用户/手动/b.jpg')
        before = plan.snapshot()
        preview = ops.quick_preview(str(self.destination))
        self.assertFalse(preview['items'], preview)
        reasons = ' '.join(item['reason'] for item in preview['skipped'])
        self.assertIn('暂缓', reasons); self.assertIn('手动分类', reasons); self.assertIn('歧义', reasons)
        self.assertEqual(plan.snapshot(), before)

    def test_confirmation_rechecks_plan_metadata_and_attached_file_identity(self):
        for change in ('hold', 'country', 'attachment', 'target'):
            with self.subTest(change=change):
                report, document, plan, ops = self.sample()
                preview = ops.quick_preview(str(self.destination))
                if change == 'hold':
                    plan.set_states([preview['items'][0]['id']], 'hold')
                elif change == 'country':
                    next(group for group in document['video_library']['groups'] if group.get('metadata', {}).get('countries'))['metadata']['countries'] = ['中国']
                elif change == 'attachment':
                    next(path for path in self.source.rglob('*.srt')).write_bytes(b'changed generated subtitle')
                else:
                    target = Path(preview['items'][0]['target'])
                    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'keep existing target')
                with self.assertRaises((ValueError, OSError)):
                    ops.start(preview['token'])
                self.assertFalse((report / 'operations').exists())
                # Reset this subcase's generated trees; no original user media involved.
                shutil.rmtree(self.source); self.source.mkdir()
                shutil.rmtree(self.output)
                shutil.rmtree(self.destination); self.destination.mkdir()

    def test_preview_skips_whole_group_for_existing_target_or_stale_source(self):
        _, _, _, ops = self.sample()
        first = ops.quick_preview(str(self.destination))
        movie = next(item for item in first['items'] if item['role'] == '字幕')
        target = Path(movie['target']); target.parent.mkdir(parents=True); target.write_bytes(b'existing')
        second = ops.quick_preview(str(self.destination))
        self.assertEqual(len(second['items']), 4)
        self.assertTrue(all(item['relative'].startswith('照片/') for item in second['items']))
        self.assertEqual(target.read_bytes(), b'existing')
        Path(second['items'][0]['path']).write_bytes(b'changed')
        self.assertFalse(ops.quick_preview(str(self.destination))['items'])

    def test_two_batches_keep_photo_pair_whole_and_skip_success_without_recopy(self):
        for number in range(101):
            for extension in ('jpg', 'xmp'):
                self.file('20210506_%03d.' % number + extension)
        _, _, _, ops = self.scan()
        first = ops.quick_preview(str(self.destination))
        self.assertEqual(len(first['items']), 200)
        self.assertEqual(first['remaining_count'], 2)
        self.assertEqual(self.wait(ops, ops.start(first['token']))['status'], 'complete')
        second = ops.quick_preview(str(self.destination))
        self.assertEqual(len(second['items']), 2)
        self.assertEqual(second['remaining_count'], 0)
        self.assertFalse({item['path'] for item in first['items']} & {item['path'] for item in second['items']})
        self.assertEqual(self.wait(ops, ops.start(second['token']))['status'], 'complete')

    def test_stop_failed_history_corrupt_history_and_legacy_reports(self):
        report, document, plan, ops = self.sample()
        preview = ops.quick_preview(str(self.destination))
        queued = []
        with mock.patch('file_operations.threading.Thread.start', autospec=True, side_effect=queued.append):
            job = ops.start(preview['token'])
        ops.request_stop(job['id']); queued[0].run()
        self.assertEqual(ops.snapshot(job['id'])['status'], 'cancelled')
        self.assertFalse(list(self.destination.iterdir()))
        with self.assertRaisesRegex(ValueError, '逐项记录'):
            ops.remaining(job['id'])
        journal = report/'operations'/(job['id']+'.json')
        entry = ops.snapshot(job['id']); entry.update(status='stopped', items=[{**entry['planned_items'][0], 'status': 'failed'}])
        journal.write_text(json.dumps(entry), encoding='utf-8')
        reopened = FileOperations(report, MediaActions(document), lambda: plan, document=document)
        skipped = reopened.quick_preview(str(self.destination))['skipped']
        self.assertTrue(any('不会自动重试' in item['reason'] for item in skipped))
        entry['quick'] = 'true'; journal.write_text(json.dumps(entry), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '操作记录'):
            reopened.quick_preview(str(self.destination))
        journal.unlink()
        document['restored_snapshot'] = True
        with self.assertRaisesRegex(ValueError, '重新扫描'):
            reopened.quick_preview(str(self.destination))
        document.pop('restored_snapshot')
        for record in document['files'] + document['sidecars']:
            record.pop('source_signature', None)
        self.assertFalse(reopened.quick_preview(str(self.destination))['items'])

    def test_target_inside_source_and_source_replaced_by_link_are_rejected(self):
        self.file('20210506_IMG.jpg')
        _, _, _, ops = self.scan()
        with self.assertRaises(ValueError):
            ops.quick_preview(str(self.source))
        preview = ops.quick_preview(str(self.destination))
        path = Path(preview['items'][0]['path']); path.unlink()
        other = self.file('other.jpg'); make_symlink(path, other)
        with self.assertRaises((OSError, ValueError)):
            ops.start(preview['token'])
        self.assertFalse(list(self.destination.iterdir()))

    def test_api_exposes_simple_preview_without_selection_and_blocks_foreign_origin(self):
        report, _, _, _ = self.sample()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        payload = {'destination': str(self.destination), 'mode': 'trash', 'targets': ['arbitrary']}
        headers, body = request(server, prefix+'api/operations/quick-preview', 'POST', payload, origin='http://127.0.0.1:43210')
        self.assertIn('200 OK', headers)
        result = json.loads(body)
        self.assertEqual(result['mode'], 'copy'); self.assertEqual(len(result['items']), 9)
        self.assertNotIn('_media', result); self.assertNotIn('_quick_digest', result)
        headers, _ = request(server, prefix+'api/operations/quick-preview', 'POST', payload, origin='https://example.com')
        self.assertIn('403', headers)
        self.assertFalse(list(self.destination.iterdir()))

    def test_actual_ui_supports_empty_selection_and_no_start_for_empty_proposal(self):
        node = shutil.which('node'); self.assertIsNotNone(node)
        code = r'''
const fs=require('fs'),vm=require('vm');
class E{constructor(tag,text){this.textContent=text||'';this.children=[];this.value='/generated/copies';this.checked=true;}append(...v){this.children.push(...v);}replaceChildren(...v){this.children=v;}showModal(){this.open=true;}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};
let route,payload,empty=false;const scope={$,make:(t,c,s)=>new E(t,s),operating:false,saving:false,selected:new Set(),error(){},render(){},fileSize:String,api:async(r,b)=>{route=r;payload=b;return {token:empty?null:'preview',items:empty?[]:[{path:'/generated/a.jpg',target:'/copies/照片/2021/05/a.jpg'}],bytes:1,groups:[{title:'a',count:1,notes:['日期待核对']}],remaining_count:3,skipped:[]};}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(source.indexOf('async function previewOperation'),source.indexOf('function cancelPreview')),scope);
(async()=>{await $('quick-preview').onclick();if(route!=='api/operations/quick-preview'||Object.keys(payload).join()!=='destination')throw Error('must not require selections or send arbitrary targets');if(!$('execution').open||$('execution-confirm').checked||!$('execution-start').disabled)throw Error('confirmation required');if(!JSON.stringify($('execution-items').children).includes('下一批'))throw Error('missing remainder');$('execution-confirm').checked=true;$('execution-confirm').onchange();if($('execution-start').disabled)throw Error('valid proposal should be confirmable');empty=true;await $('quick-preview').onclick();$('execution-confirm').checked=true;$('execution-confirm').onchange();if(!$('execution-start').disabled||!$('execution-confirm').disabled)throw Error('empty proposal must not start');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', code, str(Path(__file__).parent/'management.js')], text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_overlapping_movie_groups_and_case_equivalent_directories_are_not_copied(self):
        for scenario in ('overlap', 'case'):
            with self.subTest(scenario=scenario):
                self.file('a/Film.mp4'); self.file('b/Other.mp4')
                _, document, plan, _ = self.scan()
                groups = document['video_library']['groups']
                self.assertEqual(len(groups), 2)
                if scenario == 'overlap':
                    repeated = copy.deepcopy(groups[0]); repeated['work_key'] = 'new-work'
                    groups.append(repeated)
                else:
                    for number, group in enumerate(groups):
                        group['work_key'] = 'a'*12 + str(number)
                        group['title'] = 'Film' if number else 'film'
                proposal = propose_simple_copy(document, plan.snapshot(), {'jobs': []})
                excluded = {item['path'] for item in proposal['skipped'] if '重叠' in item['reason']}
                if scenario == 'overlap':
                    self.assertTrue({file['path'] for file in groups[0]['files']} <= excluded)
                else:
                    self.assertEqual(excluded, {item['path'] for item in document['files']})
                shutil.rmtree(self.output)

    def test_entire_movie_work_over_limit_is_reported_without_splitting(self):
        for number in range(201):
            self.file('Film.CD%03d.mp4' % (number+1))
        _, _, _, ops = self.scan()
        preview = ops.quick_preview(str(self.destination))
        self.assertFalse(preview['items'])
        self.assertIsNone(preview['token'])
        self.assertEqual(len(preview['skipped']), 201)
        self.assertTrue(all('超过 200' in item['reason'] for item in preview['skipped']))


if __name__ == '__main__':
    unittest.main()
