# coding: utf-8
"""Persistent library, restore and desktop checks using temporary generated data."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import media_scan
import media_gui
import portable_fs as fs
import library_backup
import library_index
from media_actions import MediaActions
from workspace_data import data_lock, read_json, write_json, active_workspace
from local_movie_metadata import parse_nfo, enrich_library
from test_support import make_symlink
from test_report_history import MemoryServer, request
import desktop_entry
import media_backend


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / '电影与照片'
        self.output = self.base / 'reports'
        self.source.mkdir()
        self.movie = self.source / 'movie.mp4'
        self.movie.write_bytes(b'generated video')

    def tearDown(self):
        self.temporary.cleanup()

    def scan(self, source=None):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media_scan.main([str(source or self.source), '--output', str(self.output), '--no-image-metadata']), 0)
        return max(self.output.glob('scan-*'))

    def test_collection_persists_and_monitor_is_opt_in(self):
        with mock.patch.object(media_scan, 'choose_folders', return_value=[str(self.source)]):
            state = media_gui.DashboardState(self.output)
            self.assertFalse(state.monitor['enabled'])
            state.add_folders()
            state.set_monitor({'enabled': True, 'minutes': 20})
            restarted = media_gui.DashboardState(self.output)
            self.assertEqual(restarted.folders, [str(self.source)])
            self.assertEqual(restarted.monitor, {'enabled': True, 'minutes': 20})
            restarted.remove_folder(0)
            self.assertFalse(restarted.monitor['enabled'])
            state.close()
            restarted.close()

    def test_monitor_due_and_busy_behavior(self):
        state = media_gui.DashboardState(self.output)
        state.folders = [str(self.source)]
        state.set_monitor({'enabled': True, 'minutes': 1})
        due = state.next_scan
        with mock.patch.object(state, 'start_scan') as start:
            self.assertFalse(state.monitor_tick(due - 1))
            self.assertTrue(state.monitor_tick(due))
            self.assertFalse(state.monitor_tick(due))
            start.assert_called_once_with({})
            state.running = True
            self.assertFalse(state.monitor_tick(due + 60))
            state.running = False
        state.close()

    def test_invalid_monitor_and_settings(self):
        state = media_gui.DashboardState(self.output)
        for values in ({'enabled':True,'minutes':1}, {'enabled':1,'minutes':5}, {'enabled':False,'minutes':0}, {'enabled':False,'minutes':True}):
            with self.assertRaises(ValueError):
                state.set_monitor(values)
        fs.ensure_private_directory(self.output)
        write_json(self.output, 'workspace-settings.json', {'folders': 'invalid'})
        restarted = media_gui.DashboardState(self.output)
        self.assertEqual(restarted.folders, [])
        self.assertTrue(restarted.logs)
        state.close()
        restarted.close()

    def test_index_new_changed_missing_and_disconnected_root(self):
        self.scan()
        self.assertEqual(library_index.catalog(self.output)['items'][0]['state'], 'new')
        self.movie.write_bytes(b'changed generated movie')
        self.scan()
        self.assertEqual(library_index.catalog(self.output)['items'][0]['state'], 'changed')
        other = self.base / '另一磁盘'
        other.mkdir()
        (other / 'movie.mp4').write_bytes(b'another generated movie')
        self.scan(other)
        self.assertEqual(library_index.catalog(self.output)['all_count'], 2)
        self.movie.unlink()
        self.scan()
        result = library_index.catalog(self.output, state='not_seen')
        self.assertEqual([item['path'] for item in result['items']], [str(self.movie)])
        self.assertEqual(library_index.catalog(self.output, folder=str(other))['total'], 1)

    def test_index_reuses_only_report_cache_and_source_bytes_still_checked(self):
        self.scan()
        with mock.patch.object(fs, 'read_private_file', wraps=fs.read_private_file) as read:
            library_index.catalog(self.output)
            self.assertFalse(any(call.args[1][-1] == 'report.json' for call in read.call_args_list))
        copy = self.source / 'copy.mp4'
        copy.write_bytes(self.movie.read_bytes())
        report = self.scan()
        self.assertEqual(len(json.loads((report / 'report.json').read_text(encoding='utf-8'))['duplicates']), 1)

    def test_foreign_windows_and_unc_paths_are_browsable_on_mac(self):
        self.scan()
        report = max(self.output.glob('scan-*')) / 'report.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        (self.output / library_index.INDEX).unlink()
        data['roots'] = ['Z:\\Movies', '\\\\nas\\share']
        data['files'] = [{'path': 'Z:\\Movies\\Same\\film.mp4', 'root':'Z:\\Movies', 'kind':'视频', 'bytes':1},
                         {'path':'\\\\nas\\share\\Same\\photo.jpg','root':'\\\\nas\\share','kind':'照片','bytes':1}]
        report.write_text(json.dumps(data), encoding='utf-8')
        result = library_index.catalog(self.output)
        self.assertEqual(result['all_count'], 2)
        self.assertEqual(library_index.catalog(self.output, folder='Z:\\Movies')['total'], 1)
        self.assertEqual(len(result['folders']), 2)

    def test_backup_restore_keeps_current_data_and_disables_original_actions(self):
        report = self.scan()
        key = json.loads((report / 'report.json').read_text(encoding='utf-8'))['video_library']['groups'][0]['tag_key']
        write_json(self.output, 'library-tags.json', {'version':1, 'groups':{key:['收藏','已观看']}})
        write_json(self.output, 'library-notes.json', {'version':1, 'groups':{key:{'rating':5,'note':'测试备注'}}})
        original = self.movie.read_bytes()
        state = media_gui.DashboardState(self.output)
        preview = state.preview_restore(state.backup())
        self.assertTrue((self.output / report.name / 'report.json').is_file())
        self.assertEqual(state.output, self.output)
        state.confirm_restore(preview['token'])
        self.assertNotEqual(state.output, self.output)
        restored = json.loads((state.output / report.name / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(MediaActions(restored).records, {})
        self.assertEqual(read_json(state.output,'library-tags.json')['groups'][key], ['收藏','已观看'])
        self.assertEqual(read_json(state.output,'library-notes.json')['groups'][key]['rating'], 5)
        self.assertFalse(state.monitor['enabled'])
        self.assertEqual(active_workspace(self.output), state.output)
        self.assertEqual(self.movie.read_bytes(), original)
        with self.assertRaises(ValueError):
            state.confirm_restore(preview['token'])
        state.close()

    def test_backup_skips_cancelled_exports_and_rejects_empty_library(self):
        fs.ensure_private_directory(self.output)
        with self.assertRaises(ValueError): library_backup.export_backup(self.output)
        report = self.scan()
        partial = self.output / 'scan-interrupted'
        partial.mkdir()
        (partial / 'library.html').write_bytes(b'incomplete')
        files, _ = library_backup.validate_backup(library_backup.export_backup(self.output))
        self.assertFalse(any(name.startswith('scan-interrupted/') for name in files))
        self.assertIn(report.name + '/report.json', files)

    def test_backup_excludes_originals_and_rewrites_imported_html(self):
        report = self.scan()
        (report / 'report.html').write_text('<script>bad()</script>')
        body = library_backup.export_backup(self.output)
        files, summary = library_backup.validate_backup(body)
        self.assertEqual(summary['reports'], 1)
        self.assertNotIn(b'bad()', files[report.name + '/report.html'])
        self.assertFalse(any(name.endswith('.mp4') for name in files))

    def test_restore_preview_token_and_busy_guard(self):
        self.scan()
        state = media_gui.DashboardState(self.output)
        preview = state.preview_restore(state.backup())
        with self.assertRaises(ValueError): state.confirm_restore('wrong')
        state.running = True
        with self.assertRaises(ValueError): state.confirm_restore(preview['token'])
        with self.assertRaises(ValueError): state.backup()
        state.running = False
        with mock.patch('workspace_controller.time.monotonic', return_value=10**12):
            with self.assertRaises(ValueError): state.confirm_restore(preview['token'])
        state.close()

    def test_backup_rejects_traversal_duplicates_corruption_and_symlinks(self):
        self.scan()
        for name in ('../report.json','scan-a//report.json','scan-a/../report.json','C:/x','scan-a/operations/../x.json'):
            self.assertFalse(library_backup.allowed(name))
        for members in ([('manifest.json',b'{}'),('manifest.json',b'{}')], [('manifest.json',b'{')], [('manifest.json',b'{"version":1,"files":{"../x":{}}}'),('../x',b'x')]):
            output = io.BytesIO()
            with contextlib.redirect_stderr(io.StringIO()), zipfile.ZipFile(output,'w') as archive:
                for name, body in members: archive.writestr(name,body)
            with self.assertRaises(ValueError): library_backup.validate_backup(output.getvalue())
        report = max(self.output.glob('scan-*'))
        saved = self.base / 'saved-report.json'
        (report / 'report.json').replace(saved)
        make_symlink(report / 'report.json', saved)
        with self.assertRaises((OSError,ValueError)): library_backup.export_backup(self.output)

    def test_private_data_rejects_links_and_serializes_metadata(self):
        fs.ensure_private_directory(self.output)
        path = self.output / 'unsafe.json'
        target = self.base / 'target.json'
        target.write_bytes(b'unchanged')
        make_symlink(path, target)
        with self.assertRaises((OSError,ValueError)): fs.write_private_file(self.output, path.name, b'bad')
        with self.assertRaises((OSError,ValueError)): fs.read_private_file(self.output, (path.name,))
        self.assertEqual(target.read_bytes(), b'unchanged')
        with data_lock(self.output):
            with data_lock(self.output): write_json(self.output, 'safe.json', {'sample':True})
        self.assertEqual(read_json(self.output,'safe.json'), {'sample':True})

    def test_dashboard_api_catalog_and_monitor_reject_foreign_origin(self):
        self.scan()
        with mock.patch.object(media_gui,'ThreadingHTTPServer',MemoryServer):
            server, state, url = media_gui.create_dashboard_server(self.output)
            prefix = '/' + url.split('/')[-2] + '/'
            headers, raw = request(server, prefix+'api/catalog', 'POST', {})
            self.assertIn('200 OK',headers)
            self.assertEqual(json.loads(raw)['all_count'],1)
            headers, _ = request(server,prefix+'api/monitor','POST',{'enabled':False,'minutes':5},origin='http://evil.example')
            self.assertIn('403 Forbidden',headers)
            state.close()

    def test_nfo_offline_fields_and_unsafe_xml_rejection(self):
        result = parse_nfo(b'<movie><title>&lt;script&gt;</title><plot>Story</plot><actor><name>A</name></actor><genre>Action</genre><tag>Local</tag><thumb>https://never-open.example/poster</thumb></movie>')
        self.assertEqual(result['title'],'<script>')
        self.assertEqual(result['actors'],['A'])
        self.assertNotIn('thumb',result)
        for body in (b'<!DOCTYPE movie [<!ENTITY a "unsafe">]><movie/>', b'<invalid/>', b'<movie>', b'x'*(1024*1024+1)):
            with self.assertRaises((ValueError,__import__('xml.etree.ElementTree',fromlist=['ParseError']).ParseError)):
                parse_nfo(body)

    def test_changed_nfo_is_not_imported(self):
        nfo = self.source/'movie.nfo'
        nfo.write_bytes(b'<movie><title>Before</title></movie>')
        sidecar={'path':str(nfo),'extension':'nfo','status':'已关联','media_paths':[str(self.movie)],'_signature':media_scan.signature(nfo.stat())}
        nfo.write_bytes(b'<movie><title>After</title></movie>')
        groups=[{'files':[{'path':str(self.movie)}]}]
        issues=[]
        enrich_library(groups,[sidecar],issues)
        self.assertEqual(groups[0]['metadata'],{})
        self.assertTrue(issues)

    def test_frozen_worker_dispatch_and_native_url_guards(self):
        with mock.patch.object(sys,'frozen',True,create=True):
            self.assertEqual(media_backend.command(Path('portable_image_probe.py')), [sys.executable,'--worker','portable_image_probe'])
        for url in ('http://evil.example/a/', 'http://127.0.0.1:80/../', 'file:///x', 'http://127.0.0.1:80/'+ 'a'*24 + '/bad'):
            with self.assertRaises(ValueError): desktop_entry.local_url(url)
        self.assertEqual(desktop_entry.local_url('http://127.0.0.1:12345/'+'a'*24+'/'),'http://127.0.0.1:12345/'+'a'*24+'/')

    def test_native_window_has_valid_private_console_in_production(self):
        with desktop_entry.prepare_console(self.output) as stream:
            stream.write('中文启动记录\n')
            self.assertIsInstance(stream.fileno(), int)
        self.assertEqual(fs.read_private_file(self.output, ('desktop-session.log',)).decode('utf-8'), '中文启动记录\n')
        target = self.base / 'log-target'
        target.write_bytes(b'unchanged')
        (self.output / 'desktop-session.log').unlink()
        make_symlink(self.output / 'desktop-session.log', target)
        with self.assertRaises((OSError, ValueError)):
            desktop_entry.prepare_console(self.output)
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_native_script_values_do_not_require_unsafe_eval(self):
        window = mock.Mock()
        for raw in (True, 'true', '"true"'):
            window.run_js.return_value = raw
            self.assertIs(desktop_entry.native_value(window, 'true'), True)
        for raw in (False, 'false', '"false"'):
            window.run_js.return_value = raw
            self.assertIs(desktop_entry.native_value(window, 'false'), False)
        window.evaluate_js.assert_not_called()

    def test_dashboard_script_syntax_and_metadata_search_in_actual_js(self):
        import shutil
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node is required by the dual-platform workflow')
        html=(Path(__file__).parent/'dashboard.html').read_text(encoding='utf-8')
        script=html.split('<script>')[1].split('</script>')[0]
        check=self.base/'ui-check.js'
        check.write_text('new (require("vm").Script)('+json.dumps(script)+');\nconst {browseMovies}=require('+json.dumps(str(Path(__file__).parent/'library_browse.js'))+');\nif(browseMovies([{title:"X",metadata:{actors:["Sample Actor"]},files:[]}],{query:"sample actor"}).length!==1)throw Error("NFO search failed");',encoding='utf-8')
        subprocess.run([node,str(check)],check=True,capture_output=True,text=True,encoding='utf-8')
