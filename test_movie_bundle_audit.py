"""Edition reviews are read-only and use generated media on both platforms."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server
import movie_grouping
from media_actions import MediaActions, media_id
from movie_bundle import movie_bundle_audit
from organization_plan import STATE_FILE
from test_library_server import MemoryServer, request
import test_movie_upgrade as movie_fixture
import test_photo_bundle_audit as photo_fixture


class MovieBundleAuditTests(unittest.TestCase):
    setUp = movie_fixture.MovieUpgradeTests.setUp
    generated = movie_fixture.MovieUpgradeTests.generated
    run_scan = movie_fixture.MovieUpgradeTests.run_scan
    operations = movie_fixture.MovieUpgradeTests.operations
    bundle_sample = movie_fixture.MovieUpgradeTests.bundle_sample
    node = photo_fixture.PhotoBundleAuditTests.node
    ui_fixture = photo_fixture.PhotoBundleAuditTests.ui_fixture

    def test_audit_matches_copy_contents_and_preserves_sources_and_plan(self):
        movies, _, report, document, ops, plan = self.bundle_sample()
        before = {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                  for path in self.source.rglob('*') if path.is_file()}
        state = (report / STATE_FILE).read_bytes()
        key = media_id(str(movies[0]))
        result = movie_bundle_audit(document, key, plan.snapshot()['items'])
        preview = ops.preview('copy', [key], str(self.destination), bundle=True)
        self.assertEqual({item['path'] for item in result['items']}, {item['path'] for item in preview['items']})
        self.assertEqual(set(result['media_ids']), {media_id(str(path)) for path in movies})
        self.assertEqual(result['counts'], {'视频': 2, '字幕': 1, 'NFO': 1, '封面': 1, '剧照': 1})
        self.assertEqual([Path(item['path']).name for item in result['skipped']], ['orphan.srt'])
        self.assertEqual(result['identity_missing'], 0)
        self.assertEqual((report / STATE_FILE).read_bytes(), state)
        self.assertEqual(before, {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                                for path in self.source.rglob('*') if path.is_file()})
        self.assertFalse(list(self.destination.iterdir()))

    def test_offline_snapshot_and_missing_video_identity_remain_reviewable(self):
        movies, _, _, document, _, plan = self.bundle_sample()
        document['restored_snapshot'] = True
        next(item for item in document['files'] if item['path'] == str(movies[0])).pop('source_signature')
        for path in self.source.rglob('*'):
            if path.is_file():
                path.unlink()
        with mock.patch.object(MediaActions, 'validate', side_effect=AssertionError('review must not read originals')):
            result = movie_bundle_audit(document, media_id(str(movies[0])), plan.snapshot()['items'])
        self.assertTrue(result['restored_snapshot'])
        self.assertEqual(result['identity_missing'], 1)
        self.assertEqual(len(result['items']), 6)
        self.assertIn('能否播放', result['message'])

    def test_review_keeps_ambiguous_shared_art_out_and_only_selects_current_edition(self):
        first = self.generated('ABC-123.1080p.mp4')
        self.generated('ABC-123.2160p.mp4', b'other edition')
        self.generated('ABC-123.srt')
        self.generated('poster.png')
        _, document = self.run_scan()
        result = movie_bundle_audit(document, media_id(str(first)))
        self.assertEqual(result['media_ids'], [media_id(str(first))])
        self.assertEqual({item['relative'] for item in result['items']}, {first.name})
        self.assertEqual({Path(item['path']).name for item in result['skipped']}, {'ABC-123.srt', 'poster.png'})
        self.assertIn('字幕', result['missing'])

    def test_invalid_photo_or_nonunique_group_never_selects_another_work(self):
        movies, _, _, document, _, _ = self.bundle_sample()
        for key in (None, [], '../media', 'f' * 64, media_id(str(self.source / 'poster.png'))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                movie_bundle_audit(document, key)
        for groups in ([], document['video_library']['groups'] * 2):
            changed = copy.deepcopy(document)
            changed['video_library']['groups'] = groups
            with self.assertRaisesRegex(ValueError, '唯一影片分组'):
                movie_bundle_audit(changed, media_id(str(movies[0])))

    def test_windows_unc_and_posix_report_names_are_preserved(self):
        for folder, sep in (('C:\\samples', '\\'), ('/samples', '/'), ('\\\\nas\\samples', '\\')):
            path = folder + sep + 'ABC-123.MP4'
            record = {'path': path, 'kind': '视频', 'bytes': 12}
            document = {'files': [record], 'video_library': {'groups': [{'title': 'ABC-123', 'files': [record]}]}}
            result = movie_bundle_audit(document, media_id(path))
            self.assertEqual(result['items'][0]['relative'], 'ABC-123.MP4')
            self.assertEqual(result['items'][0]['bytes'], 12)
            self.assertEqual(result['identity_missing'], 1)

    def test_legacy_missing_library_and_invalid_group_members_have_actionable_errors(self):
        movies, _, _, document, _, _ = self.bundle_sample()
        key = media_id(str(movies[0]))
        for library in (None, {}, {'groups': []}):
            changed = {**document, 'video_library': library}
            with self.subTest(library=library), self.assertRaisesRegex(ValueError, '重新扫描'):
                movie_bundle_audit(changed, key)
        for path in (str(self.source / 'poster.png'), str(movies[0]), str(self.source / 'unknown.mp4')):
            changed = copy.deepcopy(document)
            changed['video_library']['groups'][0]['files'].append({'path': path})
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, '清单无效'):
                movie_bundle_audit(changed, key)

    def test_http_review_uses_current_grouping_and_plan_then_works_offline(self):
        movies, _, report, document, _, plan = self.bundle_sample()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        route = prefix + 'api/movie-bundles/' + media_id(str(movies[0]))
        headers, body = request(server, route)
        self.assertIn('200 OK', headers)
        initial = json.loads(body)
        group = document['video_library']['groups'][0]
        movie_grouping.update_assignments(self.output, document, [{'id': media_id(str(movies[1])),
            'work_key': group['work_key'], 'title': group['title'], 'edition': '另一版本', 'part': 2}])
        plan.set_states([media_id(str(movies[0]))], 'hold')
        for path in self.source.rglob('*'):
            if path.is_file():
                path.unlink()
        before = (report / STATE_FILE).read_bytes()
        with mock.patch.object(MediaActions, 'validate', side_effect=AssertionError('HTTP must not check originals')):
            headers, body = request(server, route)
        result = json.loads(body)
        self.assertIn('200 OK', headers)
        self.assertNotEqual(result['grouping_revision'], initial['grouping_revision'])
        self.assertEqual(len(result['media_ids']), 1)
        self.assertEqual(result['items'][0]['plan']['state'], 'hold')
        self.assertEqual((report / STATE_FILE).read_bytes(), before)
        headers, _ = request(server, prefix + 'api/movie-bundles/unknown')
        self.assertIn('400', headers)
        _, body = request(server, prefix + 'organize.html')
        self.assertIn('影片整组核对'.encode(), body)

    def movie_ui(self):
        return self.ui_fixture() + r"""
