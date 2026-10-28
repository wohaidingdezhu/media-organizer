"""Large batches update in place and remain explicit during slow file work."""
import unittest

import test_media_center
import test_direct_cleanup


class BatchReviewInteractionTests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_selected_multi_part_movie_explains_file_limit_and_recovers_after_deselection(self):
        self.run_ui(r'''
movie.files=Array.from({length:201},(_,n)=>({...movie.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/part'+n+'.mp4',bytes:1073741824}));for(const file of movie.files)access.ids_by_path[file.path]=file.id;
await $('center-refresh').onclick();$('manage-toggle').onclick();$('select-page').onclick();assert($('delete-selected').disabled);assert($('selected-count').textContent.includes('201 个文件'));assert($('selected-count').textContent.includes('201.0 GB'));assert($('selected-count').textContent.includes('请减少选择'));
$('clear-selection').onclick();assert(!$('selected-count').textContent.includes('请减少选择'));assert.equal(operations.length,0);
''')

    def test_cancelled_or_failed_preview_keeps_cross_page_selection_after_refresh(self):
        self.run_ui(r'''
items=Array.from({length:24},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));
for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;
review.exact=Array.from({length:12},(_,n)=>({files:items.slice(n*2,n*2+2).map(item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:{}}))}));
location.hash='#review';events.hashchange();await $('center-refresh').onclick();$('center-next').onclick();
const batch=()=>$('center-content').children.find(n=>n.className==='actions review-batch');batch().children.find(n=>n.textContent==='删除所选…').onclick();
const original=Array.from(operations[0].ids);failCenter=true;await operations[0].onClose();assert(batch().children[0].textContent.includes('12 个文件'));assert(batch().children.find(n=>n.textContent==='删除所选…').disabled);
failCenter=false;await $('center-refresh').onclick();assert($('center-page').textContent.includes('2/2'));batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.deepEqual(Array.from(operations[1].ids),original);
''')

    def test_104_large_file_selection_never_rebuilds_gallery_on_toggle_or_invert(self):
        self.run_ui(r'''
items=Array.from({length:208},(_,n)=>({...movie,id:(n+1).toString(16).padStart(64,'0'),files:[{...movie.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'C:\\generated\\'+n+'.mp4',bytes:8*1073741824}]}));
for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;
review.exact=Array.from({length:104},(_,n)=>({files:items.slice(n*2,n*2+2).map(item=>({...item.files[0],kind:'video',media_kind:'movie',owner:item.id,metadata:{}}))}));
location.hash='#review';events.hashchange();await $('center-refresh').onclick();
const group=()=> $('center-content').children.find(n=>n.className==='review-group'),originalGroup=group(),cards=originalGroup.children.find(n=>n.className==='review-files').children,cover=cards[0].children.find(n=>n.className==='cover'),pick=cards[0].children[0].children[0],batch=$('center-content').children.find(n=>n.className==='actions review-batch'),tools=$('center-content').children.find(n=>n.className==='actions review-selection-tools');
let filters=0;const filter=scope.centerFilter;scope.centerFilter=(...args)=>{filters++;return filter(...args);};scope.window.scrollY=1200;
assert(batch.children[0].textContent.includes('104'));assert(batch.children[0].textContent.includes('832.0 GB'));
for(let i=0;i<8;i++){pick.checked=!pick.checked;pick.onchange();}
assert.equal(group(),originalGroup);assert.equal(cards[0].children.find(n=>n.className==='cover'),cover);assert.equal(scope.window.scrollY,1200);assert.equal(filters,0);
tools.children.find(n=>n.textContent==='反选当前结果').onclick();assert(!pick.checked);assert(cards[1].children[0].children[0].checked);assert.equal(group(),originalGroup);assert.equal(filters,0);
batch.children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations[0].ids.length,104);assert(operations[0].ids.includes(items[1].files[0].id));
$('center-next').onclick();$('center-prev').onclick();assert($('center-content').children.find(n=>n.className==='actions review-batch').children[0].textContent.includes('104'));
''')

    def test_shared_similar_photo_checkboxes_stay_synchronized_without_selecting_twice(self):
        self.run_ui(r'''
items=Array.from({length:3},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg',bytes:1048576}]}));
for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;
const file=item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:{}});
review.similar=[{id:'one',files:[file(items[0]),file(items[1])]},{id:'two',files:[file(items[0]),file(items[2])]}];location.hash='#review';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[1].onclick();
const sections=$('center-content').children.filter(n=>n.className==='review-group'),check=section=>section.children.find(n=>n.className==='review-files').children[0].children[0].children[0];
check(sections[0]).checked=true;check(sections[0]).onchange();assert(check(sections[1]).checked);
const batch=$('center-content').children.find(n=>n.className==='actions review-batch');assert(batch.children[0].textContent.includes('1 个文件'));assert(batch.children[0].textContent.includes('1.0 MB'));
check(sections[1]).checked=false;check(sections[1]).onchange();assert(!check(sections[0]).checked);assert(batch.hidden);assert.equal(operations.length,0);
''')

    def test_200_limit_and_keep_one_protection_leave_existing_cards_and_selection_intact(self):
        self.run_ui(r'''
items=Array.from({length:402},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;
review.exact=Array.from({length:201},(_,n)=>({files:items.slice(n*2,n*2+2).map(item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:{}}))}));location.hash='#review';events.hashchange();await $('center-refresh').onclick();
const section=$('center-content').children.find(n=>n.className==='review-group'),cards=section.children.find(n=>n.className==='review-files').children,batch=$('center-content').children.find(n=>n.className==='actions review-batch'),check=cards[1].children[0].children[0];
check.checked=true;check.onchange();assert(!check.checked);assert(batch.children[0].textContent.includes('200'));assert(batch.children.some(n=>n.textContent.includes('分批')));assert.equal($('center-content').children.find(n=>n.className==='review-group'),section);
const first=cards[0].children[0].children[0];first.checked=false;first.onchange();check.checked=true;check.onchange();batch.children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations[0].ids.length,200);assert(!operations[0].ids.includes(items[0].files[0].id));assert(operations[0].ids.includes(items[1].files[0].id));
''')


