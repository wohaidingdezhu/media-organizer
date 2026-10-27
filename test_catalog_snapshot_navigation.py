"""Snapshots opened from the long-term index keep the active report consistent."""
import unittest
import test_compact_browsing as dashboard_tests


class CatalogSnapshotNavigationTests(unittest.TestCase):
    node = dashboard_tests.CompactBrowsingTests.node

    def test_snapshot_sets_report_and_view_including_older_than_history_limit(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),opened=[],notes=[],reports=[{id:'scan-new'}],old={id:'scan-old',created_at:'2026-01-01',summary:{files:2},roots:[]};
Object.assign(scope,{reports,selectedReport:'scan-new',viewRequest:0,document:{createElement:t=>new E(t)},formatTime:()=> '2026/01/01',notice:error=>notes.push(error),renderSummary(){assert.equal(scope.selectedReport,'scan-old');},openView:async view=>opened.push({view,report:scope.selectedReport}),request:async(path,body)=>({report_id:body.report_id,report:old})});
vm.runInContext(html.slice(html.indexOf('async function openCatalogSnapshot('),html.indexOf('async function loadCatalog(')),scope);
(async()=>{await scope.openCatalogSnapshot({report_id:'scan-old',kind:'照片'});assert.equal(scope.selectedReport,'scan-old');assert.equal(opened[0].view,'photos');assert.equal(opened[0].report,'scan-old');assert.equal(reports.length,2);assert.equal($('history').children[0].value,'scan-old');assert(!$('result-content').hidden);await scope.openCatalogSnapshot({report_id:'scan-old',kind:'视频'});assert.equal(opened[1].view,'library');assert.equal(reports.length,2);assert.equal($('history').children.length,1);assert.equal(requests.length,0);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_failed_or_late_snapshot_does_not_replace_active_report(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),errors=[],opened=[];let finish;
Object.assign(scope,{reports:[{id:'scan-new'}],selectedReport:'scan-new',viewRequest:0,document:{createElement:t=>new E(t)},formatTime:()=>'',notice:error=>{if(error)errors.push(String(error));},renderSummary(){},openView:async view=>opened.push(view),request:async()=>new Promise(resolve=>finish=resolve)});
vm.runInContext(html.slice(html.indexOf('async function openCatalogSnapshot('),html.indexOf('async function loadCatalog(')),scope);
(async()=>{const pending=scope.openCatalogSnapshot({report_id:'scan-old',kind:'照片'});$('catalog-pane').hidden=true;finish({report_id:'scan-old',report:{id:'scan-old',summary:{files:1}}});await pending;assert.equal(scope.selectedReport,'scan-new');assert.equal(opened.length,0);$('catalog-pane').hidden=false;const bad=scope.openCatalogSnapshot({report_id:'scan-old',kind:'照片'});finish({report_id:'wrong',report:{id:'wrong',summary:{files:1}}});await bad;assert.equal(scope.selectedReport,'scan-new');assert.equal(opened.length,0);assert(errors.some(e=>e.includes('不一致')));})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_latest_snapshot_click_wins_if_requests_finish_out_of_order(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),pending={},opened=[];Object.assign(scope,{reports:[{id:'scan-new'}],selectedReport:'scan-new',viewRequest:0,document:{createElement:t=>new E(t)},formatTime:()=>'',notice(){},renderSummary(){},openView:async view=>opened.push(view),request:async(path,body)=>new Promise(resolve=>pending[body.report_id]=resolve)});
vm.runInContext(html.slice(html.indexOf('async function openCatalogSnapshot('),html.indexOf('async function loadCatalog(')),scope);
(async()=>{const first=scope.openCatalogSnapshot({report_id:'scan-a',kind:'照片'}),second=scope.openCatalogSnapshot({report_id:'scan-b',kind:'视频'});pending['scan-b']({report_id:'scan-b',report:{id:'scan-b',summary:{files:1}}});await second;pending['scan-a']({report_id:'scan-a',report:{id:'scan-a',summary:{files:1}}});await first;assert.equal(scope.selectedReport,'scan-b');assert.deepEqual(opened,['library']);assert(!scope.reports.some(report=>report.id==='scan-a'));})().catch(e=>{console.error(e);process.exitCode=1;});
''')
