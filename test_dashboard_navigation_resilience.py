"""Loading reports must respect navigation and report real open failures."""
import unittest
from unittest import mock
import desktop_entry
import test_compact_browsing as dashboard_tests


class DashboardNavigationResilienceTests(unittest.TestCase):
    node = dashboard_tests.CompactBrowsingTests.node

    def test_native_window_startup_error_is_retried_until_real_catalog_is_ready(self):
        window = mock.Mock()
        window.get_current_url.side_effect = [RuntimeError('Main window not ready'), 'http://127.0.0.1:12345/token/']
        with mock.patch('time.sleep'), mock.patch('time.monotonic', side_effect=[0, 0, 1]), mock.patch.object(desktop_entry, 'native_value', side_effect=[True, True]):
            self.assertTrue(desktop_entry.verify_catalog_ui(window, 'http://127.0.0.1:12345/token/', timeout=10))
        self.assertEqual(window.get_current_url.call_count, 2)
        window.run_js.assert_called_once()

    def test_native_smoke_failure_cleans_up_and_never_claims_success(self):
        window, process, allow_close = mock.Mock(), mock.Mock(), mock.Mock()
        process.poll.return_value = None
        for outcome in (False, RuntimeError('Generated renderer failure')):
            window.reset_mock(); process.reset_mock(); allow_close.reset_mock()
            with mock.patch.object(desktop_entry, 'verify_catalog_ui', side_effect=outcome if isinstance(outcome, Exception) else None, return_value=False), mock.patch.object(desktop_entry, 'request_shutdown') as shutdown:
                self.assertFalse(desktop_entry.verify_smoke_window(window, 'generated', process, allow_close))
            process.terminate.assert_called_once()
            allow_close.set.assert_called_once()
            window.destroy.assert_called_once()
            shutdown.assert_not_called()

    def test_native_smoke_success_requires_real_verification_and_clean_shutdown(self):
        window, process, allow_close = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(desktop_entry, 'verify_catalog_ui', return_value=True), mock.patch.object(desktop_entry, 'request_shutdown') as shutdown:
            self.assertTrue(desktop_entry.verify_smoke_window(window, 'generated', process, allow_close))
        shutdown.assert_called_once_with('generated')
        process.terminate.assert_not_called()
        window.destroy.assert_not_called()

    def test_native_startup_failure_at_deadline_still_fails_verification(self):
        window = mock.Mock()
        window.get_current_url.side_effect = RuntimeError('Generated permanent startup failure')
        with mock.patch('time.sleep'), mock.patch('time.monotonic', side_effect=[0, 0, 2]), mock.patch.object(desktop_entry, 'native_value') as inspect:
            self.assertFalse(desktop_entry.verify_catalog_ui(window, 'generated', timeout=1))
        inspect.assert_not_called()

    def test_native_smoke_shutdown_error_is_a_failure_with_cleanup(self):
        window, process, allow_close = mock.Mock(), mock.Mock(), mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(desktop_entry, 'verify_catalog_ui', return_value=True), mock.patch.object(desktop_entry, 'request_shutdown', side_effect=OSError('Generated shutdown failure')):
            self.assertFalse(desktop_entry.verify_smoke_window(window, 'generated', process, allow_close))
        process.terminate.assert_called_once()
        window.destroy.assert_called_once()

    def test_native_smoke_publishes_success_before_shutdown_closes_ui_loop(self):
        window, process, allow_close = mock.Mock(), mock.Mock(), mock.Mock()
        result = [False]
        def shutdown(_url):
            self.assertTrue(result[0])
        with mock.patch.object(desktop_entry, 'verify_catalog_ui', return_value=True), mock.patch.object(desktop_entry, 'request_shutdown', side_effect=shutdown):
            self.assertTrue(desktop_entry.verify_smoke_window(window, 'generated', process, allow_close, result))

    def test_slow_initial_load_does_not_override_user_navigation(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),opened=[];let finish;
Object.assign(scope,{historyLoading:false,selectedReport:'',selectedView:'home',reports:[],viewRequest:0,document:{createElement:t=>new E(t)},formatTime:x=>x,renderSummary(){},notice(){},openView:async view=>{opened.push(view);return true;},request:async()=>new Promise(resolve=>finish=resolve)});
vm.runInContext(html.slice(html.indexOf('async function loadReports('),html.indexOf('function renderStatus(')),scope);
(async()=>{const loading=scope.loadReports();$('platform-main').setAttribute('data-pane','catalog');finish({reports:[{id:'new',created_at:'2026',summary:{files:1}}]});assert.equal(await loading,false);assert.equal(opened.length,0);assert.equal($('platform-main').getAttribute('data-pane'),'catalog');assert.equal(scope.selectedReport,'new');})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_latest_report_failure_keeps_notification_for_retry(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');let success=false;
Object.assign(scope,{historyLoading:false,selectedReport:'old',selectedView:'videos',reports:[],viewRequest:0,document:{createElement:t=>new E(t)},formatTime:x=>x,renderSummary(){},notice(){},openView:async()=>success,request:async()=>({reports:[{id:'new',created_at:'2026',summary:{files:1}}]})});
vm.runInContext(html.slice(html.indexOf('async function loadReports('),html.indexOf('function renderStatus(')),scope);
(async()=>{$('pending-report-update').hidden=false;assert.equal(await scope.loadReports(true,true),false);assert(!$('pending-report-update').hidden);success=true;assert(await scope.loadReports(true,true));assert($('pending-report-update').hidden);assert.equal(scope.selectedReport,'new');assert.equal(scope.selectedView,'videos');})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_open_view_reports_failure_instead_of_claiming_success(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),errors=[];let failed=true;$('result-viewer').classList.remove=function(name){this.values.delete(name);};E.prototype.removeAttribute=function(key){delete this.attrs[key];};
Object.assign(scope,{URL,selectedReport:'scan-new',selectedView:'home',viewRequest:0,reports:[{id:'scan-new',created_at:'2026'}],descriptions:{home:'Home'},document:{querySelectorAll:()=>[],querySelector:()=>new E('button','Home')},showPane(){},saveSelection(){},formatTime:x=>x,notice:e=>{if(e)errors.push(String(e));},request:async()=>{if(failed)throw Error('unavailable');return {url:'http://127.0.0.1:12345/token/center.html'};}});
vm.runInContext(html.slice(html.indexOf('async function openView('),html.indexOf("$('result-frame').addEventListener('load'")),scope);
(async()=>{assert.equal(await scope.openView('home'),false);assert(errors.some(e=>e.includes('unavailable')));assert($('result-frame').hidden);failed=false;assert.equal(await scope.openView('home'),true);assert(!$('result-frame').hidden);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_native_smoke_waits_for_actual_catalog_and_times_out_on_failure(self):
        window = mock.Mock()
        window.get_current_url.return_value = 'http://127.0.0.1:12345/token/'
        with mock.patch('time.sleep'), mock.patch('time.monotonic', side_effect=[0, 0, 1]), \
             mock.patch.object(desktop_entry, 'native_value', side_effect=[True, False, True]):
            self.assertTrue(desktop_entry.verify_catalog_ui(window, 'http://127.0.0.1:12345/token/', timeout=10))
        window.run_js.assert_called_once()
        window.reset_mock()
        with mock.patch('time.sleep'), mock.patch('time.monotonic', side_effect=[0, 0, 2]), \
             mock.patch.object(desktop_entry, 'native_value', return_value=False):
            self.assertFalse(desktop_entry.verify_catalog_ui(window, 'http://127.0.0.1:12345/token/', timeout=1))
        window.run_js.assert_not_called()
