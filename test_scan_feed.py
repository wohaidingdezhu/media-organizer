"""Progressive browsing uses generated records, bounded batches and operation doubles."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from PIL import Image
import library_server
import media_gui
import media_scan
from media_actions import MediaActions, media_id
from scan_feed import ScanFeed, ScanFeedReader
from unified_library import build_library, with_pending
import test_unified_library
import test_media_center
from test_library_server import request


class ScanFeedTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    record = test_unified_library.UnifiedLibraryTests.record
    report = test_unified_library.UnifiedLibraryTests.report
    server = test_unified_library.UnifiedLibraryTests.server

    def feed(self):
        directory = self.output / 'scan-live'
        directory.mkdir()
        return ScanFeed(self.output, directory, [self.a], True, True)

    def raw(self, name='photo.jpg'):
        record = self.record(self.a, name)
        record['_signature'] = tuple(record.pop('source_signature'))
        return record

    def test_batches_publish_before_traversal_finishes_and_do_not_rewrite_inventory(self):
        feed, reader = self.feed(), ScanFeedReader(self.output)
        first = self.raw()
        clock = mock.Mock(return_value=10)
        with mock.patch('scan_feed.time.monotonic', clock):
            feed.record(first)
            snapshot = reader.snapshot()
            self.assertEqual(len(snapshot['files']), 1)
            self.assertTrue(snapshot['files'][0]['browse_only'])
            for n in range(1000):
                feed.record({**first, 'path': str(self.a / ('generated-%d.jpg' % n))})
            feed.flush(True)
        manifest = json.loads((self.output / 'scan-pending.json').read_text(encoding='utf-8'))
        self.assertLessEqual(manifest['chunks'], 7)
        self.assertNotIn('files', manifest)
        sizes = [len(json.loads(path.read_text(encoding='utf-8'))['files']) for path in feed.directory.glob('progress-*.json')]
        self.assertEqual(sum(sizes), 1001)
        self.assertLessEqual(max(sizes), 200)
        final = reader.snapshot()
        self.assertEqual(len(final['files']), 1001)
        with mock.patch('scan_feed.fs.read_private_file', side_effect=AssertionError('Do not reread chunks')):
            # The small manifest uses workspace_data, not the chunk reader.
            with mock.patch('scan_feed.read_json', return_value=manifest):
                self.assertIs(reader.snapshot(), final)
        self.assertFalse(list(self.output.glob('library-*')))

    def test_scan_emits_during_discovery_not_only_at_its_end(self):
        for n in range(3):
            self.raw('%d.jpg' % n)
        reader = ScanFeedReader(self.output)
        walked = []
        def walk(*args, **kwargs):
            yield str(self.a), [], ['0.jpg']
            walked.extend(reader.snapshot()['files'])
            raise KeyboardInterrupt
        with mock.patch.dict('os.environ', {'MEDIA_ORGANIZER_PROGRESS': '1'}), mock.patch.object(media_scan.os, 'walk', walk), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(media_scan.main([str(self.a), '--output', str(self.output)]), 130)
        self.assertEqual([file['name'] for file in walked], ['0.jpg'])
        self.assertEqual(build_library(self.output)[1]['files'], [])

    def test_command_line_cancel_before_discovery_leaves_no_report_directory(self):
        generated = self.raw()
        original = Path(generated['path']).read_bytes()
        with mock.patch.dict('os.environ', {'MEDIA_ORGANIZER_PROGRESS': '0'}), mock.patch.object(media_scan, 'discover', side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(media_scan.main([str(self.a), '--output', str(self.output)]), 130)
        self.assertFalse(list(self.output.glob('scan-*')))
        self.assertEqual(Path(generated['path']).read_bytes(), original)

    def test_overlay_shows_metadata_art_and_retains_completed_authority_only(self):
        old = self.record(self.b)
        self.report(1, [self.b], [old])
        _, base = build_library(self.output)
        feed, reader = self.feed(), ScanFeedReader(self.output)
        photo, video = self.raw(), self.raw('clip.mp4')
        video.update(kind='视频', extension='mp4', video={'duration_seconds': 21, 'width': 640, 'height': 360})
        photo['image'] = {'width': 12, 'height': 16, 'date_original': '2026:11:04 12:00:00'}
        for file in (photo, video):
            feed.record(file)
        feed.preview(photo['path'], 'previews/image-00001.png')
        feed.group({'tag_key': 'a' * 64, 'files': [{'path': video['path']}], 'poster': 'covers/frame-00001.png'})
        feed.flush(True)
        doc = with_pending(base, reader.snapshot())
        by_path = {file['path']: file for file in doc['files']}
        self.assertEqual(by_path[video['path']]['video']['duration_seconds'], 21)
        self.assertEqual(by_path[photo['path']]['image']['width'], 12)
        self.assertTrue(doc['previews'][photo['path']].startswith('previews/live-'))
        self.assertTrue(doc['video_library']['groups'][0]['poster'].startswith('previews/live-'))
        self.assertEqual(set(MediaActions(doc).records), {media_id(old['path'])})
        self.assertEqual(len(base['files']), 1)

    def test_reader_retries_missing_batch_and_rejects_unsafe_chunks(self):
        feed, reader = self.feed(), ScanFeedReader(self.output)
        feed.record(self.raw())
        manifest = self.output / 'scan-pending.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['chunks'] = 2
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(FileNotFoundError):
            reader.snapshot()
        (feed.directory / 'progress-00000002.json').write_text(json.dumps({'files': [], 'groups': {}, 'previews': {}}), encoding='utf-8')
        self.assertEqual(len(reader.snapshot()['files']), 1)
        for report in ('../outside', 'scan-x/../outside', 'library-abc'):
            manifest.write_text(json.dumps({**data, 'report': report}), encoding='utf-8')
            with self.assertRaises(ValueError):
                reader.snapshot()
        manifest.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            reader.snapshot()

    def test_photos_video_info_and_covers_publish_before_report_export(self):
        for name in ('one.jpg', 'two.jpg', 'three.jpg', 'clip.mp4'):
            self.raw(name)
        reader, observed = ScanFeedReader(self.output), []
        class Worker:
            def __init__(self, helper):
                pass
            def request(self, path, thumbnail=None, **kwargs):
                if thumbnail:
                    Image.new('RGB', (20, 16), 'blue').save(thumbnail)
                    return {}
                return {'width': 20, 'height': 16, 'duration_seconds': 21} if path.endswith('.mp4') else {'width': 20, 'height': 16}
            def close(self):
                pass
        original = media_scan.write_reports
        def export(directory, document):
            partial = reader.snapshot()
            observed.append(partial)
            self.assertTrue(any(file.get('image', {}).get('width') == 20 for file in partial['files']))
            self.assertTrue(any(file.get('video', {}).get('duration_seconds') == 21 for file in partial['files']))
            self.assertEqual(len(partial['previews']), 3)
            self.assertTrue(partial['groups'][0]['poster'])
            self.assertFalse((directory / 'report.json').exists())
            return original(directory, document)
        with mock.patch.dict('os.environ', {'MEDIA_ORGANIZER_PROGRESS': '1'}), mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker', Worker), mock.patch.object(media_scan, 'write_reports', side_effect=export), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media_scan.main([str(self.a), '--output', str(self.output)]), 0)
        self.assertEqual(len(observed), 1)

    def test_finish_and_cancel_keep_scope_and_never_promote_pending_actions(self):
        old = self.record(self.b)
        self.report(1, [self.b], [old])
        _, base = build_library(self.output)
        feed, reader = self.feed(), ScanFeedReader(self.output)
        new = self.raw()
        feed.record(new)
        pending = reader.snapshot()
        staged = with_pending(base, pending)
        finished = with_pending(base, {**pending, 'finished': True})
        self.assertFalse(finished['pending_scan'])
        self.assertTrue(finished['scan_complete'])
        self.assertEqual(len(finished['files']), 2)
        self.assertEqual(set(MediaActions(finished).records), {media_id(old['path'])})
        reverted = with_pending(base, None)
        self.assertEqual([file['path'] for file in reverted['files']], [old['path']])
        self.assertEqual(len(staged['files']), 2)
        # A freshly opened completed view must not reapply older pending facts.
        completed = {**base, 'source_reports': ['scan-live']}
        self.assertEqual(with_pending(completed, {**pending, 'finished': True})['scan_revision'], '')

    def test_new_scan_cannot_reuse_the_previous_finished_temporary_inventory(self):
        feed, reader = self.feed(), ScanFeedReader(self.output)
        feed.record(self.raw())
        reader.snapshot()
        state = media_gui.DashboardState(self.output)
        self.addCleanup(state.close)
        state.folders = [str(self.a)]
        state._scan_feed_reader = reader
        state.progress = {'stage': 'done'}
        self.assertTrue(state.pending_library()['finished'])
        with mock.patch.object(media_gui.threading, 'Thread'):
            state.start_scan({'image_analysis': False, 'video_covers': False, 'video_rule': 'auto'})
        self.assertIsNone(state.pending_library())
        state.running = False
        state.progress = {'stage': 'done'}
        self.assertIsNone(state.pending_library())  # Even an empty successful scan has no old batches.

    def test_finishing_scan_keeps_visible_batch_without_loading_unseen_batches(self):
        feed, reader = self.feed(), ScanFeedReader(self.output)
        first, second = self.raw(), self.raw('next.jpg')
        feed.record(first)
        visible = reader.snapshot()
        feed.record(second)
        feed.flush(True)
        self.report(2, [self.a], [
            {**file, 'source_signature': list(file['_signature'])} for file in (first, second)
        ])
        state = media_gui.DashboardState(self.output)
        self.addCleanup(state.close)
        state.running = True
        state._scan_feed_reader = reader
        process = mock.Mock(stdout=io.StringIO())
        process.wait.return_value = 0
        with mock.patch.object(media_gui, 'compile_helpers'), mock.patch.object(media_gui.subprocess, 'Popen', return_value=process), mock.patch.object(reader, 'snapshot', side_effect=AssertionError('No catch-up read at scan completion')):
            state._run_scan(['media_scan.py'], False, False)
        finished = state.pending_library()
        self.assertTrue(finished['finished'])
        self.assertIs(finished['files'], visible['files'])
        self.assertFalse(state.running)
        self.assertEqual(state.snapshot()['latest_report'], 'scan-002')
        self.assertEqual(len(build_library(self.output)[1]['files']), 2)

    def test_changed_movie_does_not_reuse_old_art_and_unchanged_facts_stay_visible(self):
        record = self.record(self.a, 'Feature2025.mp4')
        record.update(kind='视频', extension='mp4', image={'width': 20}, suggested_path='known/path')
        group = {'tag_key': 'a' * 64, 'group_key': 'a' * 64, 'files': [{'path': record['path']}],
                 'poster': 'covers/old.png', 'metadata': {'title': 'Known movie'}}
        self.report(1, [self.a], [record], video_library={'groups': [group]})
        _, base = build_library(self.output)
        unchanged = {**record, 'sha256': '', 'suggested_path': '', 'browse_only': True}
        known = with_pending(base, {'roots': [str(self.a)], 'files': [unchanged]})
        self.assertEqual(known['files'][0]['image'], record['image'])
        self.assertEqual(known['files'][0]['suggested_path'], 'known/path')
        self.assertEqual(known['files'][0]['sha256'], record['sha256'])
        self.assertFalse(known['files'][0]['browse_only'])
        changed = with_pending(base, {'roots': [str(self.a)], 'files': [{**unchanged, 'source_signature': [9] * 5}],
                                      'groups': [{**group, 'poster': '', 'metadata': {}}]})
        self.assertEqual(changed['video_library']['groups'][0]['poster'], '')
        self.assertEqual(changed['video_library']['groups'][0]['metadata'], {})
        self.assertEqual(MediaActions(changed).records, {})

    def test_same_server_updates_browsing_serves_only_published_png_and_keeps_actions_frozen(self):
        old = self.record(self.b)
        self.report(1, [self.b], [old])
        directory, _ = build_library(self.output)
        feed, reader = self.feed(), ScanFeedReader(self.output)
        fresh = self.raw()
        feed.record(fresh)
        from test_library_server import MemoryServer
        from urllib.parse import urlsplit
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(directory, self.output, pending_provider=reader.snapshot)
        self.addCleanup(server.missing_monitor.stop)
        prefix = urlsplit(url).path.removesuffix('library.html')
        _, body = request(server, prefix + 'api/center')
        first = json.loads(body)
        self.assertEqual(len(first['items']), 2)
        self.assertNotIn(fresh['path'], first['media_access']['ids_by_path'])
        (feed.directory / 'previews').mkdir()
        image = feed.directory / 'previews' / 'generated.png'
        Image.new('RGB', (20, 16), 'blue').save(image)
        feed.preview(fresh['path'], 'previews/generated.png')
        fresh['image'] = {'width': 20, 'height': 16}
        feed.record(fresh)
        feed.flush(True)
        from portable_fs import read_private_file
        with mock.patch('scan_feed.fs.read_private_file', wraps=read_private_file) as reads:
            _, status = request(server, prefix + 'api/scan')
        self.assertEqual([call.args[1] for call in reads.call_args_list], [('scan-pending.json',)])
        self.assertNotEqual(json.loads(status)['revision'], first['scan_revision'])
        _, body = request(server, prefix + 'api/center')
        second = json.loads(body)
        item = next(item for item in second['items'] if item['id'] == media_id(fresh['path']))
        self.assertEqual(item['metadata']['width'], 20)
        header, png = request(server, prefix + item['preview'])
        self.assertIn('200 OK', header)
        self.assertEqual(png, image.read_bytes())
        image.write_bytes(b'corrupt generated image')
        self.assertIn('404', request(server, prefix + item['preview'])[0])
        self.assertIn('404', request(server, prefix + 'previews/live-' + '0' * 64 + '.png')[0])
        for action in ('open', 'reveal'):
            with mock.patch('system_integration.open_media', create=True) as opened:
                header, _ = request(server, prefix + 'api/media/action', 'POST', {'action': action, 'id': media_id(fresh['path'])}, origin='http://127.0.0.1:43210')
                self.assertIn('400', header)
                opened.assert_not_called()


class ScanFeedUITests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_old_scan_status_cannot_reload_a_newer_manual_refresh(self):
        self.run_ui(r'''
const pending=poll();await new Promise(resolve=>setImmediate(resolve));
revision='batch-2';await $('center-refresh').onclick();assert.equal(readCount,2);
releaseStatus({revision:'batch-1'});await pending;
assert.equal(readCount,2);assert(!$('center-status').textContent.includes('扫描有新结果'));
''', setup=r'''
const intervals=[];scope.setInterval=(fn,ms)=>intervals.push({fn,ms});const poll=()=>intervals.find(i=>i.ms===3000).fn();
let revision='batch-1',readCount=0,releaseStatus;const original=scope.fetch;
scope.fetch=async(path,options)=>{if(path==='api/scan')return {ok:true,json:()=>new Promise(resolve=>releaseStatus=resolve)};const result=await original(path,options);if(path!=='api/center')return result;readCount++;return {...result,json:async()=>({...await result.json(),unified_library:true,scan_revision:revision})};};
''')

    def test_new_batches_defer_for_selection_preview_and_drafts_then_update_same_page(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();
const wall=$('center-content').children.find(n=>n.className.includes('photo-wall'));const reads=()=>readCount;
revision='batch-2';items.push({...photo,id:'9'.repeat(64),title:'New photo'});await poll();assert.equal(reads(),1);assert($('selected-count').textContent.includes('1 项'));assert($('center-status').textContent.includes('扫描有新结果'));assert.equal($('center-content').children.find(n=>n.className.includes('photo-wall')),wall);
const notice=$('center-status');let noticeWrites=0,text=notice.textContent;Object.defineProperty(notice,'textContent',{get:()=>text,set:value=>{text=value;noticeWrites++;}});await poll();assert.equal(noticeWrites,0);
$('clear-selection').onclick();$('manage-toggle').onclick();$('query').value='Photo';scope.document.activeElement={tagName:'INPUT'};await poll();assert.equal(reads(),1);assert.equal($('query').value,'Photo');
scope.document.activeElement=null;$('media-detail').open=true;await poll();assert.equal(reads(),1);$('media-detail').open=false;
await poll();assert.equal(reads(),2);assert.equal($('query').value,'Photo');assert.equal(scope.window.scrollY,350);assert(!$('center-status').textContent.includes('扫描有新结果'));
await poll();assert.equal(reads(),2);assert.equal(operations.length,0);
''', setup=r'''
const intervals=[];scope.setInterval=(fn,ms)=>intervals.push({fn,ms});const poll=()=>intervals.find(i=>i.ms===3000).fn();let revision='batch-1',readCount=0;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/scan')return {ok:true,json:async()=>({revision})};const result=await original(path,options);if(path!=='api/center')return result;readCount++;return {...result,json:async()=>({...await result.json(),unified_library:true,scan_revision:revision,pending_scan:true})};};
''')

    def test_slow_update_rechecks_interaction_and_failed_update_keeps_old_data(self):
        self.run_ui(r'''
let release;hold=true;const checking=poll();await new Promise(r=>setImmediate(r));$('manage-toggle').onclick();releaseCenter();await checking;assert($('center-status').textContent.includes('扫描有新结果'));assert.equal($('manage-toggle').getAttribute('aria-pressed'),'true');
$('manage-toggle').onclick();hold=false;failCenter=true;await poll();assert($('center-content').children.length);failCenter=false;await poll();assert(!$('center-status').textContent.includes('扫描有新结果'));assert.equal(operations.length,0);
''', setup=r'''
const intervals=[];scope.setInterval=(fn,ms)=>intervals.push({fn,ms});const poll=()=>intervals.find(i=>i.ms===3000).fn();let hold=false,releaseCenter;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/scan')return {ok:true,json:async()=>({revision:'next'})};if(path==='api/center'&&hold)await new Promise(r=>releaseCenter=r);const result=await original(path,options);if(path!=='api/center')return result;return {...result,json:async()=>({...await result.json(),unified_library:true,scan_revision:hold?'next':'first'})};};
''')


if __name__ == '__main__':
    unittest.main()
