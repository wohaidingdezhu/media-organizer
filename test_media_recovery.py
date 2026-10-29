"""Generated-only regressions for stale playback, cover repair and change watching."""
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock
from urllib.parse import urlsplit

from PIL import Image
import media_actions as actions
import media_gui
import media_scan
import library_server
import portable_fs as fs
from preview_repair import PreviewRepair
from test_library_server import MemoryServer, request
from test_support import make_symlink
import test_media_center as center_tests


class MediaRecoveryTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'generated';self.source.mkdir()
        self.output = self.root / 'reports';self.report = self.output / 'scan-generated';self.report.mkdir(parents=True)
        self.video = self.source / 'sample.mp4';self.video.write_bytes(b'generated video bytes')
        self.record = dict(path=str(self.video), kind='视频', bytes=self.video.stat().st_size,
                           mtime=self.video.stat().st_mtime, source_signature=actions.file_signature(self.video.stat()),
                           sha256=hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.doc = dict(roots=[str(self.source)], files=[self.record])
        self.identifier = actions.media_id(str(self.video))

    def test_attribute_drift_recovers_only_verified_view_not_cleanup(self):
        self.record['source_signature'][-1] -= 1
        manager = actions.MediaActions(self.doc)
        with self.assertRaises(ValueError):manager.validate(self.identifier)
        with mock.patch('system_integration.open_path') as native, mock.patch.object(actions.subprocess, 'run') as mac:
            mac.return_value.returncode = 0
            self.assertTrue(manager.perform(self.identifier, 'open')['ok'])
        with self.assertRaises(ValueError):manager.validate(self.identifier)
        with mock.patch.object(actions.hashlib, 'sha256', side_effect=AssertionError('Already checked this full signature')):
            manager.validate_for_view(self.identifier)
        self.assertEqual(manager.records[self.identifier]['source_signature'], self.record['source_signature'])
        self.assertEqual(self.video.read_bytes(), b'generated video bytes')

    def test_view_rejects_changed_content_missing_hash_large_file_and_links(self):
        self.record['source_signature'][-1] -= 1
        for digest in ('', 'f'*64, None):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                actions.MediaActions({**self.doc, 'files': [{**self.record, 'sha256': digest}]}).validate_for_view(self.identifier)
        original = self.video.stat()
        self.video.write_bytes(b'X'*original.st_size)
        fs.utime(self.video, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(ValueError):actions.MediaActions(self.doc).validate_for_view(self.identifier)
        self.video.unlink();other = self.source / 'other.mp4';other.write_bytes(b'generated video bytes')
        make_symlink(self.video, other)
        with self.assertRaises(ValueError):actions.MediaActions(self.doc).validate_for_view(self.identifier)

    def test_view_refuses_large_verification_and_change_during_hash(self):
        self.record['source_signature'][-1] -= 1
        manager = actions.MediaActions(self.doc)
        info = self.video.stat()
        huge = mock.Mock(st_dev=info.st_dev, st_ino=info.st_ino, st_size=65*1024*1024,
                         st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns, st_birthtime_ns=getattr(info, 'st_birthtime_ns', info.st_ctime_ns))
        with mock.patch.object(actions, 'checked_stat', return_value=huge), self.assertRaises(ValueError):manager.validate_for_view(self.identifier)
        real_read = fs.read
        def changing(fd, amount):
            block = real_read(fd, amount)
            # Simulate observed source replacement after reading without depending on OS timestamp granularity.
            manager.view_signatures.clear()
            return block
        with mock.patch.object(fs, 'read', side_effect=changing), mock.patch.object(actions, 'checked_stat', side_effect=[info, info, huge]), self.assertRaises(ValueError):
            manager.validate_for_view(self.identifier)

    def test_repair_api_persists_cover_and_rejects_foreign_origin_and_stale_file(self):
        (self.report/'report.json').write_text(json.dumps(self.doc), encoding='utf-8')
        before = self.video.read_bytes()
        def write_preview(path, thumbnail, timeout):Image.new('RGB', (40, 30), 'steelblue').save(thumbnail)
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):server, url = library_server.create_library_server(self.report,self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        route = prefix+'api/media/cover'
        with mock.patch.object(media_scan, 'ImageProbeWorker') as worker, mock.patch.object(media_scan, 'helper_available', return_value=True):
            worker.return_value.request.side_effect = write_preview
            self.assertIn('403', request(server,route,'POST',{'id':self.identifier})[0])
            self.assertIn('403', request(server,route,'POST',{'id':self.identifier},origin='http://evil.test')[0])
            header, body = request(server,route,'POST',{'id':self.identifier},origin='http://127.0.0.1:43210')
            self.assertIn('200',header);relative=json.loads(body)['preview'];self.assertTrue((self.report/relative).is_file())
            item = json.loads(request(server,prefix+'api/center')[1])['items'][0];self.assertEqual(item['preview'],relative)
            self.assertEqual(PreviewRepair(self.report,actions.MediaActions(self.doc),self.doc).previews[self.identifier],relative)
            request(server,route,'POST',{'id':self.identifier},origin='http://127.0.0.1:43210');self.assertEqual(worker.return_value.request.call_count,1)
            self.video.write_bytes(b'changed video')
            self.assertIn('400',request(server,route,'POST',{'id':self.identifier},origin='http://127.0.0.1:43210')[0])
        self.assertEqual(before,b'generated video bytes')

    def test_repair_timeout_and_restore_leave_media_and_report_clean(self):
        manager = PreviewRepair(self.report, actions.MediaActions(self.doc), self.doc)
        with mock.patch.object(media_scan,'ImageProbeWorker') as worker, mock.patch.object(media_scan,'helper_available',return_value=True):
            worker.return_value.request.side_effect=subprocess.TimeoutExpired('generated',25)
            with self.assertRaisesRegex(ValueError,'超时'):manager.generate(self.identifier)
            worker.return_value.close.assert_called_once()
        self.assertEqual(list((self.report/'covers').iterdir()),[])
        with self.assertRaises(ValueError):PreviewRepair(self.report, actions.MediaActions({**self.doc,'restored_snapshot':True}),self.doc).generate(self.identifier)
        self.assertEqual(self.video.read_bytes(),b'generated video bytes')

    def test_cached_covers_do_not_starve_later_videos_at_limit(self):
        groups, records = [], []
        for index in range(3):
            path=self.source/f'{index}.mp4';path.write_bytes(b'generated')
            record=dict(path=str(path),kind='视频',extension='mp4',hardlink_to='',_signature=media_scan.signature(path.stat()))
            records.append(record);groups.append(dict(poster='',files=[record]))
        def reuse(record,destination,role):
            if record is records[0]:Image.new('RGB',(10,10)).save(destination);return True
            return False
        def decode(path,thumbnail,timeout):Image.new('RGB',(10,10)).save(thumbnail)
        with mock.patch.object(media_scan,'helper_available',return_value=True),mock.patch.object(media_scan,'ImageProbeWorker') as worker:
            worker.return_value.request.side_effect=decode
            count=media_scan.export_video_frame_covers(self.report,groups,records,Path('generated-helper'),[],limit=1,cache=mock.Mock(thumbnail=reuse))
        self.assertEqual(count,2);self.assertTrue(groups[0]['poster']);self.assertTrue(groups[1]['poster']);self.assertFalse(groups[2]['poster'])

    def test_cover_button_requests_only_clicked_file_and_refreshes_result(self):
        self.run_ui(r'''
movie.preview='';await $('center-refresh').onclick();const card=$('center-content').children[0].children[0],repair=card.children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='补封面');assert(repair);await repair.onclick();const sent=calls.find(c=>c.path==='api/media/cover');assert(sent);assert.equal(sent.body.id,movie.files[0].id);assert.equal(operations.length,0);
''')

    def test_changes_wait_for_stability_and_detect_nested_add_edit_remove(self):
        state=media_gui.DashboardState(self.output);self.addCleanup(state.close);state.folders=[str(self.source)]
        # Use exact scheduled instants: repeated fractional +15 additions can
        # differ by one float ULP from the independently calculated offsets.
        with mock.patch('workspace_controller.time.monotonic',return_value=1000.0):
            state.set_monitor({'enabled':True,'minutes':15,'mode':'changes'})
        now=state.next_scan
        with mock.patch.object(state,'start_scan') as start:
            self.assertFalse(state.monitor_tick(now));self.assertTrue(state.monitor_tick(now+15));self.assertEqual(start.call_count,1)
            self.assertFalse(state.monitor_tick(now+30));self.assertEqual(start.call_count,1)
            nested=self.source/'person';nested.mkdir();photo=nested/'new.jpg';photo.write_bytes(b'first chunk')
            self.assertFalse(state.monitor_tick(now+45));photo.write_bytes(b'second chunk is larger')
            self.assertFalse(state.monitor_tick(now+60));self.assertTrue(state.monitor_tick(now+75));self.assertEqual(start.call_count,2)
            photo.unlink();self.assertFalse(state.monitor_tick(now+90));self.assertTrue(state.monitor_tick(now+105));self.assertEqual(start.call_count,3)
            state.running=True;self.assertFalse(state.monitor_tick(now+120));state.running=False
        restored=media_gui.DashboardState(self.output);self.addCleanup(restored.close);self.assertEqual(restored.monitor['mode'],'changes')

    def test_disconnected_or_linked_root_does_not_replace_library_with_empty_scan(self):
        state=media_gui.DashboardState(self.output);self.addCleanup(state.close);state.folders=[str(self.source)]
        state.set_monitor({'enabled':True,'minutes':1,'mode':'changes'});now=state.next_scan
        moved=self.root/'offline';self.source.rename(moved)
        with mock.patch.object(state,'start_scan') as start:
            self.assertFalse(state.monitor_tick(now));self.assertIn('保留当前资料库',state.watch_status);start.assert_not_called()
            make_symlink(self.source,moved,target_is_directory=True)
            self.assertFalse(state.monitor_tick(now+15));start.assert_not_called()

    def test_fingerprint_excludes_report_output_and_does_not_follow_nested_links(self):
        excluded=self.source/'reports';excluded.mkdir();outside=self.root/'outside';outside.mkdir();(outside/'a.jpg').write_bytes(b'outside')
        link=self.source/'linked';make_symlink(link,outside,target_is_directory=True)
        fingerprint=lambda:fs.directory_fingerprint([str(self.source)],excluded,{'jpg','mp4'},set(),set())
        before=fingerprint();(outside/'b.jpg').write_bytes(b'external change');(excluded/'scan-generated').mkdir()
        self.assertEqual(fingerprint(),before)
        with mock.patch.object(Path,'read_bytes',side_effect=AssertionError('Never reads media contents')):fingerprint()

    def test_pinned_directory_metadata_matches_path_and_inspects_links_without_following(self):
        import stat
        from file_operations import open_directory
        child=self.source/'nested';child.mkdir()
        linked=self.source/'linked';make_symlink(linked,child,target_is_directory=True)
        descriptor=open_directory(self.source)
        try:
            self.assertEqual(actions.file_signature(fs.fstat(descriptor)),actions.file_signature(self.source.stat()))
            self.assertTrue(stat.S_ISDIR(fs.directory_entry_stat(descriptor,'nested').st_mode))
            self.assertTrue(stat.S_ISLNK(fs.directory_entry_stat(descriptor,'linked').st_mode))
            self.assertEqual(actions.file_signature(fs.directory_entry_stat(descriptor,'sample.mp4')),self.record['source_signature'])
            # Reading attributes must not close or transfer ownership of the
            # pinned directory, including on Windows CRT handle conversion.
            self.assertIn('nested',fs.listdir(descriptor))
            self.assertEqual(actions.file_signature(fs.fstat(descriptor)),actions.file_signature(self.source.stat()))
        finally:fs.close(descriptor)

    def test_rescan_uses_report_roots_without_replacing_saved_folder_selection(self):
        (self.report/'report.json').write_text(json.dumps(self.doc),encoding='utf-8')
        state=media_gui.DashboardState(self.output);self.addCleanup(state.close)
        other=self.root/'other';other.mkdir();state.folders=[str(other)]
        with mock.patch.object(state,'start_scan') as start:
            state.rescan_report(self.report.name)
            start.assert_called_once_with({},folders=[str(self.source)])
            self.assertEqual(state.folders,[str(other)]);self.assertEqual(state.scan_origin,'recovery')
        (self.report/'report.json').write_text(json.dumps({**self.doc,'restored_snapshot':True}),encoding='utf-8')
        with mock.patch.object(state,'start_scan') as start,self.assertRaises(ValueError):state.rescan_report(self.report.name)
        start.assert_not_called()

    def test_manual_cover_is_reused_by_next_scan_only_for_same_identity(self):
        from scan_cache import ScanCache
        (self.report/'report.html').write_text('generated',encoding='utf-8')
        (self.report/'report.json').write_text(json.dumps({**self.doc,'options':{'analysis_fingerprint':'generated'}}),encoding='utf-8')
        covers=self.report/'covers';covers.mkdir();Image.new('RGB',(12,12)).save(covers/('repair-'+self.identifier+'.png'))
        cache=ScanCache(self.output,[str(self.source)],'generated')
        record={**self.record,'_signature':tuple(self.record['source_signature'])}
        self.assertTrue(cache.thumbnail(record,self.root/'reused.png','video'))
        self.video.write_bytes(b'changed')
        self.assertFalse(cache.thumbnail(record,self.root/'rejected.png','video'))

    def test_monitor_stop_invalid_modes_and_settings_change_cancel_pending_check(self):
        state=media_gui.DashboardState(self.output);self.addCleanup(state.close);state.folders=[str(self.source)]
        for mode in ([],None,{},'unsupported'):
            with self.subTest(mode=mode),self.assertRaises(ValueError):state.set_monitor({'enabled':True,'minutes':1,'mode':mode})
        state.set_monitor({'enabled':True,'minutes':1,'mode':'changes'});now=state.next_scan
        def disable(*args):
            state.set_monitor({'enabled':False,'minutes':1,'mode':'changes'})
            return 'unused'
        with mock.patch.object(fs,'directory_fingerprint',side_effect=disable),mock.patch.object(state,'start_scan') as start:
            self.assertFalse(state.monitor_tick(now));start.assert_not_called()

    def test_dashboard_watch_preserves_view_and_rescan_messages_are_scoped(self):
        import shutil
        node=shutil.which('node')
        self.assertIsNotNone(node, 'Both platform CI jobs install Node for UI behavior tests')
        script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert'),html=fs.readFileSync(process.argv[1]+'/dashboard.html','utf8');
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{hidden:true});return elements.get(id);};
let status={running:false,latest_report:'new-report',scan_origin:'changes'},loads=[],actions=[],handler;
const scope={$: $,stopped:false,scanControls:{epoch:0},previousRunning:true,knownLatest:'old-report',selectedReport:'old-report',URL,
request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async(...args)=>loads.push(args),
act:async(path,body)=>{actions.push({path,body});return {running:true};},window:{addEventListener:(name,fn)=>handler=fn}};
scope.scanAction=scope.act;vm.createContext(scope);vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{
await scope.refresh();assert.equal(loads.length,0);assert.equal(scope.selectedReport,'old-report');assert(!$('pending-report-update').hidden);
status={running:false,latest_report:'recovered',scan_origin:'recovery'};await scope.refresh();assert.equal(loads.length,0);assert(!$('pending-report-update').hidden);assert.equal(scope.selectedReport,'old-report');
vm.runInContext(html.slice(html.indexOf("window.addEventListener('message'"),html.indexOf('async function loadReports(')),scope);
const frame=$('result-frame');frame.src='http://127.0.0.1:12345/token/center.html';frame.contentWindow={};
for(const event of [{source:{},origin:'http://127.0.0.1:12345',data:{type:'media-rescan'}},{source:frame.contentWindow,origin:'http://evil.test',data:{type:'media-rescan'}},{source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'other'}}])await handler(event);
assert.equal(actions.length,0);await handler({source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-rescan'}});
assert.equal(actions.length,1);assert.equal(actions[0].path,'reports/rescan');assert.equal(actions[0].body.report_id,'old-report');assert(scope.previousRunning);
Object.assign(scope,{historyLoading:false,viewRequest:0,selectedView:'videos',reports:[],request:async()=>({reports:[{id:'new-report',created_at:'generated',summary:{files:4}}]}),document:{createElement:()=>({})},formatTime:x=>x,renderSummary(){},openView:async view=>{assert.equal(view,'videos');return true;},showPane(){throw Error('Must not reset sidebar after opening selected view');}});
$('history').replaceChildren=()=>{};$('history').append=()=>{};
vm.runInContext(html.slice(html.indexOf('async function loadReports('),html.indexOf('function renderStatus(')),scope);
assert(await scope.loadReports(true,true));assert.equal(scope.selectedReport,'new-report');assert.equal(scope.selectedView,'videos');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result=subprocess.run([node,'-e',script,str(Path(__file__).parent)],text=True,encoding='utf-8',capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
