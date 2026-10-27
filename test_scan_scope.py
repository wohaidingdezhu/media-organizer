"""User-selected source scope is independent of the saved folder collection."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import library_index
import media_gui
import media_scan
from test_compact_browsing import CompactBrowsingTests


class ScanScopeTests(unittest.TestCase):
    def test_startup_uses_existing_scan_without_starting_or_creating_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output = root / 'generated', root / 'reports'
            source.mkdir()
            (source / 'a.jpg').write_bytes(b'generated photo')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(media_scan.main([str(source), '--output', str(output), '--no-image-metadata']), 0)
            before = set(output.glob('scan-*'))
            with mock.patch.object(media_scan, 'choose_folders') as chooser, mock.patch.object(media_gui.DashboardState, 'start_scan') as start:
                state = media_gui.DashboardState(output)
                try:
                    self.assertEqual(state.snapshot()['latest_report'], next(iter(before)).name)
                    self.assertFalse(state.snapshot()['running'])
                    self.assertEqual(state.report_history()['reports'][0]['roots'], [str(source)])
                    self.assertEqual(set(output.glob('scan-*')), before)
                    chooser.assert_not_called()
                    start.assert_not_called()
                finally:
                    state.close()

    def test_selected_scope_scans_only_new_source_and_preserves_old_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first, second, output = root / 'first', root / 'second', root / 'reports'
            first.mkdir()
            second.mkdir()
            (first / 'a.jpg').write_bytes(b'generated first photo')
            (second / 'b.jpg').write_bytes(b'generated second photo')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(media_scan.main([str(first), '--output', str(output), '--no-image-metadata']), 0)
            state = media_gui.DashboardState(output)
            self.addCleanup(state.close)
            state.folders = [str(first), str(second)]
            state.start_scan({'image_analysis': False, 'scan_folders': [str(second)]})
            state.scan_thread.join(90)
            self.assertFalse(state.running)
            reports = state.report_history()['reports']
            self.assertEqual(len(reports), 2)
            self.assertEqual(reports[0]['roots'], [str(second)])
            self.assertEqual(state.folders, [str(first), str(second)])
            self.assertNotIn('scan_folders', state.scan_options)
            self.assertEqual(library_index.catalog(output)['all_count'], 2)
            self.assertTrue(all(item['state'] != 'not_seen' for item in library_index.catalog(output)['items']))

    def test_invalid_or_empty_scope_does_not_start_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = media_gui.DashboardState(root / 'reports')
            self.addCleanup(state.close)
            state.folders = [str(root)]
            for chosen in ([], ['/not-in-the-list'], [str(root), str(root)], [1]):
                with self.subTest(chosen=chosen), self.assertRaises(ValueError):
                    state.start_scan({'scan_folders': chosen})
                self.assertFalse(state.running)

    def test_scope_selection_survives_refresh_and_new_folder_does_not_reselect_old_one(self):
        CompactBrowsingTests.node(self, r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');
E.prototype.removeAttribute=function(key){delete this.attrs[key];};
Object.assign(scope,{folderSelection:new Map(),lastFolders:'',lastLog:'',monitorEditing:false,document:{createElement:t=>new E(t),activeElement:null},act(){}});
vm.runInContext(html.slice(html.indexOf('function syncScanScope('),html.indexOf('async function refresh()')),scope);
const status={folders:['C:\\old'],running:false,status:'ready',logs:[],monitor:{enabled:false}};
scope.renderStatus(status);const oldCheck=$('folders').children[0].children[0].children[0];assert(oldCheck.checked);
oldCheck.checked=false;oldCheck.onchange();assert($('scan').disabled);
status.folders=['C:\\old','D:\\new'];scope.renderStatus(status);
assert(!$('folders').children[0].children[0].children[0].checked);assert($('folders').children[1].children[0].children[0].checked);
assert(!$('scan').disabled);assert($('scan-scope').textContent.includes('1 / 2'));
scope.renderStatus({...status,running:true});assert($('folders').children[1].children[0].children[0].disabled);
assert.equal(requests.length,0);
''')


if __name__ == '__main__':
    unittest.main()