class BatchProgressTests(unittest.TestCase):
    node = test_direct_cleanup.DirectCleanupTests.node

    def test_slow_preview_cancel_ignores_late_result_and_never_starts(self):
        self.node(r'''
(async()=>{
let resolvePreview,refreshed=0,elapsed,clock=0;const body=new E();
Object.assign(scope,{Date:{now:()=>clock},document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},setTimeout(fn){elapsed=fn;return 1;},fetch:async(path)=>{requests.push(path);return new Promise(resolve=>resolvePreview=()=>resolve({ok:true,json:async()=>({token:'late',items:[{path:a.path}]})}));}});
vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);scope.reportTrashOpen([a.id],()=>refreshed++);
const dialog=body.children[0],message=dialog.children[1].children[0],buttons=dialog.children.at(-1).children;
assert(message.textContent.includes('尚未删除'));clock=8000;elapsed();assert(message.textContent.includes('8 秒'));assert(buttons[2].disabled);
buttons[0].onclick();const text=message.textContent;resolvePreview();await new Promise(setImmediate);assert(dialog.removed);assert.equal(message.textContent,text);assert(buttons[2].disabled);assert.equal(refreshed,1);assert.equal(requests.length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_failed_preview_can_be_rechecked_without_reselecting_or_starting_delete(self):
        self.node(r'''
(async()=>{
let fail=true,resolveRetry;const ids=Array.from({length:104},(_,n)=>(n+1).toString(16).padStart(64,'0')),body=new E();
Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},fetch:async(path,options)=>{requests.push({path,body:JSON.parse(options.body)});if(fail)return {ok:false,json:async()=>({error:'磁盘未连接'})};return new Promise(resolve=>resolveRetry=()=>resolve({ok:true,json:async()=>({token:'ready',bytes:832*1073741824,items:ids.map((id,n)=>({path:'C:\\generated\\'+n+'.mp4'}))})}));}});
vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);scope.reportTrashOpen(ids);await new Promise(setImmediate);
const dialog=body.children[0],buttons=dialog.children.at(-1).children,retry=buttons.find(b=>b.textContent==='重新检查');assert(!retry.hidden);assert(buttons[2].disabled);fail=false;
const checking=retry.onclick();await retry.onclick();assert.equal(requests.length,2);resolveRetry();await checking;assert(!buttons[2].disabled);assert(retry.hidden);assert(dialog.children[1].children[0].textContent.includes('832.0 GB'));assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);assert.deepEqual(requests[1].body.ids,ids);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_batch_progress_and_stop_feedback_survive_polling(self):
        self.node(r'''
(async()=>{
const body=new E();let poll,status='running',stopCalls=0;
Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},setTimeout(fn){poll=fn;return 1;},fetch:async(path)=>{requests.push(path);if(path.endsWith('/stop'))stopCalls++;return {ok:true,json:async()=>path.endsWith('/preview')?{token:'ready',bytes:16*1073741824,items:[{path:a.path},{path:b.path}]}:path.endsWith('/start')?{id:'a'.repeat(24)}:path.endsWith('/stop')?{}:{id:'a'.repeat(24),mode:'trash',status,total:2,items:[{path:a.path,status:'success'},{path:b.path,status:status==='running'?'processing':'cancelled'}]}};}});
vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);scope.reportTrashOpen([a.id,b.id]);await new Promise(setImmediate);
const dialog=body.children[0],content=dialog.children[1],buttons=dialog.children.at(-1).children,message=content.children[0],meter=content.children.find(n=>n.tagName==='PROGRESS');await buttons[2].onclick();assert(message.textContent.includes('1/2'));assert(message.textContent.includes('50%'));assert(message.textContent.includes(b.path));assert.equal(meter.value,1);assert.equal(meter.max,2);assert(!meter.hidden);
await buttons[1].onclick();await buttons[1].onclick();assert.equal(stopCalls,1);await poll();assert(message.textContent.includes('正在安全停止'));
status='cancelled';await poll();assert(message.textContent.includes('完成 1/2'));assert(!buttons[0].disabled);assert(buttons[2].disabled);assert.equal(requests.filter(p=>p.endsWith('/start')).length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')
