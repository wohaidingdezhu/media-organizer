"""Background work and batch buttons preserve the user's current screen."""
import unittest

import test_compact_browsing as dashboard_tests
import test_media_center as center_tests


class BrowseScanContinuityTests(unittest.TestCase):
    node = dashboard_tests.CompactBrowsingTests.node
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_partial_scan_and_cancel_offer_refresh_without_interrupting_selection(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');let loads=0,status={running:true,latest_report:'old',pending_library:'discovered'};
Object.assign(scope,{stopped:false,scanControls:{epoch:0},previousRunning:true,knownLatest:'old',pendingLibrarySeen:'',selectedReport:'all',request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async()=>{loads++;}});
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{$('platform-main').setAttribute('data-pane','results');await scope.refresh();assert.equal(loads,0);assert(!$('pending-report-update').hidden);assert($('pending-report-update').textContent.includes('先浏览'));
$('pending-report-update').hidden=true;status={running:false,latest_report:'old',pending_library:''};await scope.refresh();assert.equal(loads,0);assert(!$('pending-report-update').hidden);assert($('pending-report-update').textContent.includes('刷新'));assert.equal(scope.selectedReport,'all');})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_manual_completion_and_cancel_keep_existing_selection_and_iframe(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),loads=[];let status={running:false,latest_report:'new',scan_origin:'manual'};
Object.assign(scope,{stopped:false,scanControls:{epoch:0},previousRunning:true,knownLatest:'old',selectedReport:'old',request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async(...args)=>{loads.push(args);return true;}});
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{for(const pane of ['results','settings','catalog']){scope.knownLatest='old';scope.previousRunning=true;$('platform-main').setAttribute('data-pane',pane);$('pending-report-update').hidden=true;await scope.refresh();assert.equal(loads.length,0);assert.equal(scope.selectedReport,'old');assert(!$('pending-report-update').hidden);}
status.latest_report='old';scope.knownLatest='old';scope.previousRunning=true;$('pending-report-update').hidden=true;await scope.refresh();assert.equal(loads.length,0);assert($('pending-report-update').hidden);
status.latest_report='new';$('platform-main').setAttribute('data-pane','scan');scope.previousRunning=true;await scope.refresh();assert.deepEqual(loads,[[true,false]]);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_live_batches_do_not_offer_reopening_the_already_updating_page(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');let status={running:true,latest_report:'old',pending_library:'batch-1'},loads=0;
Object.assign(scope,{stopped:false,scanControls:{epoch:0},previousRunning:true,knownLatest:'old',pendingLibrarySeen:'',selectedReport:'all',request:async()=>status,renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async()=>{loads++;}});
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{$('platform-main').setAttribute('data-pane','results');$('result-frame').hidden=false;$('result-frame').setAttribute('data-report-id','all');await scope.refresh();assert($('pending-report-update').hidden);status.pending_library='batch-2';await scope.refresh();assert($('pending-report-update').hidden);assert.equal(loads,0);
status={running:false,latest_report:'new',pending_library:'',scan_origin:'manual'};await scope.refresh();assert(!$('pending-report-update').hidden);assert($('pending-report-update').textContent.includes('查看新结果'));assert.equal(loads,0);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_failed_or_superseded_completion_load_can_be_retried(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');let loads=0;
Object.assign(scope,{stopped:false,scanControls:{epoch:0},previousRunning:true,knownLatest:'old',selectedReport:'old',request:async()=>({running:false,latest_report:'new',scan_origin:'manual'}),renderStatus(){},notice(){},setTimeout(){return 1;},loadReports:async()=>{loads++;return false;}});
$('platform-main').setAttribute('data-pane','scan');$('pending-report-update').hidden=true;
vm.runInContext(html.slice(html.indexOf('async function refresh()'),html.indexOf('async function act(')),scope);
(async()=>{await scope.refresh();assert.equal(loads,1);assert(!$('pending-report-update').hidden);await scope.refresh();assert.equal(loads,1);assert(!$('pending-report-update').hidden);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_late_report_response_cannot_replace_current_report_identity(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8');let finish;const old=[{id:'old'}],opened=[];
Object.assign(scope,{historyLoading:false,selectedReport:'old',selectedView:'photos',reports:old,viewRequest:0,document:{createElement:t=>new E(t)},formatTime:x=>x,renderSummary(){},notice(){},openView:async view=>{opened.push(view);return true;},request:async()=>new Promise(resolve=>finish=resolve)});
vm.runInContext(html.slice(html.indexOf('async function loadReports('),html.indexOf('function renderStatus(')),scope);
(async()=>{$('platform-main').setAttribute('data-pane','scan');const loading=scope.loadReports(true);$('platform-main').setAttribute('data-pane','results');finish({reports:[{id:'new',created_at:'2026',summary:{files:1}}]});assert.equal(await loading,false);assert.equal(scope.selectedReport,'old');assert.equal(scope.selectedView,'photos');assert.equal(scope.reports,old);assert.equal(opened.length,0);assert(!scope.historyLoading);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_select_page_and_clear_preserve_cards_and_cross_page_selection(self):
        self.run_ui(r'''
items=Array.from({length:210},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.files[0].id]));
location.hash='#photos';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();const wall=()=>$('center-content').children.find(n=>n.className.includes('photo-wall')),initial=wall();let filtered=0;const filter=scope.centerFilter;scope.centerFilter=(...args)=>{filtered++;return filter(...args);};
$('select-page').onclick();assert.equal(wall(),initial);assert.equal(filtered,0);assert($('selected-count').textContent.includes('48 项'));$('select-page').onclick();assert.equal(filtered,0);assert($('selected-count').textContent.includes('48 项'));
for(let p=0;p<4;p++){$('center-next').onclick();const before=wall(),count=filtered;$('select-page').onclick();assert.equal(wall(),before);assert.equal(filtered,count);}
assert($('selected-count').textContent.includes('200 项'));assert($('center-error').textContent.includes('上限'));const before=wall(),count=filtered;$('clear-selection').onclick();assert.equal(wall(),before);assert.equal(filtered,count);assert($('selected-count').textContent.includes('0 项'));assert(!$('center-error').textContent.includes('上限'));assert.equal(operations.length,0);
''')


if __name__ == '__main__':
    unittest.main()
