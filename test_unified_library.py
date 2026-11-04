"""Unified browsing and staged scans use generated reports and temporary media only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

from PIL import Image
import file_operations
import library_server
import media_gui
import media_scan
from media_actions import MediaActions, file_signature, media_id
from media_center import CenterState, center_catalog
from organization_plan import OrganizationPlan
from unified_library import build_library, publish_discovery, PENDING
from test_library_server import MemoryServer, request


class UnifiedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.output = self.base / 'reports'
        self.output.mkdir()
        self.a, self.b = self.base / 'A', self.base / 'B'
        self.a.mkdir()
        self.b.mkdir()

    def record(self, root, name='photo.jpg', content=b'generated media'):
        path = root / name
        path.write_bytes(content)
        return {'path': str(path), 'root': str(root), 'kind': '照片', 'name': name,
                'bytes': len(content), 'mtime': path.stat().st_mtime,
                'source_signature': file_signature(path.stat()),
                'sha256': hashlib.sha256(content).hexdigest(), 'hash_status': '完整校验',
                'suggested_path': '照片/' + name, 'hardlink_to': '', 'extension': '.jpg'}

    def report(self, number, roots, files, **extra):
        folder = self.output / ('scan-%03d' % number)
        folder.mkdir()
        doc = {'created_at': '2026-11-02T12:00:%02d' % number,
               'roots': list(map(str, roots)), 'files': files, 'duplicates': [],
               'issues': [], 'similar': {'pairs': []}, 'video_library': {'groups': []}, **extra}
        (folder / 'report.json').write_text(json.dumps(doc), encoding='utf-8')
        (folder / 'report.html').write_text('generated', encoding='utf-8')
        (folder / 'library.html').write_text('generated', encoding='utf-8')
        return folder

    def catalog(self, document):
        return center_catalog(document, CenterState(self.output).snapshot())

    def server(self, directory):
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(directory, self.output)
        self.addCleanup(server.missing_monitor.stop)
        return server, urlsplit(url).path.removesuffix('library.html')

    def test_separate_scans_share_browsing_labels_favorites_and_scope_removal(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a], [one])
        _, first = build_library(self.output)
        state = CenterState(self.output)
        identifier = media_id(one['path'])
        state.update({'action': 'favorite', 'id': identifier, 'value': True}, {identifier})
        state.update({'action': 'labels', 'id': identifier, 'tags': ['家人']}, {identifier})
        self.report(2, [self.b], [two])
        directory, joined = build_library(self.output)
        items = self.catalog(joined)['items']
        self.assertEqual(len(items), 2)
        saved = next(item for item in items if item['id'] == identifier)
        self.assertTrue(saved['favorite'])
        self.assertEqual(saved['local_tags'], ['家人'])
        self.assertEqual(len(first['files']), 1)
        self.assertEqual(build_library(self.output)[0], directory)
        self.report(3, [self.a], [])
        _, rescanned = build_library(self.output)
        self.assertEqual([file['path'] for file in rescanned['files']], [two['path']])
        self.assertTrue(state.snapshot()['favorites'][identifier])

    def test_unreadable_root_preserves_records_and_unavailable_disk_is_not_missing(self):
        record = self.record(self.a)
        self.report(1, [self.a], [record])
        self.report(2, [self.a], [], issues=[{'path': str(self.a), 'reason': '无法读取目录：offline'}])
        _, doc = build_library(self.output)
        self.assertEqual(len(doc['files']), 1)
        media = MediaActions(doc)
        self.a.rename(self.base / 'offline')
        self.assertEqual(media.availability(media_id(record['path'])), 'unavailable')
        self.assertEqual(len(self.catalog(doc)['items']), 1)

    def test_cancelled_export_cannot_remove_previous_directory_inventory(self):
        record = self.record(self.a)
        self.report(1, [self.a], [record])
        build_library(self.output)
        partial = self.report(2, [self.a], [])
        (partial / 'library.html').unlink()
        _, doc = build_library(self.output)
        self.assertEqual([file['path'] for file in doc['files']], [record['path']])

    def test_each_source_keeps_its_disabled_auto_preview_option(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a], [one], image_inspection={'enabled': False})
        self.report(2, [self.b], [two], image_inspection={'enabled': True})
        _, doc = build_library(self.output)
        files = {file['path']: file for item in self.catalog(doc)['items'] for file in item['files']}
        self.assertFalse(files[one['path']]['auto_preview'])
        self.assertTrue(files[two['path']]['auto_preview'])

    def test_discovery_only_changed_files_cannot_play_delete_or_remain_similar(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a, self.b], [one, two], similar={'pairs': [{'left': one['path'], 'right': two['path'], 'distance': 1}]})
        pending = {**one, 'source_signature': [9] * 5}
        _, doc = build_library(self.output, {'roots': [str(self.a)], 'files': [pending]})
        self.assertTrue(doc['pending_scan'])
        self.assertEqual(len(doc['files']), 2)
        self.assertEqual(doc['similar']['pairs'], [])
        self.assertEqual(doc['duplicates'], [])
        media = MediaActions(doc)
        self.assertNotIn(media_id(one['path']), media.records)
        self.assertIn(media_id(two['path']), media.records)
        self.assertTrue(next(item for item in self.catalog(doc)['items'] if item['id'] == media_id(one['path']))['files'][0]['browse_only'])
        for method in (media.validate, media.validate_for_view):
            with self.assertRaises(ValueError):
                method(media_id(one['path']))

    def test_same_identity_keeps_known_metadata_while_pending_and_cancel_restores_completed_view(self):
        record = self.record(self.a)
        record['image'] = {'width': 12, 'height': 16}
        self.report(1, [self.a], [record])
        old_dir, _ = build_library(self.output)
        _, pending = build_library(self.output, {'roots': [str(self.a)], 'files': [dict(record)]})
        self.assertFalse(pending['files'][0]['browse_only'])
        self.assertEqual(pending['files'][0]['image'], record['image'])
        self.assertEqual(build_library(self.output)[0], old_dir)

    def test_cross_report_exact_groups_keep_one_and_revalidate_changed_keeper(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a], [one])
        self.report(2, [self.b], [two])
        directory, doc = build_library(self.output)
        self.assertEqual(set(doc['duplicates'][0]['paths']), {one['path'], two['path']})
        media = MediaActions(doc)
        plan = OrganizationPlan(directory, doc)
        ops = file_operations.FileOperations(directory, media, lambda: plan)
        ids = [media_id(one['path']), media_id(two['path'])]
        with self.assertRaisesRegex(ValueError, '至少保留'):
            ops.preview('trash', ids)
        self.assertEqual(len(ops.preview('trash', ids[:1])['items']), 1)
        Path(two['path']).write_bytes(b'changed generated keeper')
        with self.assertRaisesRegex(ValueError, '保留'):
            ops.preview('trash', ids[:1])
        self.assertEqual(Path(one['path']).read_bytes(), b'generated media')

    def test_restored_records_stay_read_only_when_mixed_with_local_scan(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a], [one], restored_snapshot=True)
        self.report(2, [self.b], [two])
        _, doc = build_library(self.output)
        self.assertFalse(doc['restored_snapshot'])
        self.assertEqual(set(MediaActions(doc).records), {media_id(two['path'])})
        self.assertEqual(len(self.catalog(doc)['items']), 2)

    def test_existing_cover_is_served_without_copying_or_reading_original(self):
        record = self.record(self.a)
        report = self.report(1, [self.a], [record], previews={record['path']: 'previews/sample.png'})
        (report / 'previews').mkdir()
        buffer = io.BytesIO()
        Image.new('RGB', (16, 12), 'blue').save(buffer, format='PNG')
        (report / 'previews' / 'sample.png').write_bytes(buffer.getvalue())
        directory, doc = build_library(self.output)
        server, prefix = self.server(directory)
        Path(record['path']).unlink()
        header, body = request(server, prefix + doc['previews'][record['path']])
        self.assertIn('200 OK', header)
        self.assertEqual(body, buffer.getvalue())
        self.assertFalse((directory / 'previews').exists())
        self.assertIn('404', request(server, prefix + 'previews/../../report.json')[0])

    def test_gui_all_scope_opens_common_center_and_legacy_tools_stay_scoped(self):
        record = self.record(self.a)
        self.report(1, [self.a], [record])
        state = media_gui.DashboardState(self.output)
        self.addCleanup(state.close)
        dummy = mock.Mock()
        old = mock.Mock()
        state.report_servers['scan-old'] = (old, 'http://127.0.0.1:43210/old/library.html')
        with mock.patch.object(media_gui, 'create_library_server', return_value=(dummy, 'http://127.0.0.1:43210/token/library.html')):
            result = state.view_report('all', 'photos')
            self.assertEqual(result['report_id'], 'all')
            self.assertTrue(result['url'].endswith('center.html#photos'))
            self.assertEqual(result['report']['summary']['files'], 1)
            old.missing_monitor.pause.assert_called_once()
            dummy.missing_monitor.pause.assert_not_called()
            self.assertTrue(state.report_history()['library_available'])
            with self.assertRaises(ValueError):
                state.view_report('all', 'overview')

    def test_scan_publishes_list_before_hashing_and_never_grants_actions(self):
        self.record(self.a)
        observed = []
        def stop_after_discovery(records, issues):
            from scan_feed import ScanFeedReader
            pending = ScanFeedReader(self.output).snapshot()
            observed.extend(pending['files'])
            _, doc = build_library(self.output, pending)
            self.assertEqual(len(self.catalog(doc)['items']), 1)
            self.assertEqual(MediaActions(doc).records, {})
            raise KeyboardInterrupt
        with mock.patch.dict('os.environ', {'MEDIA_ORGANIZER_PROGRESS': '1'}), mock.patch.object(media_scan, 'exact_duplicates', side_effect=stop_after_discovery), contextlib.redirect_stdout(io.StringIO()):
            media_scan.main([str(self.a), '--output', str(self.output), '--no-image-metadata'])
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0]['browse_only'])
        self.assertNotIn('_signature', observed[0])
        self.assertEqual(build_library(self.output)[1]['files'], [])

    def test_real_generated_scans_keep_movie_details_and_photo_navigation(self):
        (self.a / 'Feature2025.mp4').write_bytes(b'Generated movie')
        (self.a / 'Feature2025.nfo').write_text('<movie><title>Example Film</title><actor><name>Example Actor</name></actor></movie>', encoding='utf-8')
        self.record(self.b)
        with contextlib.redirect_stdout(io.StringIO()):
            for root in (self.a, self.b):
                self.assertEqual(media_scan.main([str(root), '--output', str(self.output), '--no-image-metadata']), 0)
        directory, doc = build_library(self.output)
        server, prefix = self.server(directory)
        header, raw = request(server, prefix + 'api/center')
        self.assertIn('200 OK', header)
        catalog = json.loads(raw)
        movie = next(item for item in catalog['items'] if item['kind'] == 'movie')
        self.assertEqual(movie['title'], 'Example Film')
        self.assertEqual(movie['metadata']['actors'], ['Example Actor'])
        self.assertEqual(sum(item['kind'] == 'photo' for item in catalog['items']), 1)
        self.assertTrue(all(not file['auto_preview'] for item in catalog['items'] for file in item['files']))
        self.assertIn('未启用', catalog['review']['similar_note'])

    def test_second_part_without_metadata_or_poster_keeps_known_movie_details(self):
        one, two = self.record(self.a, 'Feature-CD1.mp4'), self.record(self.b, 'Feature-CD2.mp4')
        for record in (one, two):
            record.update(kind='视频', extension='.mp4')
        key = 'c' * 64
        group = {'group_key': key, 'tag_key': key, 'title': 'Feature', 'type': '名称'}
        first = self.report(1, [self.a], [one], video_library={'groups': [{**group,
            'files': [{'path': one['path']}], 'poster': 'covers/poster.png',
            'metadata': {'title': 'Known film', 'actors': ['Known actor']}, 'tags': ['剧情']}]})
        (first / 'covers').mkdir()
        png = io.BytesIO()
        Image.new('RGB', (12, 16), 'blue').save(png, format='PNG')
        (first / 'covers' / 'poster.png').write_bytes(png.getvalue())
        self.report(2, [self.b], [two], video_library={'groups': [{**group,
            'files': [{'path': two['path']}], 'poster': '', 'metadata': {}, 'tags': []}]})
        directory, doc = build_library(self.output)
        merged = doc['video_library']['groups'][0]
        self.assertEqual(merged['metadata']['title'], 'Known film')
        self.assertEqual(merged['metadata']['actors'], ['Known actor'])
        self.assertEqual(merged['tags'], ['剧情'])
        self.assertEqual(len(merged['files']), 2)
        server, prefix = self.server(directory)
        headers, body = request(server, prefix + merged['poster'])
        self.assertIn('200 OK', headers)
        self.assertEqual(body, png.getvalue())

    def test_interrupted_shared_batch_has_current_center_history_entry(self):
        one, two = self.record(self.a), self.record(self.b)
        self.report(1, [self.a, self.b], [one, two])
        directory, _ = build_library(self.output)
        server, prefix = self.server(directory)
        from workspace_data import write_json
        journal = self.output / 'library-actions' / 'operations'
        journal.mkdir()
        planned = [{'path': file['path'], 'target': 'trash'} for file in (one, two)]
        identifier = 'b' * 24
        write_json(journal, identifier + '.json', {'id': identifier, 'mode': 'trash',
                   'status': 'running', 'total': 2, 'created_at': '2026-11-03', 'planned_items': planned,
                   'items': [{**planned[0], 'status': 'success'}, {**planned[1], 'status': 'processing'}]})
        headers, raw = request(server, prefix + 'api/center')
        self.assertIn('200 OK', headers)
        state = json.loads(raw)['operation_state']
        self.assertEqual(state['resumable'], [])
        self.assertEqual(state['unfinished'][0]['id'], identifier)
        self.assertEqual(state['unfinished'][0]['status'], 'interrupted')
        self.assertEqual(state['unfinished'][0]['completed'], 1)


if __name__ == '__main__':
    unittest.main()
