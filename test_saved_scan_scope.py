"""Remember manual source scope without restricting scheduled/recovery scans."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import media_gui
import workspace_data
import test_compact_browsing


class SavedScanScopeTests(unittest.TestCase):
    node = test_compact_browsing.CompactBrowsingTests.node

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.first, self.second = self.root / 'first', self.root / 'second'
        self.first.mkdir()
        self.second.mkdir()
        self.output = self.root / 'reports'
        self.state = self.new_state()
        self.state.folders = [str(self.first), str(self.second)]

    def new_state(self):
        state = media_gui.DashboardState(self.output)
        self.addCleanup(state.close)
        return state

    def submit(self, options):
        with mock.patch.object(media_gui.threading, 'Thread'):
            self.state.start_scan(options)
        self.state.running = False

    def test_restart_remembers_scope_without_starting_scan(self):
        self.submit({'scan_folders': [str(self.second)]})
        restored = self.new_state()
        self.assertEqual(restored.snapshot()['manual_scan_folders'], [str(self.second)])
        self.assertEqual(restored.folders, [str(self.first), str(self.second)])
        self.assertFalse(restored.running)
        self.assertNotIn('scan_folders', restored.scan_options)

    def test_automatic_or_report_scope_does_not_replace_manual_choice(self):
        self.submit({'scan_folders': [str(self.second)]})
        with mock.patch.object(media_gui, 'scan_arguments', wraps=media_gui.scan_arguments) as args:
            self.submit({})
            self.assertEqual(args.call_args.args[0], [str(self.first), str(self.second)])
        with mock.patch.object(media_gui.threading, 'Thread'):
            self.state.start_scan({}, folders=[str(self.first)])
        self.state.running = False
        self.assertEqual(self.new_state().snapshot()['manual_scan_folders'], [str(self.second)])

    def test_add_and_remove_update_saved_scope_without_reselecting_others(self):
        self.submit({'scan_folders': [str(self.second)]})
        self.state.remove_folder(1)
        self.assertEqual(self.new_state().snapshot()['manual_scan_folders'], [])
        with mock.patch.object(media_gui.scan, 'choose_folders', return_value=[self.second]):
            self.state.add_folders()
        self.assertEqual(self.new_state().snapshot()['manual_scan_folders'], [str(self.second)])

    def test_invalid_scope_and_failed_save_do_not_start_or_change_scope(self):
        self.submit({'scan_folders': [str(self.second)]})
        with self.assertRaises(ValueError):
            self.state.start_scan({'scan_folders': []})
        with mock.patch.object(self.state, 'save_settings', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                self.state.start_scan({'scan_folders': [str(self.first)]})
        self.assertFalse(self.state.running)
        self.assertEqual(self.state.manual_scan_folders, [str(self.second)])

    def test_legacy_or_corrupt_scope_retains_valid_folder_settings(self):
        self.state.save_settings()
        self.assertEqual(self.new_state().snapshot()['manual_scan_folders'], self.state.folders)
        self.state.manual_scan_folders = [str(self.second)]
        self.state.load_settings()
        self.assertIsNone(self.state.manual_scan_folders)
        settings = workspace_data.read_json(self.output, 'workspace-settings.json', {})
        for chosen in ('invalid', [str(self.first), str(self.first)], [str(self.root / 'other')], [1]):
            settings['manual_scan_folders'] = chosen
            workspace_data.write_json(self.output, 'workspace-settings.json', settings)
            restored = self.new_state()
            self.assertEqual(restored.folders, self.state.folders)
            self.assertEqual(restored.snapshot()['manual_scan_folders'], self.state.folders)

    def test_ui_restores_scope_but_preserves_unsaved_edits_during_polling(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');E.prototype.removeAttribute=function(key){delete this.attrs[key];};
Object.assign(scope,{stopped:false,folderSelection:new Map(),lastFolders:'',lastLog:'',monitorEditing:false,document:{createElement:t=>new E(t),activeElement:null},act(){}});
vm.runInContext(html.slice(html.indexOf('function syncScanScope('),html.indexOf('async function refresh()')),scope);
let status={folders:['C:\\first','D:\\second'],manual_scan_folders:['D:\\second'],running:false,status:'ready',logs:[],monitor:{enabled:false}};
scope.renderStatus(status);const picks=()=>$('folders').children.map(li=>li.children[0].children[0]);assert(!picks()[0].checked);assert(picks()[1].checked);
picks()[0].checked=true;picks()[0].onchange();scope.renderStatus({...status,running:true});assert(picks()[0].checked);scope.renderStatus(status);assert(picks()[0].checked);
// Simulate a restart, with no in-memory choices. Empty saved scope never selects all.
scope.folderSelection.clear();scope.lastFolders='';status.manual_scan_folders=[];scope.renderStatus(status);assert(picks().every(check=>!check.checked));assert($('scan').disabled);
''')


if __name__ == '__main__':
    unittest.main()
