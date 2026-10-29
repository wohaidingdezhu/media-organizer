"""Slow dashboard requests stay explicit and cancellation is idempotent."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import media_gui
import test_compact_browsing


class ScanRequestFeedbackTests(unittest.TestCase):
    node = test_compact_browsing.CompactBrowsingTests.node

    def dashboard(self, body):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');
E.prototype.removeAttribute=function(k){delete this.attrs[k];};
const initial={folders:['C:\\generated'],running:false,cancel_requested:false,status:'ready',logs:[],monitor:{enabled:false},progress:null};let replies=[],resolveAction,fail=false;
Object.assign(scope,{stopped:false,previousRunning:false,knownLatest:'old',selectedReport:'old',folderSelection:new Map(),lastFolders:'',lastLog:'',monitorEditing:false,document:{activeElement:null,createElement:t=>new E(t)},notice(e){$('notice').textContent=e?String(e):'';},request:async(path,body)=>{requests.push({path,body});if(path==='status')return replies.shift()||initial;return new Promise(resolve=>resolveAction=resolve);},setTimeout(){return 1;},loadReports:async()=>true});
vm.runInContext(html.slice(html.indexOf('function syncScanScope('),html.indexOf('async function beginFirstScan(')),scope);
vm.runInContext(html.slice(html.indexOf("$('add-folder').onclick="),html.indexOf("$('quit').onclick=")),scope);
scope.renderStatus(initial);
(async()=>{
''' + body + r'''
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_slow_start_ignores_repeat_clicks_and_poll_cannot_unlock_controls(self):
        self.dashboard(r'''
const job=$('scan').onclick();assert($('scan').disabled);assert($('scan').textContent.includes('正在提交'));assert($('add-folder').disabled);assert($('images').disabled);scope.renderStatus(initial);assert($('scan').disabled);assert($('folders').children[0].children[0].children[0].disabled);
await $('scan').onclick();await $('add-folder').onclick();assert.equal(requests.length,1);assert.deepEqual(Array.from(requests[0].body.scan_folders),initial.folders);
resolveAction({...initial,running:true});await job;assert($('scan').disabled);assert(!$('cancel').disabled);assert.equal($('scan').textContent,'开始只读扫描');
''')

    def test_folder_picker_remains_single_and_unlocks_after_cancel(self):
        self.dashboard(r'''
const job=$('add-folder').onclick();assert($('add-folder').disabled);assert($('status-text').textContent.includes('系统窗口'));scope.renderStatus(initial);await $('add-folder').onclick();await $('scan').onclick();assert.equal(requests.length,1);
resolveAction(initial);await job;assert(!$('add-folder').disabled);assert(!$('scan').disabled);assert(!$('images').disabled);assert.equal($('add-folder').textContent,'＋ 添加文件夹…');
''')

    def test_cancellation_survives_progress_poll_and_finishes_cleanly(self):
        self.dashboard(r'''
const running={...initial,running:true,progress:{stage:'photos',label:'解析照片',total:100,completed:50,percent:50}};scope.renderStatus(running);
const job=$('cancel').onclick();assert($('cancel').disabled);assert($('cancel').textContent.includes('请求'));await $('cancel').onclick();assert.equal(requests.length,1);
resolveAction({...running,cancel_requested:true});await job;scope.renderStatus({...running,cancel_requested:true,progress:{...running.progress,completed:51,percent:51}});assert($('cancel').disabled);assert($('status-text').textContent.includes('正在取消'));assert($('scan-progress-label').textContent.includes('正在取消'));await $('cancel').onclick();assert.equal(requests.length,1);
scope.renderStatus({...initial,status:'扫描已取消'});assert(!$('scan').disabled);assert($('cancel').disabled);assert.equal($('cancel').textContent,'取消扫描');assert($('scan-banner').hidden);
''')

    def test_failed_request_recovers_controls_and_keeps_error(self):
        self.dashboard(r'''
const request=scope.request;scope.request=async(path,body)=>{if(path==='scan/start')throw Error('network failed');return request(path,body);};await $('scan').onclick();assert(!$('scan').disabled);assert(!$('add-folder').disabled);assert($('notice').textContent.includes('network failed'));assert.equal($('scan').textContent,'开始只读扫描');
''')

    def test_poll_started_before_scan_request_cannot_restore_idle_state(self):
        self.dashboard(r'''
let statusResponse;const request=scope.request;scope.request=(path,body)=>path==='status'?new Promise(resolve=>statusResponse=resolve):request(path,body);
const poll=scope.refresh(),job=$('scan').onclick();resolveAction({...initial,running:true});await job;statusResponse(initial);await poll;assert($('scan').disabled);assert(!$('cancel').disabled);assert(scope.previousRunning);
''')

    def test_old_poll_error_does_not_hide_successful_scan_start(self):
        self.dashboard(r'''
let rejectStatus;const request=scope.request;scope.request=(path,body)=>path==='status'?new Promise((resolve,reject)=>rejectStatus=reject):request(path,body);
const poll=scope.refresh(),job=$('scan').onclick();resolveAction({...initial,running:true});await job;rejectStatus(Error('old connection'));await poll;assert(!$('notice').textContent);assert($('scan').disabled);
''')

    def test_cancel_signals_once_and_reports_state_until_worker_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = media_gui.DashboardState(Path(temporary) / 'reports')
            self.addCleanup(state.close)
            state.running = True
            state.process = mock.Mock()
            state.process.poll.return_value = None
            with mock.patch.object(state, '_signal_scan') as signal:
                self.assertTrue(state.cancel_scan()['cancel_requested'])
                state.log('MEDIA sample processing')
                self.assertTrue(state.cancel_scan()['cancel_requested'])
                signal.assert_called_once_with(state.process)
                state.running = False
                state.process = None
                self.assertFalse(state.snapshot()['cancel_requested'])
                state.cancel_scan()
                self.assertEqual(signal.call_count, 1)

    def test_failed_cancel_signal_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = media_gui.DashboardState(Path(temporary) / 'reports')
            self.addCleanup(state.close)
            state.running = True
            state.process = mock.Mock()
            state.process.poll.return_value = None
            with mock.patch.object(state, '_signal_scan', side_effect=[OSError('unavailable'), None]) as signal:
                with self.assertRaises(OSError):
                    state.cancel_scan()
                self.assertFalse(state.snapshot()['cancel_requested'])
                self.assertTrue(state.cancel_scan()['cancel_requested'])
                self.assertEqual(signal.call_count, 2)
                state.running = False
                state.process = None


if __name__ == '__main__':
    unittest.main()
