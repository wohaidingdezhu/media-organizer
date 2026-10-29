"""Concurrent source edits and failed saves cannot change the wrong scope."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import media_gui
import workspace_data
import test_scan_request_feedback
import test_report_history
from urllib.parse import urlsplit


class ComplexSourceOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.paths = [self.root / name for name in ('first', 'second', 'third')]
        for path in self.paths:
            path.mkdir()
        self.state = media_gui.DashboardState(self.root / 'reports')
        self.addCleanup(self.state.close)
        self.state.folders = [str(path) for path in self.paths[:2]]
        self.state.manual_scan_folders = [str(self.paths[1])]
        self.state.save_settings()

    def test_old_window_cannot_remove_a_different_folder_at_reused_index(self):
        old_path = self.state.folders[0]
        self.state.remove_folder(0, old_path)
        with self.assertRaisesRegex(ValueError, '列表已变化'):
            self.state.remove_folder(0, old_path)
        self.assertEqual(self.state.folders, [str(self.paths[1])])
        self.assertEqual(workspace_data.read_json(self.state.output, 'workspace-settings.json', {})['folders'], self.state.folders)
        self.assertTrue(all(path.is_dir() for path in self.paths))

    def test_add_valid_then_invalid_folder_is_atomic(self):
        invalid = self.root / 'file.txt'
        invalid.write_text('generated')
        before = self.state.snapshot()
        with mock.patch.object(media_gui.scan, 'choose_folders', return_value=[self.paths[2], invalid]):
            with self.assertRaises(ValueError):
                self.state.add_folders()
        self.assertEqual(self.state.snapshot(), before)

    def test_failed_add_save_preserves_scope_and_next_attempt_succeeds(self):
        before = self.state.snapshot()
        with mock.patch.object(media_gui.scan, 'choose_folders', return_value=[self.paths[2]]):
            with mock.patch.object(self.state, 'save_settings', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    self.state.add_folders()
            self.assertEqual(self.state.snapshot(), before)
            self.state.add_folders()
        self.assertEqual(self.state.folders.count(str(self.paths[2])), 1)
        self.assertIn(str(self.paths[2]), self.state.manual_scan_folders)

    def test_failed_last_folder_removal_preserves_monitor_and_retry(self):
        self.state.folders = [str(self.paths[0])]
        self.state.manual_scan_folders = list(self.state.folders)
        self.state.monitor = {'enabled': True, 'minutes': 15}
        self.state.next_scan = 12345
        before = (list(self.state.folders), list(self.state.manual_scan_folders), dict(self.state.monitor), self.state.next_scan, self.state.status)
        with mock.patch.object(self.state, 'save_settings', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.state.remove_folder(0, str(self.paths[0]))
        self.assertEqual((self.state.folders,self.state.manual_scan_folders,self.state.monitor,self.state.next_scan,self.state.status), before)
        self.state.remove_folder(0, str(self.paths[0]))
        self.assertFalse(self.state.monitor['enabled'])
        self.assertEqual(self.state.manual_scan_folders, [])

    def test_directory_limit_does_not_save_unloadable_settings(self):
        self.state.folders = [str(self.root / ('generated-' + str(n))) for n in range(100)]
        before = list(self.state.folders)
        with mock.patch.object(media_gui.scan, 'choose_folders', return_value=[self.paths[2]]):
            with self.assertRaisesRegex(ValueError, '100'):
                self.state.add_folders()
        self.assertEqual(self.state.folders, before)

    def test_http_requires_original_path_and_rejects_stale_window(self):
        with mock.patch.object(media_gui, 'ThreadingHTTPServer', test_report_history.MemoryServer):
            server, state, url = media_gui.create_dashboard_server(self.root / 'api-reports')
        self.addCleanup(state.close)
        state.folders = [str(path) for path in self.paths[:2]]
        route = urlsplit(url).path + 'api/folders/remove'
        headers, _ = test_report_history.request(server, route, 'POST', {'index': 0})
        self.assertIn('400', headers)
        original = state.folders[0]
        headers, _ = test_report_history.request(server, route, 'POST', {'index': 0, 'path': original})
        self.assertIn('200', headers)
        headers, _ = test_report_history.request(server, route, 'POST', {'index': 0, 'path': original})
        self.assertIn('400', headers)
        self.assertEqual(state.folders, [str(self.paths[1])])

    def test_ui_sends_original_path_and_refreshes_after_stale_list_error(self):
        test_scan_request_feedback.ScanRequestFeedbackTests.dashboard(self, r'''
const request=scope.request;scope.request=async(path,body)=>{if(path==='folders/remove'){requests.push({path,body});throw Error('文件夹列表已变化');}return request(path,body);};
await $('folders').children[0].children[1].onclick();assert.equal(requests[0].body.index,0);assert.equal(requests[0].body.path,initial.folders[0]);assert($('notice').textContent.includes('列表已变化'));assert(!$('add-folder').disabled);assert.equal(requests.filter(r=>r.path==='folders/remove').length,1);
''')

    node = test_scan_request_feedback.ScanRequestFeedbackTests.node


if __name__ == '__main__':
    unittest.main()
