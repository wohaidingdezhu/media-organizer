"""Scheduled scans do not interrupt an existing library or active preview."""
import unittest
import test_compact_browsing as dashboard_tests
import test_workspace_upgrade as workspace_tests
from unittest import mock


class ScheduledRefreshExperienceTests(unittest.TestCase):
    node = dashboard_tests.CompactBrowsingTests.node
    setUp = workspace_tests.WorkspaceTests.setUp
    tearDown = workspace_tests.WorkspaceTests.tearDown

    def test_timer_scan_is_identified_separately_from_manual_scan(self):
        import media_gui
        state = media_gui.DashboardState(self.output)
        self.addCleanup(state.close)
        state.folders = [str(self.source)]
        state.set_monitor({'enabled': True, 'minutes': 1, 'mode': 'timer'})
        with mock.patch.object(state, 'start_scan') as start:
            self.assertTrue(state.monitor_tick(state.next_scan))
            start.assert_called_once_with({})
            self.assertEqual(state.snapshot()['scan_origin'], 'timer')

    def test_timer_completion_keeps_current_view_and_offers_explicit_update(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),loads=[];let status={running:false,latest_report:'new-report',scan_origin:'timer'};
Object.assign(scope,{stopped:false,previousRunning:true,knownLatest:'old-report',selectedReport:'old-report',request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async(...args)=>loads.push(args)});
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{ $('pending-report-update').hidden=true;await scope.refresh();assert.equal(loads.length,0);assert.equal(scope.selectedReport,'old-report');assert(!$('pending-report-update').hidden);
status={running:false,latest_report:'manual-report',scan_origin:'manual'};scope.previousRunning=true;await scope.refresh();assert.deepEqual(loads,[[true,false]]);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_first_load_and_cancelled_timer_do_not_claim_new_results(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),loads=[];let status={running:false,latest_report:'old-report',scan_origin:'timer'};
Object.assign(scope,{stopped:false,previousRunning:true,knownLatest:'old-report',selectedReport:'old-report',request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async(...args)=>loads.push(args)});
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{ $('pending-report-update').hidden=true;await scope.refresh();assert.equal(loads.length,0);assert($('pending-report-update').hidden);scope.knownLatest=undefined;scope.selectedReport='';await scope.refresh();assert.deepEqual(loads,[[false,false]]);})().catch(e=>{console.error(e);process.exitCode=1;});
''')
