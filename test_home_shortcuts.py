"""Home statistics are keyboard-accessible navigation, including embedded use."""
import unittest
import test_media_center as center_tests
import test_compact_browsing as dashboard_tests


class HomeShortcutTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui
    node = dashboard_tests.CompactBrowsingTests.node

    def test_statistics_open_corresponding_libraries_without_media_operations(self):
        self.run_ui(r'''
for(const [label,target] of [['电影','movies'],['视频','videos'],['照片','photos'],['收藏','favorites']]){
location.hash='#home';events.hashchange();const card=$('center-content').children[0].children.find(node=>node.attrs['aria-label']==='查看'+label);assert(card);assert.equal(card.tagName,'BUTTON');assert(card.children.some(node=>node.textContent==='查看'+label+' →'));card.onclick();assert.equal(location.hash,'#'+target);assert.equal($('center-title').textContent,label);
}
assert.equal(operations.length,0);assert.equal(calls.length,0);
''')

    def test_embedded_shortcut_requests_parent_navigation_with_expected_origin(self):
        self.run_ui(r'''
location.hash='#home';events.hashchange();$('center-content').children[0].children.find(node=>node.attrs['aria-label']==='查看照片').onclick();const last=scope.messages.at(-1);assert.equal(last.data.type,'media-navigate');assert.equal(last.data.view,'photos');assert.equal(last.origin,'http://127.0.0.1:12345');assert.equal(operations.length,0);
''', setup=r'''
scope.messages=[];scope.URL=URL;scope.window.parent={postMessage:(data,origin)=>scope.messages.push({data,origin})};scope.window.history={replaceState:(state,title,url)=>{location.hash=url;}};scope.document.querySelector=()=>({href:'http://127.0.0.1:12345/dashboard/'});
''')

    def test_dashboard_accepts_all_four_shortcuts_only_from_its_current_frame(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),events={},opened=[];
Object.assign(scope,{URL,window:{addEventListener:(name,handler)=>events[name]=handler},selectedReport:'scan-generated',selectedView:'home',reports:[],descriptions:{},saveSelection(){},formatTime:x=>x,openView:async view=>opened.push(view),document:{querySelectorAll:()=>[],querySelector:()=>null}});
const frame=$('result-frame');frame.src='http://127.0.0.1:12345/token/center.html#home';frame.contentWindow={};$('result-pane').hidden=false;
vm.runInContext(html.slice(html.indexOf('function adoptCenterView('),html.indexOf('async function loadReports')),scope);
(async()=>{for(const [view,expected] of [['movies','library'],['videos','videos'],['photos','photos'],['favorites','favorites']]){await events.message({source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-navigate',view}});assert.equal(opened.at(-1),expected);}const before=opened.length;await events.message({source:{},origin:'http://127.0.0.1:12345',data:{type:'media-navigate',view:'photos'}});await events.message({source:frame.contentWindow,origin:'https://foreign.invalid',data:{type:'media-navigate',view:'photos'}});await events.message({source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-navigate',view:'other'}});assert.equal(opened.length,before);})().catch(e=>{console.error(e);process.exitCode=1;});
''')
