"""Safe handling of stale library entries using generated temporary media."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

import file_operations
import library_server
from media_actions import MediaActions, file_signature, media_id
from test_library_server import MemoryServer, request
from test_support import make_symlink


class MissingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve()
        self.source = self.output / 'source'
        self.source.mkdir()
        self.report = self.output / 'scan-test'
        self.report.mkdir()
        self.paths = [self.source / 'left.jpg', self.source / 'right.jpg']
        for path in self.paths:
            path.write_bytes(b'generated identical test image')
        digest = hashlib.sha256(self.paths[0].read_bytes()).hexdigest()
        records = []
        for path in self.paths:
            info = path.stat()
            records.append({'path': str(path), 'kind': '照片', 'bytes': info.st_size,
                            'mtime': info.st_mtime, 'sha256': digest,
                            'source_signature': file_signature(info),
                            'suggested_path': '照片/' + path.name})
        self.doc = {'roots': [str(self.source)], 'files': records,
                    'duplicates': [{'paths': [str(p) for p in self.paths],
                                    'sha256': digest, 'bytes_each': records[0]['bytes']}],
                    'video_library': {'groups': []}}
        (self.report / 'report.json').write_text(json.dumps(self.doc), encoding='utf-8')
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            self.server, url = library_server.create_library_server(self.report, self.output)
        self.prefix = urlsplit(url).path.removesuffix('library.html')
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.server.missing_monitor.stop)

    def run_check(self):
        monitor = self.server.missing_monitor
        deadline = time.monotonic() + 3
        while monitor.snapshot()[1]['scanning'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(monitor.request_scan(force=True))
        while monitor.snapshot()[1]['scanning'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(monitor.snapshot()[1]['scanning'])

    def preview(self, path):
        with mock.patch.object(file_operations, 'trash_helper', return_value=Path('/temporary/mock')):
            headers, body = request(self.server, self.prefix + 'api/operations/preview', 'POST',
                                    {'mode': 'trash', 'ids': [media_id(str(path))]}, origin=self.origin)
        return headers, json.loads(body)

    def center(self):
        headers, body = request(self.server, self.prefix + 'api/center')
        self.assertIn('200 OK', headers)
        return json.loads(body)

    def test_missing_retained_file_hides_until_it_returns_and_no_file_is_deleted(self):
        self.paths[1].unlink()
        headers, result = self.preview(self.paths[0])
        self.assertIn('400', headers)
        self.assertIn(str(self.paths[1]), result['error'])
        self.assertEqual(result['missing_count'], 1)
        self.assertTrue(self.paths[0].is_file())
        center = self.center()
        self.assertEqual(center['missing_files'], 1)
        self.assertEqual(len(center['items']), 1)
        self.paths[1].write_bytes(b'generated identical test image')
        self.run_check()
        self.assertEqual(self.center()['missing_files'], 0)
        self.assertEqual(len(self.center()['items']), 2)

    def test_selected_missing_file_hides_but_disconnected_root_never_hides(self):
        self.paths[0].unlink()
        headers, result = self.preview(self.paths[0])
        self.assertIn('400', headers)
        self.assertEqual(result['missing_count'], 1)
        self.assertEqual(self.center()['missing_files'], 1)
        # An inaccessible root is not evidence of a missing file and restores
        # the previously hidden entry until the source is available again.
        self.source.rename(self.output / 'offline')
        self.run_check()
        self.assertEqual(self.center()['missing_files'], 0)
        self.assertEqual(len(self.center()['items']), 2)

    def test_permission_errors_and_changed_device_do_not_claim_missing(self):
        manager = MediaActions(self.doc)
        identifier = media_id(str(self.paths[0]))
        with mock.patch('media_actions.checked_stat', side_effect=PermissionError('denied')):
            self.assertEqual(manager.availability(identifier), 'unavailable')
        manager.records[identifier]['source_signature'][0] += 1
        self.paths[0].unlink()
        self.assertEqual(manager.availability(identifier), 'unavailable')

    def test_symlink_replacement_is_not_mistaken_for_missing(self):
        manager = MediaActions(self.doc)
        identifier = media_id(str(self.paths[0]))
        self.paths[0].unlink()
        make_symlink(self.paths[0], self.paths[1])
        self.assertEqual(manager.availability(identifier), 'unavailable')
        with self.assertRaises(ValueError):
            manager.validate(identifier)

    def test_changed_retained_file_explains_which_file_and_stops(self):
        self.paths[1].write_bytes(b'changed generated data')
        headers, result = self.preview(self.paths[0])
        self.assertIn('400', headers)
        self.assertIn(str(self.paths[1]), result['error'])
        self.assertIn('当前文件未删除', result['error'])
        self.assertNotIn('missing_count', result)
        self.assertTrue(all(path.is_file() for path in self.paths))

    def test_background_check_finds_moved_file_without_attempting_delete(self):
        self.paths[0].unlink()
        # Viewing a catalog kicks off one asynchronous check, not a full scan
        # on the request thread. Poll the tiny status endpoint for completion.
        initial = self.center()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            headers, body = request(self.server, self.prefix + 'api/media/availability')
            self.assertIn('200 OK', headers)
            status = json.loads(body)
            if not status['scanning']:
                break
            time.sleep(.005)
        self.assertEqual(status['checked'], 2)
        self.assertEqual(status['missing_count'], 1)
        self.assertFalse(self.server.missing_monitor.request_scan(), 'full check must not repeat on each request')
        self.assertGreaterEqual(status['revision'], initial['availability_revision'])
        center = self.center()
        self.assertEqual(center['missing_files'], 1)
        self.assertEqual(center['availability_revision'], status['revision'])
        self.assertTrue(self.paths[1].is_file())

    def test_checks_do_not_overlap_and_stop_on_service_close(self):
        from missing_monitor import MissingMonitor
        manager = MediaActions(self.doc)
        entered, release = threading.Event(), threading.Event()
        def slow(_identifier):
            entered.set()
            release.wait(3)
            return 'available'
        monitor = MissingMonitor(manager, delay=0)
        self.addCleanup(release.set)
        with mock.patch.object(manager, 'availability', side_effect=slow):
            self.assertTrue(monitor.request_scan())
            self.assertTrue(entered.wait(2))
            self.assertFalse(monitor.request_scan(force=True))
            monitor.stop()
            release.set()
            deadline = time.monotonic() + 3
            while monitor.snapshot()[1]['scanning'] and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(monitor.snapshot()[1]['scanning'])
            self.assertEqual(monitor.snapshot()[1]['checked'], 1)
            self.assertFalse(monitor.request_scan(force=True))

    def test_center_returns_while_background_file_check_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(_identifier):
            entered.set()
            release.wait(3)
            return 'available'
        with mock.patch.object(self.server.missing_monitor.media, 'availability', side_effect=slow):
            start = time.monotonic()
            self.center()
            self.assertLess(time.monotonic() - start, 1)
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.server.missing_monitor.status()['scanning'])
            self.server.missing_monitor.stop()
            release.set()

    def test_refresh_rechecks_restored_file_without_waiting_for_full_scan_interval(self):
        self.paths[0].unlink()
        self.run_check()
        self.assertEqual(self.center()['missing_files'], 1)
        self.paths[0].write_bytes(b'restored generated image')
        headers, body = request(self.server, self.prefix + 'api/media/availability/recheck', 'POST', {}, origin=self.origin)
        self.assertIn('200 OK', headers)
        self.assertEqual(json.loads(body)['queued'], 1)
        deadline = time.monotonic() + 3
        while self.server.missing_monitor.status()['scanning'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.center()['missing_files'], 0)
        self.assertEqual(len(self.center()['items']), 2)

    def test_recheck_request_rejects_an_untrusted_origin(self):
        headers, _ = request(self.server, self.prefix + 'api/media/availability/recheck', 'POST', {}, origin='http://example.invalid')
        self.assertIn('403', headers)
        self.assertFalse(self.server.missing_monitor.status()['scanning'])

    def test_manual_confirmation_cannot_be_overwritten_by_an_older_background_check(self):
        from missing_monitor import MissingMonitor
        manager = MediaActions(self.doc)
        identifier = media_id(str(self.paths[0]))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(current):
            if current == identifier:
                entered.set()
                release.wait(3)
            return 'available'
        monitor = MissingMonitor(manager, delay=0)
        self.addCleanup(monitor.stop)
        with mock.patch.object(manager, 'availability', side_effect=slow):
            self.assertTrue(monitor.request_scan())
            self.assertTrue(entered.wait(2))
            monitor.mark_missing([str(self.paths[0])])
            release.set()
            deadline = time.monotonic() + 3
            while monitor.status()['scanning'] and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertIn(str(self.paths[0]), monitor.snapshot()[0])

    def test_recheck_has_priority_during_long_full_check(self):
        from missing_monitor import MissingMonitor
        manager = MediaActions(self.doc)
        first, second = map(lambda path: media_id(str(path)), self.paths)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def slow(identifier):
            calls.append(identifier)
            if identifier == first:
                entered.set()
                release.wait(3)
            return 'available'
        monitor = MissingMonitor(manager, delay=0)
        self.addCleanup(monitor.stop)
        monitor.mark_missing([str(self.paths[1])])
        with mock.patch.object(manager, 'availability', side_effect=slow):
            self.assertTrue(monitor.request_scan())
            self.assertTrue(entered.wait(2))
            self.assertEqual(monitor.recheck_missing(), 1)
            release.set()
            deadline = time.monotonic() + 3
            while monitor.status()['scanning'] and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(calls, [first, second, second])
            self.assertNotIn(str(self.paths[1]), monitor.snapshot()[0])


if __name__ == '__main__':
    unittest.main()
