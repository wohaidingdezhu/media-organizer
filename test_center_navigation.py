# coding: utf-8
"""Navigation follows the visible view and preserves positions without media writes."""
import unittest
import test_media_center as center_tests
import test_compact_browsing as dashboard_tests


class CenterNavigationTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui
    node = dashboard_tests.CompactBrowsingTests.node

    def test_next_page_starts_at_top_and_keeps_batch_selection(self):
        self.run_ui(r'''
items=Array.from({length:60},(_,n)=>({...photo,id:String(n).padStart(64,'0'),title:'Photo '+n,mtime:n,files:[{...photo.files[0],id:String(n).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();scope.window.scrollY=1800;$('center-next').onclick();assert.equal(scope.window.scrollY,0);assert($('center-page').textContent.includes('2/2'));assert($('selected-count').textContent.includes('48'));scope.window.scrollY=900;$('center-prev').onclick();assert.equal(scope.window.scrollY,0);assert($('center-page').textContent.includes('1/2'));assert.equal(operations.length,0);
''')

    def test_return_from_directory_restores_parent_page_and_scroll(self):
        self.run_ui(r'''
directories={};for(let n=0;n<61;n++)directories['/sample'+n]={name:'sample'+n,parent:'',children:[],ids:[photo.id]};location.hash='#resources';events.hashchange();await $('center-refresh').onclick();scope.window.scrollY=1800;$('center-content').children.find(n=>n.className==='pager').children[2].onclick();assert.equal(scope.window.scrollY,0);scope.window.scrollY=640;$('center-content').children[0].children[0].onclick();assert.equal(scope.window.scrollY,0);$('center-breadcrumb').children[0].onclick();assert.equal(scope.window.scrollY,640);assert($('center-content').children.find(n=>n.className==='pager').children[1].textContent.includes('2/2'));assert.equal($('center-content').children[0].children.length,13);assert.equal(operations.length,0);
''')

    def test_return_from_album_restores_album_page_and_respects_changed_filters(self):
        self.run_ui(r'''
folders={};for(let n=0;n<61;n++)folders['/album'+n]=[photo.id];location.hash='#collections';events.hashchange();await $('center-refresh').onclick();$('center-next').onclick();scope.window.scrollY=700;$('center-content').children[0].children[0].onclick();assert.equal(scope.window.scrollY,0);$('center-breadcrumb').children[0].onclick();assert($('center-page').textContent.includes('2/2'));assert.equal(scope.window.scrollY,700);$('center-content').children[0].children[0].onclick();$('media-order').value='name';$('media-order').onchange();$('center-breadcrumb').children[0].onclick();assert($('center-page').textContent.includes('1/2'));assert.equal(scope.window.scrollY,0);assert.equal(operations.length,0);
''')

    def test_home_album_and_folder_tag_announce_actual_view_without_reloading(self):
        self.run_ui(r'''
location.hash='#home';events.hashchange();const albumGrid=$('center-content').children.at(-1);albumGrid.children[0].onclick();assert.equal(location.hash,'#collections');assert.equal(scope.messages.at(-1).data.view,'collections');assert.equal(scope.messages.at(-1).data.type,'media-context');assert.equal(scope.messages.at(-1).origin,'http://127.0.0.1:12345');assert(!$('center-breadcrumb').hidden);
state.folder_labels={'/generated':'Person'};photo.tags=['Person'];directories={'/generated':{name:'generated',parent:'',children:[],ids:[photo.id]}};location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();$('center-breadcrumb').children.find(n=>n.textContent==='查看标签：Person').onclick();assert.equal(location.hash,'#tags');assert.equal(scope.messages.at(-1).data.view,'tags');assert($('center-subtitle').textContent.includes('Person'));$('center-breadcrumb').children[0].onclick();assert.equal($('center-title').textContent,'标签与分类');assert.equal(operations.length,0);
''', setup=r'''
scope.messages=[];scope.URL=URL;scope.window.parent={postMessage:(data,origin)=>scope.messages.push({data,origin})};scope.window.history={replaceState:(state,title,url)=>{location.hash=url;}};scope.document.querySelector=()=>({href:'http://127.0.0.1:12345/dashboard/'});
''')

    def test_only_active_report_context_updates_sidebar_without_reloading_frame(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),events={},tabs=['home','collections','tags','library'].map(view=>{const n=new E('button',view);n.dataset={view};return n;});E.prototype.removeAttribute=function(key){delete this.attrs[key];};let saved=0,opened=0;Object.assign(scope,{URL,window:{addEventListener:(name,handler)=>events[name]=handler},selectedReport:'scan-generated',selectedView:'home',reports:[{id:'scan-generated',created_at:'generated'}],descriptions:{home:'Home',collections:'Albums',tags:'Tags',library:'Movies'},saveSelection(){saved++;},formatTime:x=>x,openView(){opened++;},document:{querySelectorAll:()=>tabs,querySelector:selector=>tabs.find(t=>selector.includes('"'+t.dataset.view+'"'))}});const frame=$('result-frame');frame.src='http://127.0.0.1:12345/token/center.html#home';frame.contentWindow={};$('result-pane').hidden=false;
vm.runInContext(html.slice(html.indexOf('function adoptCenterView('),html.indexOf('async function loadReports')),scope);
(async()=>{const event={source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-context',view:'collections'}};await events.message({...event,source:{}});await events.message({...event,origin:'https://foreign.invalid'});await events.message({...event,data:{type:'media-context',view:'unknown'}});assert.equal(saved,0);frame.hidden=true;await events.message(event);assert.equal(saved,0);frame.hidden=false;await events.message(event);assert.equal(scope.selectedView,'collections');assert.equal(tabs.find(t=>t.dataset.view==='collections').attrs['aria-pressed'],'true');assert.equal(tabs.find(t=>t.dataset.view==='home').attrs['aria-pressed'],'false');assert.equal(frame.src,'http://127.0.0.1:12345/token/center.html#home');assert($('standalone').href.endsWith('#collections'));assert.equal(opened,0);$('result-pane').hidden=true;await events.message({...event,data:{type:'media-context',view:'tags'}});assert.equal(scope.selectedView,'tags');assert($('result-pane').hidden);assert.equal(tabs.find(t=>t.dataset.view==='collections').attrs['aria-pressed'],'true');assert.equal(requests.length,0);})().catch(e=>{console.error(e);process.exitCode=1;});
''')
