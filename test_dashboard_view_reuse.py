"""Repeated navigation preserves loaded media and coalesces slow library opens."""
import unittest
import test_compact_browsing
import test_media_center

SETUP = r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),tabs=['home','photos','videos','library','organize'].map(view=>{const n=new E('button',view);n.dataset={view};return n;});
E.prototype.removeAttribute=function(key){delete this.attrs[key];};$('result-viewer').classList.remove=function(name){this.values.delete(name);};
let opens=0,srcWrites=0,src='',pending=[];const frame=$('result-frame');Object.defineProperty(frame,'src',{get:()=>src,set:value=>{src=value;srcWrites++;}});
Object.assign(scope,{URL,selectedReport:'all',selectedView:'photos',viewRequest:0,reports:[{id:'all',created_at:'2026'},{id:'scan-old',created_at:'2025'}],descriptions:{home:'Home',photos:'Photos',videos:'Videos',library:'Movies',organize:'Files'},document:{querySelectorAll:()=>tabs,querySelector:selector=>tabs.find(tab=>selector.includes('"'+tab.dataset.view+'"'))},showPane(){},saveSelection(){},renderSummary(){},formatTime:x=>x,notice(){},request:async()=>{opens++;return new Promise(resolve=>pending.push(resolve));}});
vm.runInContext(html.slice(html.indexOf('async function openView('),html.indexOf("$('result-frame').addEventListener('load'")),scope);
const complete=(n=0)=>pending[n]({url:'http://127.0.0.1:12345/token/center.html#photos'});
'''


class DashboardViewReuseTests(unittest.TestCase):
    node = test_compact_browsing.CompactBrowsingTests.node

    def test_duplicate_clicks_open_once_and_loaded_current_view_never_reloads(self):
        self.node(SETUP + r'''
(async()=>{const first=scope.openView('photos'),second=scope.openView('photos');assert.equal(opens,1);assert.equal(tabs.find(tab=>tab.dataset.view==='photos').getAttribute('aria-pressed'),'true');complete();await Promise.all([first,second]);assert.equal(srcWrites,1);
scope.request=async()=>{opens++;throw Error('Unexpected reload');};frame.savedSelection=['generated-1','generated-2'];$('result-viewer').classList.add('overlay-mode');assert.equal(await scope.openView('photos'),true);assert.equal(opens,1);assert.equal(srcWrites,1);assert.equal(frame.savedSelection.length,2);assert($('result-viewer').classList.contains('overlay-mode'));
assert.equal(await scope.openView('videos'),true);assert.equal(opens,1);assert.equal(srcWrites,2);assert(frame.src.endsWith('#videos'));assert.equal(scope.selectedView,'videos');assert($('standalone').href.endsWith('#videos'));
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_explicit_refresh_and_different_scan_request_fresh_results(self):
        self.node(SETUP + r'''
(async()=>{let first=scope.openView('photos');complete();await first;
let refresh=scope.openView('photos','',true);assert.equal(opens,2);complete(1);await refresh;assert.equal(srcWrites,2);
scope.selectedReport='scan-old';let changed=scope.openView('photos');assert.equal(opens,3);complete(2);await changed;assert.equal(frame.getAttribute('data-report-id'),'scan-old');
let detail=scope.openView('photos','a'.repeat(64));assert.equal(opens,4);complete(3);await detail;assert(new URL(frame.src).searchParams.get('open_file')==='a'.repeat(64));
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_late_response_and_failed_request_cannot_replace_new_navigation(self):
        self.node(SETUP + r'''
(async()=>{let photos=scope.openView('photos'),videos=scope.openView('videos');assert.equal(opens,2);pending[1]({url:'http://127.0.0.1:12345/new/center.html#videos'});await videos;complete();await photos;assert(frame.src.includes('/new/'));assert.equal(scope.selectedView,'videos');
scope.request=async()=>{opens++;throw Error('Generated offline');};assert.equal(await scope.openView('videos','',true),false);
scope.request=async()=>{opens++;return {url:'http://127.0.0.1:12345/retry/center.html#videos'};};assert.equal(await scope.openView('videos'),true);assert(frame.src.includes('/retry/'));
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_unified_rescan_explains_scope_choice_before_any_scan_starts(self):
        test_media_center.MediaCenterTests.run_ui(self, r'''
messages.length=0;$('center-rescan').onclick();assert.equal(messages[0].type,'media-rescan');assert($('center-error').textContent.includes('选择'));assert(!$('center-error').textContent.includes('已请求重新扫描'));assert.equal(operations.length,0);
''', setup=r'''
const messages=[];scope.URL=URL;scope.window.parent={postMessage:value=>messages.push(value)};scope.document.querySelector=selector=>selector.startsWith('nav[')?{href:'http://127.0.0.1:12345/dashboard/'}:null;
const original=scope.fetch;scope.fetch=async(path,options)=>{const response=await original(path,options);const value=await response.json();if(path==='api/center')value.unified_library=true;return {...response,json:async()=>value};};
''')


if __name__ == '__main__':
    unittest.main()
