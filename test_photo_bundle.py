"""Photo bundles use generated media and the shared Mac/Windows copy engine."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

import media_scan
import library_server
from file_operations import FileOperations
from media_actions import MediaActions, media_id
from organization_plan import OrganizationPlan
from photo_bundle import photo_manifests
from test_library_server import MemoryServer, request
from test_support import make_symlink


class PhotoBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source, self.output, self.destination = [self.base / name for name in ('source', 'reports', 'copies')]
        self.source.mkdir()
        self.destination.mkdir()

    def file(self, name):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('generated ' + name).encode('utf-8'))
        return path

    def scan(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media_scan.main([str(self.source), '--output', str(self.output), '--no-image-metadata']), 0)
        report = max(self.output.glob('scan-*'))
        document = json.loads((report / 'report.json').read_text(encoding='utf-8'))
        plan = OrganizationPlan(report, document)
        ops = FileOperations(report, MediaActions(document), lambda: plan, document=lambda: document)
        return report, document, plan, ops

    def prepare(self, names=('IMG.JPG', 'IMG.DNG', 'IMG.MOV', 'IMG.xmp', 'IMG.DNG.xmp', 'IMG.AAE')):
        paths = [self.file(name) for name in names]
        report, document, plan, ops = self.scan()
        key = media_id(str(paths[0]))
        plan.set_target(key, '用户/样例用户/照片/2026/10/' + paths[0].name)
        plan.set_states([key], 'include')
        return paths, key, report, document, plan, ops

    def preview(self, ops, key):
        return ops.preview('copy', [key], str(self.destination), photo_bundle=True)

    def wait(self, ops, job):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = ops.snapshot(job['id'])
            if result['status'] != 'running':
                return result
            time.sleep(.01)
        self.fail('generated photo copy did not finish')

    def test_copy_expands_unique_pair_and_editing_files_with_original_names_and_full_hashes(self):
        paths, key, report, document, plan, ops = self.prepare()
        originals = {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
        preview = self.preview(ops, key)
        self.assertEqual(preview['bundle_kind'], 'photo')
        self.assertEqual(len(preview['items']), 6)
        self.assertEqual(preview['bundles'][0]['counts'], {'照片': 1, 'RAW': 1, '配对视频候选': 1, 'XMP': 2, 'AAE': 1})
        self.assertFalse(list(self.destination.iterdir()))
        self.assertTrue(all(item['relative'] == '用户/样例用户/照片/2026/10/' + Path(item['path']).name for item in preview['items']))
        result = self.wait(ops, ops.start(preview['token']))
        self.assertEqual(result['status'], 'complete', result)
        persisted = FileOperations(report, MediaActions(document), lambda: plan, document=document).snapshot(result['id'])
        self.assertEqual(persisted['bundle_kind'], 'photo')
        self.assertEqual({item['role'] for item in persisted['planned_items']}, {'照片', 'RAW', '配对视频候选', 'XMP', 'AAE'})
        for item in preview['items']:
            self.assertEqual(hashlib.sha256(Path(item['target']).read_bytes()).hexdigest(), originals[item['path']][0])
        self.assertEqual(originals, {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths})

    def test_ordinary_copy_only_selected_photo_and_trash_never_expands(self):
        _, key, _, _, _, ops = self.prepare()
        self.assertEqual(len(ops.preview('copy', [key], str(self.destination))['items']), 1)
        for options in ({'photo_bundle': True}, {'photo_bundle': True, 'bundle': True}, {'photo_bundle': 'true'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ops.preview('trash', [key], **options)
        with self.assertRaises(ValueError):
            ops.preview('copy', [key], str(self.destination), photo_bundle=True, bundle=True)

    def test_restored_snapshot_and_legacy_photo_identity_require_new_scan(self):
        _, key, _, document, _, ops = self.prepare()
        document['restored_snapshot'] = True
        with self.assertRaisesRegex(ValueError, '重新扫描'):
            self.preview(ops, key)
        document.pop('restored_snapshot')
        ops.media.records[key].pop('source_signature')
        with self.assertRaisesRegex(ValueError, '身份记录'):
            self.preview(ops, key)

    def test_stop_before_copy_preserves_all_sources_and_journal_tracks_every_companion(self):
        paths, key, report, document, plan, ops = self.prepare()
        preview = self.preview(ops, key)
        queued = []
        with mock.patch('file_operations.threading.Thread.start', autospec=True, side_effect=queued.append):
            job = ops.start(preview['token'])
        try:
            ops.request_stop(job['id'])
        finally:
            queued[0].run()
        result = ops.snapshot(job['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(result['planned_items']), 6)
        self.assertFalse(list(self.destination.iterdir()))
        self.assertTrue(all(path.exists() for path in paths))
        with self.assertRaisesRegex(ValueError, '逐项记录'):
            ops.remaining(job['id'])
        journal = report / 'operations' / (job['id'] + '.json')
        result['bundle_kind'] = ['invalid marker']
        journal.write_text(json.dumps(result), encoding='utf-8')
        reopened = FileOperations(report, MediaActions(document), lambda: plan, document=document)
        with self.assertRaisesRegex(ValueError, '操作记录'):
            reopened.snapshot(job['id'])

    def test_ambiguous_photo_video_and_sidecar_candidates_are_explicitly_skipped(self):
        _, key, _, _, _, ops = self.prepare(('IMG.jpg', 'IMG.jpeg', 'IMG.dng', 'IMG.mov', 'IMG.mp4', 'IMG.xmp', 'IMG.jpg.xmp'))
        preview = self.preview(ops, key)
        self.assertEqual({Path(item['path']).name for item in preview['items']}, {'IMG.jpg', 'IMG.jpg.xmp'})
        self.assertEqual({Path(item['path']).name for item in preview['skipped']}, {'IMG.jpeg', 'IMG.dng', 'IMG.mov', 'IMG.mp4', 'IMG.xmp'})
        self.assertTrue(all(item['reason'] for item in preview['skipped']))

    def test_single_heic_pair_is_filename_candidate_and_other_folder_is_never_included(self):
        paths, key, _, _, _, ops = self.prepare(('IMG.heic', 'IMG.MOV', 'IMG.aae', 'other/IMG.xmp', 'other/IMG.jpg'))
        preview = self.preview(ops, key)
        self.assertEqual({item['path'] for item in preview['items']}, {str(path) for path in paths[:3]})
        self.assertEqual(next(item['role'] for item in preview['items'] if item['path'] == str(paths[1])), '配对视频候选')

    def test_explicitly_selected_ambiguous_photos_are_not_mislabeled_as_skipped(self):
        paths, key, _, _, plan, ops = self.prepare(('IMG.jpg', 'IMG.jpeg', 'IMG.mov', 'IMG.xmp'))
        other = media_id(str(paths[1]))
        plan.set_target(other, '用户/样例用户/照片/2026/10/IMG.jpeg')
        plan.set_states([other], 'include')
        preview = ops.preview('copy', [key, other], str(self.destination), photo_bundle=True)
        self.assertEqual({item['path'] for item in preview['items']}, {str(path) for path in paths[:2]})
        self.assertEqual({item['path'] for item in preview['skipped']}, {str(path) for path in paths[2:]})

    def test_renamed_photo_requires_restoring_pair_names(self):
        _, key, _, _, plan, ops = self.prepare()
        plan.set_target(key, '照片/renamed.JPG')
        plan.set_states([key], 'include')
        with self.assertRaisesRegex(ValueError, '保留原文件名'):
            self.preview(ops, key)

    def test_two_selected_pair_members_require_same_folder_and_are_deduplicated(self):
        paths, key, _, _, plan, ops = self.prepare()
        raw = media_id(str(paths[1]))
        plan.set_states([raw], 'include')
        with self.assertRaisesRegex(ValueError, '同一分类目录'):
            ops.preview('copy', [key, raw], str(self.destination), photo_bundle=True)
        plan.set_target(raw, '用户/样例用户/照片/2026/10/IMG.DNG')
        plan.set_states([raw], 'include')
        preview = ops.preview('copy', [key, raw], str(self.destination), photo_bundle=True)
        self.assertEqual(len(preview['items']), 6)
        self.assertEqual(len(preview['bundles']), 1)

    def test_held_or_separately_classified_companion_is_not_silently_copied(self):
        paths, key, _, _, plan, ops = self.prepare()
        for path in paths[1:3]:
            companion = media_id(str(path))
            plan.set_states([companion], 'hold')
            with self.assertRaisesRegex(ValueError, '已暂缓'):
                self.preview(ops, key)
            plan.set_states([companion], 'pending')
            plan.set_target(companion, '用户/另一用户/' + path.name)
            with self.assertRaisesRegex(ValueError, '另一分类位置'):
                self.preview(ops, key)
            plan.set_target(companion, None)

    def test_all_expanded_files_require_fresh_identity_and_changed_attachment_blocks_confirmation(self):
        paths, key, _, document, _, ops = self.prepare()
        preview = self.preview(ops, key)
        paths[-1].write_bytes(b'changed generated attachment')
        with self.assertRaisesRegex(ValueError, '附件.*变化'):
            ops.start(preview['token'])
        self.assertFalse(list(self.destination.iterdir()))
        document['sidecars'][0].pop('source_signature')
        with self.assertRaises(ValueError):
            self.preview(ops, key)

    def test_confirmation_rechecks_plan_companion_state_and_manifest(self):
        paths, key, _, document, plan, ops = self.prepare()
        preview = self.preview(ops, key)
        plan.set_states([media_id(str(paths[1]))], 'hold')
        with self.assertRaisesRegex(ValueError, '已暂缓'):
            ops.start(preview['token'])
        plan.set_states([media_id(str(paths[1]))], 'pending')
        document['sidecars'].pop()
        with self.assertRaisesRegex(ValueError, '重新预览'):
            ops.start(preview['token'])
        self.assertFalse(list(self.destination.iterdir()))

    def test_existing_target_and_source_symlink_are_rejected(self):
        paths, key, _, _, _, ops = self.prepare()
        preview = self.preview(ops, key)
        target = Path(preview['items'][-1]['target'])
        target.parent.mkdir(parents=True)
        target.write_bytes(b'keep existing generated copy')
        with self.assertRaises(ValueError):
            ops.start(preview['token'])
        self.assertEqual(target.read_bytes(), b'keep existing generated copy')
        target.unlink()
        original = paths[-1]
        original.unlink()
        make_symlink(original, paths[0])
        # Windows rejects the reparse point while opening it (OSError); macOS
        # rejects the no-follow stat as a non-regular file (ValueError).
        with self.assertRaises((OSError, ValueError)):
            self.preview(ops, key)
        self.assertFalse(list(target.parent.iterdir()))

    def test_two_folders_with_same_names_cannot_overwrite_each_other(self):
        paths, key, _, _, plan, ops = self.prepare(('IMG.jpg', 'IMG.mov', 'other/IMG.heic', 'other/IMG.mov'))
        other = media_id(str(paths[2]))
        plan.set_target(other, '用户/样例用户/照片/2026/10/IMG.heic')
        plan.set_states([other], 'include')
        with self.assertRaises(ValueError):
            ops.preview('copy', [key, other], str(self.destination), photo_bundle=True)

    def test_cross_platform_report_pairing_and_unicode_equivalence(self):
        for folder in ('C:\\照片', '/generated/照片', '\\\\nas\\share\\照片'):
            separator = '\\' if '\\' in folder else '/'
            names = [('Café.JPG', '照片', 'jpg'), ('Cafe\u0301.DNG', '照片', 'dng'), ('CAFÉ.MOV', '视频', 'mov')]
            records = [{'path': folder + separator + name, 'kind': kind, 'extension': ext} for name, kind, ext in names]
            groups = photo_manifests({'files': records}, {records[0]['path']})
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0]['items']), 3)
            self.assertEqual({item['relative'] for item in groups[0]['items']}, {item[0] for item in names})

    def test_expansion_over_200_files_is_rejected(self):
        ids = []
        for number in range(41):
            for ext in ('jpg', 'dng', 'mov', 'xmp', 'aae'):
                path = self.file('IMG%02d.' % number + ext)
                if ext == 'jpg':
                    ids.append(media_id(str(path)))
        _, _, plan, ops = self.scan()
        plan.set_states(ids, 'include')
        with self.assertRaisesRegex(ValueError, '超过 200'):
            ops.preview('copy', ids, str(self.destination), photo_bundle=True)

    def test_http_preview_routes_photo_bundles_and_rejects_wrong_selection_and_flags(self):
        paths, key, report, _, _, _ = self.prepare()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        payload = {'mode': 'copy', 'ids': [key], 'destination': str(self.destination), 'photo_bundle': True}
        origin = 'http://127.0.0.1:43210'
        headers, body = request(server, prefix + 'api/operations/preview', 'POST', payload, origin=origin)
        self.assertIn('200 OK', headers)
        self.assertEqual(len(json.loads(body)['items']), 6)
        for changes in ({'ids': [media_id(str(paths[2]))]}, {'photo_bundle': 'true'}, {'bundle': True}, {'mode': 'trash'}):
            headers, _ = request(server, prefix + 'api/operations/preview', 'POST', {**payload, **changes}, origin=origin)
            self.assertIn('400', headers)
        headers, body = request(server, prefix + 'photos.html')
        self.assertIn('200', headers)
        self.assertIn('预览照片和配套文件整组复制'.encode(), body)

    def test_actual_shared_ui_routes_photo_button_shows_candidate_warning_and_requires_confirmation(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Mac and Windows CI install Node for shared UI checks')
        code = r'''
const fs=require('fs'),vm=require('vm');
class Element{constructor(tag,text){this.tag=tag;this.textContent=text||'';this.children=[];this.value='/generated/copies';this.checked=true;}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}showModal(){this.open=true;}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
let payload;
const scope={$,make:(tag,cls,text)=>new Element(tag,text),operating:false,saving:false,selected:new Set(['photo']),error(){},render(){},fileSize:n=>String(n),api:async(route,body)=>{payload=body;return {items:[{path:'/generated/IMG.MOV',target:'/generated/copies/IMG.MOV',role:'配对视频候选'}],bytes:1,bundle_kind:'photo',bundles:[{title:'IMG',counts:{'照片':1,'RAW':1,'配对视频候选':1,'XMP':1,'AAE':1}}],skipped:[{path:'/generated/IMG.jpeg',reason:'配对不明确'}]};}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(source.indexOf('let executionPage='),source.indexOf("$('execution-confirm').onchange")),scope);
(async()=>{await $('copy-photo-bundles').onclick();if(!payload.photo_bundle||payload.bundle||payload.mode!=='copy')throw Error('wrong photo operation');if(!$('execution').open||$('execution-confirm').checked||!$('execution-start').disabled)throw Error('confirmation required');const text=JSON.stringify($('execution-items').children);if(!text.includes('不能证明')&&!text.includes('人工核对'))throw Error('missing candidate warning');if(!text.includes('XMP 1')||!text.includes('配对不明确'))throw Error('missing counts or skipped reasons');if(!$('execution-title').textContent.includes('照片'))throw Error('wrong title');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', code, str(Path(__file__).parent / 'management.js')], text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
