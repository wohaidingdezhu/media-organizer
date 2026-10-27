# coding: utf-8
"""Shared UI behavior: direct cleanup previews never execute or replace batch picks."""
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const root=process.argv[1];
class E{
 constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.attrs={};this.value='';this.hidden=false;this.open=false;this.style={};this.events={};this.classList={values:new Set(),contains(n){return this.values.has(n);},add(n){this.values.add(n);},toggle(n,v){if(v)this.values.add(n);else this.values.delete(n);}};}
 append(...x){this.children.push(...x);}prepend(...x){this.children.unshift(...x);}replaceChildren(...x){this.children=x;}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}addEventListener(k,v){this.events[k]=v;}
 showModal(){this.open=true;}close(){this.open=false;this.events.close?.();}remove(){this.removed=true;}
 querySelectorAll(){return [];}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};
const a={id:'a'.repeat(64),name:'a.jpg',path:'C:\\generated\\a.jpg',kind:'照片',bytes:12},b={...a,id:'b'.repeat(64),name:'b.jpg',path:'/generated/b.jpg'};
let requests=[],failure=false,renders=0;
const scope={$,make:(t,c,s)=>new E(t,s||''),data:{items:[a,b]},mediaAvailable:true,mediaIds:{[a.path]:a.id,[b.path]:b.id},handledTrash:new Set(),selected:new Set([b.id]),saving:false,operating:false,executionPreview:null,quickIssues:[],fileSize:n=>n+' B',render(){renders++;},error(message){$('error').textContent=message||'';$('error').hidden=!message;},api:async(route,body)=>{requests.push({route,body});if(failure)throw Error('至少保留一份');return {mode:body.mode,items:[{path:a.path,target:'系统废纸篓 / 回收站'}],bytes:12};}};
vm.createContext(scope);
const management=fs.readFileSync(root+'/management.js','utf8');
vm.runInContext(management.slice(0,management.indexOf('function appendOperationStatus')),scope);
vm.runInContext(management.slice(management.indexOf('let executionPage='),management.indexOf("$('copy-files').onclick")),scope);
'''


class DirectCleanupTests(unittest.TestCase):
    def node(self, code):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Both platform CI jobs install Node for UI behavior tests')
        result = subprocess.run([node, '-e', HARNESS+code, str(Path(__file__).parent)],
                                text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_delete_opens_only_clicked_preview_preserves_batch_and_requires_confirmation(self):
        self.node(r'''
