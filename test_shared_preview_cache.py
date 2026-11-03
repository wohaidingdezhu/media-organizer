"""Generated thumbnails survive library revisions without re-decoding media."""
from pathlib import Path
import unittest
from unittest import mock

from PIL import Image
import media_actions
import media_scan
from media_actions import MediaActions, media_id
from preview_repair import PreviewRepair
import preview_repair
from unified_library import build_library
from test_library_server import request
from test_support import make_symlink
import test_unified_library
import test_media_center


class SharedPreviewCacheTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    record = test_unified_library.UnifiedLibraryTests.record
    report = test_unified_library.UnifiedLibraryTests.report
    server = test_unified_library.UnifiedLibraryTests.server

    def manager(self, directory, record):
        directory.mkdir()
        doc = {'unified_library': True, 'roots': [record['root']], 'files': [record]}
        return PreviewRepair(directory, MediaActions(doc), doc)

    @staticmethod
    def decode(path, thumbnail, timeout):
        Image.new('RGB', (40, 30), 'steelblue').save(thumbnail)

    def test_new_library_revision_reuses_photo_and_video_previews_and_serves_offline(self):
        previous_route = ''
        for number, kind in enumerate(('照片', '视频'), 1):
            record = self.record(self.a, 'sample%d.mp4' % number if kind == '视频' else 'sample%d.jpg' % number)
            record['kind'] = kind
            self.report(number, [self.a], [record])
            directory, doc = build_library(self.output)
            first = PreviewRepair(directory, MediaActions(doc), doc)
            identifier = media_id(record['path'])
            with mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker') as worker:
                worker.return_value.request.side_effect = self.decode
                first.generate(identifier)
                # A different complete scan creates a new unified snapshot.
                other = self.record(self.b, 'other%d.jpg' % number)
                self.report(number + 10, [self.b], [other])
                next_directory, next_doc = build_library(self.output)
                with mock.patch.object(media_actions, 'checked_stat', side_effect=AssertionError('cache discovery must not read originals')):
                    second = PreviewRepair(next_directory, MediaActions(next_doc), next_doc)
                self.assertIn(identifier, second.previews)
                self.assertIn('/cached-', second.previews[identifier])
                second.generate(identifier)
                self.assertFalse((next_directory / 'covers').exists(), 'cache reuse must not copy thumbnails into each snapshot')
                self.assertEqual(worker.return_value.request.call_count, 1)
            Path(record['path']).unlink()
            server, prefix = self.server(next_directory)
            headers, body = request(server, prefix + second.previews[identifier])
            self.assertIn('200 OK', headers)
            self.assertTrue(body.startswith(b'\x89PNG'))
            self.assertIn('404', request(server, prefix + 'covers/cached-' + 'f'*64 + '.png')[0])
            if previous_route:
                self.assertIn('404', request(server, prefix + previous_route)[0], 'cached media from another scope must stay inaccessible')
            previous_route = second.previews[identifier]
            with self.assertRaises(ValueError):
                second.generate(identifier)

    def test_identity_or_backend_change_and_restored_snapshot_cannot_reuse_old_cache(self):
        record = self.record(self.a)
        identifier = media_id(record['path'])
        with mock.patch.object(preview_repair, 'analysis_fingerprint', return_value='first'), mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker') as worker:
            worker.return_value.request.side_effect = self.decode
            self.manager(self.output / 'library-one', record).generate(identifier)
            changed = {**record, 'source_signature': [number + 1 for number in record['source_signature']]}
            self.assertNotIn(identifier, self.manager(self.output / 'library-changed', changed).previews)
            with mock.patch.object(preview_repair, 'analysis_fingerprint', return_value='new-backend'):
                self.assertNotIn(identifier, self.manager(self.output / 'library-backend', record).previews)
            stale = self.manager(self.output / 'library-stale', record)
            Path(record['path']).write_bytes(b'changed generated original')
            with self.assertRaises(ValueError):
                stale.generate(identifier)
            self.assertEqual(worker.return_value.request.call_count, 1)
            restored_doc = {'unified_library': True, 'restored_snapshot': True, 'roots': [str(self.a)], 'files': [record]}
            restored_dir = self.output / 'library-restored'
            restored_dir.mkdir()
            self.assertNotIn(identifier, PreviewRepair(restored_dir, MediaActions(restored_doc), restored_doc).previews)

    def test_corrupt_cache_regenerates_and_concurrent_existing_view_can_reuse_new_cache(self):
        record = self.record(self.a)
        identifier = media_id(record['path'])
        first = self.manager(self.output / 'library-one', record)
        second = self.manager(self.output / 'library-two', record)
        with mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker') as worker:
            worker.return_value.request.side_effect = self.decode
            first.generate(identifier)
            second.generate(identifier)
            self.assertEqual(worker.return_value.request.call_count, 1)
            cache = next((self.output / 'preview-cache').glob('*.png'))
            cache.write_bytes(b'generated corrupt cache')
            third = self.manager(self.output / 'library-three', record)
            self.assertNotIn(identifier, third.previews)
            third.generate(identifier)
            self.assertEqual(worker.return_value.request.call_count, 2)
            self.assertTrue(cache.read_bytes().startswith(b'\x89PNG'))

    def test_cache_symlink_failure_keeps_local_preview_and_reports_warning(self):
        record = self.record(self.a)
        target = self.base / 'outside-cache'
        target.mkdir()
        make_symlink(self.output / 'preview-cache', target)
        manager = self.manager(self.output / 'library-one', record)
        with mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker') as worker:
            worker.return_value.request.side_effect = self.decode
            result = manager.generate(media_id(record['path']))
        self.assertIn('warning', result)
        self.assertTrue((manager.report / result['preview']).is_file())
        self.assertEqual(list(target.iterdir()), [])

    def test_cache_warning_keeps_generated_image_visible_and_queue_completed(self):
        test_media_center.MediaCenterTests.run_ui(self, r'''
delete scope.IntersectionObserver;photo.preview='';items=[photo];const original=scope.fetch;scope.fetch=async(path,options)=>path==='api/media/cover'?{ok:true,json:async()=>({preview:'covers/generated.png',warning:'预览已生成，但复用缓存未保存：generated permission failure'})}:original(path,options);
location.hash='#photos';events.hashchange();await $('center-refresh').onclick();await new Promise(resolve=>setImmediate(resolve));const cover=$('center-content').children[0].children[0].children[0];assert.equal(cover.children[0].tagName,'IMG');assert.equal(cover.children[0].src,'covers/generated.png');assert.equal($('preview-progress').value,100);assert($('preview-progress-label').textContent.includes('缓存未保存'));assert.equal(operations.length,0);
''')


if __name__ == '__main__':
    unittest.main()