items[0].kind=items[1].kind='视频';items[2].kind='照片';
Object.assign(audit,{media_kind:'视频',media_ids:['jpg','raw'],title:'样例影片',edition:'1080p',group_key:'edition',grouping_revision:'initial',missing:['NFO']});
"""

    def test_shared_interface_selects_only_edition_videos_and_never_saves_or_copies(self):
        self.node(self.movie_ui() + r"""
(async()=>{const card=new Element();scope.appendBundleReviewButton(items[0],card);await card.children[0].onclick();if(!apiCalls[0].includes('api/movie-bundles/'))throw Error('wrong endpoint');if(!JSON.stringify($('bundle-review-items').children).includes('下列附件分类计划仅用于独立处理'))throw Error('lost attachment target distinction');if(!$('bundle-review-title').textContent.includes('1080p')||!JSON.stringify($('bundle-review-items').children).includes('缺少不表示影片损坏'))throw Error('lost edition or missing warning');await $('bundle-review-select').onclick();if([...scope.selected].join(',')!=='jpg,raw'||items[0].state!=='include'||items[1].state!=='pending'||planReads!==1||apiCalls.length!==2)throw Error('wrong selection or saved plan');if($('kind').value!=='视频'||$('view-mode').value!=='list'||$('risk').value!=='selected'||$('bundle-review-dialog').open)throw Error('edition not visible');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_group_revision_members_or_edition_change_keeps_old_selection(self):
        self.node(self.movie_ui() + r"""
(async()=>{for(const change of [{grouping_revision:'changed'},{media_ids:['jpg']},{group_key:'other'}]){scope.api=async()=>audit;await scope.openBundleReview(items[0]);scope.api=async()=>({...audit,...change});await $('bundle-review-select').onclick();if([...scope.selected].join(',')!=='mov'||scope.saving||$('bundle-review-error').hidden||!$('bundle-review-error').textContent.includes('分组已变化'))throw Error('stale review selected media');}if(planReads)throw Error('changed grouping read plan');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_cancel_or_failure_during_movie_recheck_preserves_selection_and_releases_controls(self):
        self.node(self.movie_ui() + r"""
(async()=>{await scope.openBundleReview(items[0]);scope.api=async()=>{throw Error('unavailable');};await $('bundle-review-select').onclick();if([...scope.selected].join(',')!=='mov'||scope.saving||$('bundle-review-error').hidden)throw Error('failed recheck lost selection');scope.api=async()=>audit;await scope.openBundleReview(items[0]);let resolve;scope.api=()=>new Promise(done=>resolve=done);const selection=$('bundle-review-select').onclick();$('bundle-review-close').onclick();resolve(audit);await selection;if([...scope.selected].join(',')!=='mov'||scope.saving||scope.bundleReviewInfo||planReads)throw Error('cancelled recheck selected media');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_snapshot_cleaned_video_missing_mapping_busy_or_over_limit_disable_group_selection(self):
        self.node(self.movie_ui() + r"""
(async()=>{await scope.openBundleReview(items[0]);for(const mode of ['snapshot','trashed','busy','unknown','large']){audit.restored_snapshot=mode==='snapshot';scope.handledTrash=new Set(mode==='trashed'?[items[0].path]:[]);scope.operating=mode==='busy';if(mode==='unknown')delete scope.mediaIds[items[1].path];if(mode==='large'){audit.media_ids=Array.from({length:201},(_,n)=>String(n));scope.data.items=audit.media_ids.map(id=>({id,kind:'视频',path:id}));scope.mediaIds=Object.fromEntries(audit.media_ids.map(id=>[id,id]));}scope.syncBundleReviewControls();if(!$('bundle-review-select').disabled)throw Error('unsafe selection '+mode);}})().catch(e=>{console.error(e);process.exitCode=1;});
""")