(async()=>{
 const host=new E();scope.appendTrashButton(a,host);assert(!host.children[0].disabled);
 await host.children[0].onclick();assert.equal(requests.length,1);assert.equal(requests[0].route,'api/operations/preview');
 assert.deepEqual(Array.from(requests[0].body.ids),[a.id]);assert.equal(requests[0].body.mode,'trash');
 assert.deepEqual(Array.from(scope.selected),[b.id]);assert($('execution').open);assert.equal($('execution-confirm').checked,false);assert($('execution-start').disabled);
 assert($('execution-page').textContent.includes('待清理'));assert(!$('execution-help').textContent.includes('复制'));
 $('execution').close();assert.equal(requests.length,1,'cancelling must never start an operation');
 scope.operating=true;await scope.previewTrashItem(a);assert.equal(requests.length,1);
 scope.operating=false;scope.handledTrash.add(a.path);await scope.previewTrashItem(a);assert.equal(requests.length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_preview_refusal_is_visible_inside_comparison_without_clearing_selection(self):
        self.node(r'''
(async()=>{
 failure=true;$('picture-viewer').open=true;await scope.previewTrashItem(a);
 assert(!$('execution').open);assert(!$('picture-error').hidden);assert($('picture-error').textContent.includes('至少保留一份'));
 assert.deepEqual(Array.from(scope.selected),[b.id]);assert.equal(requests.length,1);
 scope.mediaAvailable=false;await scope.previewTrashItem(b);assert.equal(requests.length,1);
 const host=new E();scope.appendTrashButton(b,host);assert(host.children[0].disabled);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_legacy_report_adds_in_place_buttons_with_ids_and_comparison_without_posting(self):
        self.node(r'''
(async()=>{
 const first=new E('li',a.path),second=new E('li',b.path),group=new E();group.querySelectorAll=()=>[first,second];
 const body=new E(),document={body,createElement:t=>new E(t),querySelector:()=>group,querySelectorAll:selector=>selector==='#duplicates details'?[group]:[]};
 Object.assign(scope,{document,fetch:async()=>({ok:true,json:async()=>({available:true,ids_by_path:scope.mediaIds})})});
 await vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);
 assert.equal(first.children[0].textContent,'删除…');assert.equal(group.children[0].textContent,'对比这两张');
 let deleted=[];scope.reportTrashOpen=ids=>deleted.push(ids);first.children[0].onclick();assert.deepEqual(Array.from(deleted[0]),[a.id]);assert.equal(requests.length,0);let compared=[];scope.reportActionOpen=(action,ids)=>compared.push({action,ids});group.children[0].onclick();assert.equal(compared[0].action,'compare');assert.deepEqual(Array.from(compared[0].ids),[a.id,b.id]);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_single_confirmation_cancels_without_start_and_completes_own_job(self):
        self.node(r"""
(async()=>{
let refreshed=0,jobStatus='running',poll;const body=new E();Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},setTimeout(fn){poll=fn;return 1;},fetch:async(path,options)=>{requests.push({path,body:options?.body&&JSON.parse(options.body)});return {ok:true,json:async()=>path.endsWith('/preview')?{token:'safe-preview',items:[{path:a.path}]}:path.endsWith('/start')?{id:'a'.repeat(24)}:{id:'a'.repeat(24),mode:'trash',status:jobStatus,items:[]}};}});
vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);
scope.reportActionOpen('delete',[a.id],()=>refreshed++);await new Promise(setImmediate);let dialog=body.children[0],buttons=dialog.children.at(-1).children;assert(dialog.open);assert(!dialog.children.some(n=>n.tagName==='IFRAME'));assert(!buttons[2].disabled);buttons[0].onclick();assert(dialog.removed);assert.equal(refreshed,1);assert.equal(requests.length,1);assert(requests[0].path.endsWith('/preview'));
scope.reportActionOpen('delete',[a.id],()=>refreshed++);await new Promise(setImmediate);dialog=body.children[1];buttons=dialog.children.at(-1).children;await buttons[2].onclick();await buttons[2].onclick();assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,1);let prevented=false;dialog.events.cancel({preventDefault(){prevented=true;}});assert(prevented);buttons[0].onclick();assert(dialog.open);jobStatus='complete';await poll();assert(dialog.removed);assert.equal(refreshed,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_failed_preview_and_uncertain_start_never_retry_delete(self):
        self.node(r"""
(async()=>{
let failPreview=true;const body=new E();Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},fetch:async(path)=>{requests.push(path);if(path.endsWith('/start'))throw Error('Disconnected');return {ok:!failPreview,json:async()=>failPreview?{error:'至少保留一份'}:{token:'safe',items:[{path:a.path}]}};}});vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);
scope.reportActionOpen('delete',[a.id]);await new Promise(setImmediate);let dialog=body.children[0],buttons=dialog.children.at(-1).children;assert(buttons[2].disabled);assert(dialog.children[1].children[0].textContent.includes('至少保留'));await buttons[2].onclick();assert.equal(requests.length,1);buttons[0].onclick();
failPreview=false;scope.reportActionOpen('delete',[a.id]);await new Promise(setImmediate);dialog=body.children[1];buttons=dialog.children.at(-1).children;await buttons[2].onclick();assert(dialog.children[1].children[0].textContent.includes('启动结果未确认'));await buttons[2].onclick();assert.equal(requests.filter(p=>p.endsWith('/start')).length,1);assert(!buttons[0].disabled);
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_comparison_is_one_dialog_and_hands_off_to_one_delete_confirmation(self):
        self.node(r"""
(async()=>{
const body=new E(),photos=[a,b].map(file=>({kind:'photo',files:[file],metadata:{},preview:'previews/sample.png'}));Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},fetch:async(path)=>({ok:true,json:async()=>path==='api/center'?{items:photos,operation_state:{warnings:[]}}:{available:true,ids_by_path:scope.mediaIds}})});vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);let deleted=[];scope.reportTrashOpen=ids=>deleted.push(ids);scope.reportActionOpen('compare',[a.id,b.id]);await new Promise(setImmediate);const dialog=body.children[0];assert(dialog.open);assert(!dialog.children.some(n=>n.tagName==='IFRAME'));const cards=dialog.children[2].children;assert.equal(cards.length,2);cards[0].children.at(-1).onclick();assert(dialog.removed);assert.deepEqual(Array.from(deleted[0]),[a.id]);assert.equal(requests.length,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_completion_notification_matches_only_the_submitted_job_and_fires_once(self):
        self.node(r"""
(async()=>{
let history={jobs:[{id:'other',mode:'trash',status:'complete'}],handled:{trash:[],copy:[]},warnings:[],record_count:1},notifications=[];
Object.assign(scope,{operationTimer:null,monitorRevision:0,monitorFailures:0,renderOperationCards(){},clearTimeout(){},setTimeout(){return 1;},api:async()=>history,window:{dispatchEvent:event=>notifications.push(event)},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}});
vm.runInContext(management.slice(management.indexOf('function notifyReview('),management.indexOf("$('execution-start').onclick")),scope);vm.runInContext(management.slice(management.indexOf('function activeOperation('),management.indexOf('const operationViews=')),scope);vm.runInContext(management.slice(management.indexOf('async function refreshOperations()'),management.indexOf("$('refresh-operations').onclick")),scope);vm.runInContext("submittedOperationId='mine'",scope);
await scope.refreshOperations();assert.equal(notifications.length,0);history.jobs=[{id:'mine',mode:'trash',status:'running'}];await scope.refreshOperations();assert.equal(notifications.length,0);history.jobs[0].status='complete';await scope.refreshOperations();assert.equal(notifications.length,1);assert.equal(notifications[0].type,'media-operation-finished');assert.equal(notifications[0].detail.status,'complete');assert.equal(notifications[0].detail.mode,'trash');await scope.refreshOperations();assert.equal(notifications.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_legacy_comparison_cancel_does_not_close_comparison_or_start_operation(self):
        self.node(r"""
let notifications=[];scope.window={dispatchEvent:event=>notifications.push(event)};scope.CustomEvent=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
vm.runInContext(management.slice(management.indexOf('function notifyReview('),management.indexOf("$('execution-start').onclick")),scope);scope.executionPreview={mode:'trash',token:'preview-only'};scope.operating=true;let prevented=false;$('execution').events.cancel({preventDefault(){prevented=true;}});assert(prevented);assert(scope.executionPreview);scope.operating=false;$('execution-cancel').onclick();assert.equal(scope.executionPreview,null);assert.equal(notifications.length,1);assert.equal(requests.length,0);
""")

    def test_direct_entry_waits_for_media_then_previews_once_and_rejects_missing_files(self):
        self.node(r'''
(async()=>{
 const html=fs.readFileSync(root+'/organization.html','utf8'),start=html.indexOf('function tryIncomingReview()'),end=html.indexOf('function setupSimpleWorkspace()',start);
 Object.assign(scope,{incomingReview:['','#delete',a.id],mediaLoaded:false});
 scope.incomingReview=['','delete',a.id];vm.runInContext(html.slice(start,end),scope);
 scope.tryIncomingReview();assert.equal(requests.length,0);assert(scope.incomingReview);
 scope.mediaLoaded=true;scope.tryIncomingReview();await new Promise(resolve=>setImmediate(resolve));assert.equal(requests.length,1);assert.equal(scope.incomingReview,null);
 scope.tryIncomingReview();assert.equal(requests.length,1);
 scope.incomingReview=['','delete','c'.repeat(64)];scope.tryIncomingReview();assert.equal(requests.length,1);assert(!$('error').hidden);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_simple_workspace_keeps_browsing_and_delete_visible_and_preserves_advanced_controls(self):
        self.node(r'''
 const html=fs.readFileSync(root+'/organization.html','utf8'),start=html.indexOf('function setupSimpleWorkspace()'),end=html.indexOf('setupCleanupEntry();setupSimpleWorkspace();',start);
 E.prototype.append=function(...nodes){for(const node of nodes){if(node.parent)node.parent.children=node.parent.children.filter(n=>n!==node);node.parent=this;this.children.push(node);}};
 E.prototype.prepend=function(...nodes){for(const node of nodes.reverse()){if(node.parent)node.parent.children=node.parent.children.filter(n=>n!==node);node.parent=this;this.children.unshift(node);}};
 const workspace=$('workspace'),quick=new E(),filters=new E(),toolbar=new E(),copy=new E(),basket=new E();filters.classList.add('filters');toolbar.classList.add('toolbar');
 const files=$('list-view'),photos=$('photo-wall'),folder=$('folder-browser'),logs=$('operation-section');
 for(const [id,node]of nodes)node.id=id;logs.append(new E('h2','记录'));workspace.append(quick,filters,toolbar,copy,basket,files,photos,folder,logs);
 Object.assign(scope,{document:{body:new E()},location:{pathname:'/organize.html'}});$('state').value='pending';
 vm.runInContext(html.slice(start,end),scope);scope.setupSimpleWorkspace();
 assert(scope.document.body.classList.contains('simple-workspace'));assert.equal($('state').value,'');assert.equal(files.parent,workspace);assert.equal(toolbar.parent,workspace);assert.equal(folder.parent,workspace);
 const advanced=workspace.children.find(n=>n.id==='advanced-options');assert(advanced);assert(!advanced.open);assert.equal(copy.parent,advanced);assert.equal(basket.parent,advanced);
 assert.equal(workspace.children[0].id,'simple-organize');assert.equal(quick.parent,workspace.children[0]);assert.deepEqual(Array.from(scope.selected),[b.id]);
 advanced.open=true;advanced.events.toggle();assert(scope.document.body.classList.contains('show-advanced'));
''')

    def test_movie_card_and_details_share_a_delete_button_for_the_exact_video(self):
        self.node(r'''
 const source=fs.readFileSync(root+'/media_scan.py','utf8'),start=source.indexOf('    function mediaButtons('),end=source.indexOf('    @@BROWSE@@',start);
 let opened=[];Object.assign(scope,{reportActionOpen:(action,ids)=>opened.push({action,ids}),openMedia(){throw Error('delete must not play media');}});
 vm.runInContext(source.slice(start,end),scope);const card=new E(),video={path:a.path,name:'movie.mp4'};scope.mediaButtons(video,card);
 const trash=card.children.find(n=>n.textContent==='删除…');assert(trash);trash.onclick();assert.equal(opened.length,1);assert.equal(opened[0].action,'delete');assert.deepEqual(Array.from(opened[0].ids),[a.id]);assert.equal(requests.length,0);
 scope.mediaAvailable=false;const offline=new E();scope.mediaButtons(video,offline);assert.equal(offline.children.length,0);
''')

    def test_embedded_delete_expands_parent_view_and_cancel_restores_it(self):
        self.node(r'''
(async()=>{const body=new E(),rootElement=new E(),messages=[],parent={postMessage:(data,origin)=>messages.push({data,origin})};body.style.overflow='auto';rootElement.style.overflow='scroll';Object.assign(scope,{window:{parent,addEventListener(){}},URL,document:{body,documentElement:rootElement,createElement:t=>new E(t),querySelector:selector=>selector.startsWith('nav[')?{href:'http://127.0.0.1:12345/dashboard/'}:null},clearTimeout(){},fetch:async()=>({ok:true,json:async()=>({token:'safe-preview',items:[{path:'/generated/'+('long-name-'.repeat(40))+'.mp4'}]})})});vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);scope.reportTrashOpen([a.id],null,'将保留：'+a.path);await new Promise(setImmediate);assert.equal(messages[0].data.type,'media-overlay');assert.equal(messages[0].data.open,true);assert.equal(body.style.overflow,'hidden');assert.equal(rootElement.style.overflow,'hidden');assert.equal(messages[0].origin,'http://127.0.0.1:12345');const dialog=body.children[0];dialog.children.at(-1).children[0].onclick();assert.equal(messages.at(-1).data.open,false);assert.equal(body.style.overflow,'auto');assert.equal(rootElement.style.overflow,'scroll');assert.equal(requests.length,0);})().catch(e=>{console.error(e);process.exitCode=1;});
''')
