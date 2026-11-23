"""Generated copy records exercise the shared Mac/Windows result browser."""
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
const browse=require(process.argv[1]);
function copy(id,parts,name,kind='照片',bytes=10){
  const separator=/^[A-Za-z]:|^\\\\/.test(parts[0])?'\\':'/';
  const folder=parts.join(separator);
  return {id,path:'/generated/'+id,name,folder,result_parts:parts,target:folder+separator+name,kind,bytes,can_open:kind==='照片'||kind==='视频'};
}
class E {
  constructor(tag,text=''){this.tag=tag;this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.value='';this.attrs={};this.disabled=false;this.events={};this.open=false;}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=values;}
  insertBefore(value,before){const index=this.children.indexOf(before);this.children.splice(index<0?this.children.length:index,0,value);}
  setAttribute(key,value){this.attrs[key]=value;}
  getAttribute(key){return this.attrs[key];}
  addEventListener(name,handler){this.events[name]=handler;}
  showModal(){this.open=true;}
  close(){this.open=false;this.events.close?.();}
  querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]);}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E('div'));return nodes.get(id);};
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const cards=()=>$('organized-items').children.filter(node=>node.tag==='article');
let records=[],calls=[],failure=null;
$('organized-descendants').checked=true;$('organized-sort').value='name';
const scope={...browse,$,photos:new Map(),photoPreviewSource:id=>scope.photos.get(id)?.preview||'',make:(tag,cls,value)=>new E(tag,value||''),organizedItems:[],organizedNode:'',organizedPage:0,organizedFolderPage:0,operating:false,saving:false,mediaIds:{},picture(){},fileSize:value=>value+' B',error:message=>calls.push({error:message}),api:async(route,body)=>{
  if(route==='api/organized-results')return {items:records,message:'generated records'};
  calls.push({route,body});if(failure)throw Error(failure);return {opened:true};
}};
const path=require('path'),{mountTestOrganizedNavigation,mountTestOrganizedCopies}=require(path.join(path.dirname(process.argv[2]),'frontend/view-test-dom.cjs'));
scope.mountOrganizedNavigation=(breadcrumbs,folders,onNavigate)=>mountTestOrganizedNavigation(breadcrumbs,folders,onNavigate,tag=>new E(tag));
scope.mountOrganizedCopies=(host,actions)=>mountTestOrganizedCopies(host,actions,tag=>new E(tag));
vm.createContext(scope);const source=fs.readFileSync(process.argv[2],'utf8');
vm.runInContext(source.slice(source.indexOf('async function openOrganizedCopy'),
  source.indexOf('// Initialize the organizing workspace')),scope);
const render=()=>scope.renderOrganizedResults();
'''


class OrganizedBrowseTests(unittest.TestCase):
    def node(self, code):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'UI behavior tests require the declared Node test runtime')
        root = Path(__file__).parent
        result = subprocess.run([node, '-e', HARNESS+code, str(root/'management_browse.js'), str(root/'management.js')],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_type_scope_search_natural_size_sort_and_read_only_counts(self):
        self.node(r'''
const items=[copy('p10',['/copies','旅行'],'photo10.jpg','照片',30),copy('p2',['/copies','旅行'],'photo2.jpg','照片',50),copy('v',['/copies','旅行','day2'],'clip.mp4','视频',400),copy('x',['/copies','旅行'],'clip.xmp','XMP',2),copy('other',['/copies','旅行2'],'other.jpg','照片',9)];
const before=JSON.stringify(items),key=JSON.stringify(['/copies','旅行']);
let view=browse.organizedResultView(items,{key,kind:'照片'});
assert.deepEqual(view.rows.map(i=>i.id),['p2','p10']);assert.equal(view.bytes,80);assert.equal(view.photos,2);assert.equal(view.videos,0);assert.equal(view.folders.length,0);
view=browse.organizedResultView(items,{key,order:'size'});assert.deepEqual(view.rows.map(i=>i.id),['v','p2','p10','x']);assert.equal(view.folders[0].name,'day2');
assert.deepEqual(browse.organizedResultView(items,{key,kind:'attachment'}).rows.map(i=>i.id),['x']);
assert.equal(browse.organizedResultView(items,{key,descendants:false}).rows.length,3);
assert.deepEqual(browse.organizedResultView(items,{key,descendants:false,query:'CLIP.MP4'}).rows.map(i=>i.id),['v']);
view=browse.organizedResultView(items,{key,kind:'视频',descendants:false});assert.equal(view.active.key,key);assert.equal(view.rows.length,0);assert.equal(view.folders.length,1);
assert.equal(JSON.stringify(items),before,'browse mutated copy records');
''')

    def test_same_named_windows_unc_mac_folders_unicode_and_sibling_scopes_stay_distinct(self):
        self.node(r'''
const items=[copy('c',['C:\\copies','旅行'],'a.jpg'),copy('d',['D:\\copies','旅行'],'a.jpg'),copy('nas',['\\\\nas\\share','旅行'],'a.jpg'),copy('mac',['/copies','旅行'],'a.jpg'),copy('other',['/copies','旅行2'],'a.jpg'),copy('nfc',['/copies','é'],'é.jpg'),copy('nfd',['/copies','e\u0301'],'e\u0301.jpg')];
for(const id of ['c','d','nas','mac']){const item=items.find(i=>i.id===id),view=browse.organizedResultView(items,{key:JSON.stringify(item.result_parts)});assert.deepEqual(view.rows.map(i=>i.id),[id]);assert.equal(view.active.path,item.folder);assert.equal(view.ancestors.length,3);}
const view=browse.organizedResultView(items,{query:'É'});assert.equal(view.rows.length,2);assert.notEqual(JSON.stringify(view.rows[0].result_parts),JSON.stringify(view.rows[1].result_parts));
assert.equal(browse.organizedResultView(items,{key:'missing'}).active.key,'');
''')

    def test_ui_paginates_folders_and_files_preserves_refresh_and_resets_filters(self):
        self.node(r'''
(async()=>{
records=Array.from({length:65},(_,i)=>copy('p'+i,['/copies','album'+i],'photo'+i+'.jpg'));await scope.refreshOrganizedResults();
assert.equal(cards().length,50);assert.equal($('organized-folders').children.length,1);$('organized-folders').children[0].onclick();
assert.equal($('organized-folders').children.length,30);$('organized-folder-next').onclick();$('organized-folder-next').onclick();assert.equal($('organized-folders').children.length,5);assert($('organized-folder-next').disabled);
$('organized-next').onclick();assert.equal(cards().length,15);assert(text($('organized-items')).includes('photo64.jpg'));
await scope.refreshOrganizedResults();assert.equal(cards().length,15);assert($('organized-folder-page').textContent.includes('3/3'));assert($('organized-page').textContent.includes('2/2'));
$('organized-folders').children[0].onclick();assert.equal(cards().length,1);assert.equal($('organized-breadcrumbs').children.length,3);assert($('organized-breadcrumbs').children[2].disabled);
$('organized-kind').value='视频';$('organized-kind').onchange();assert.equal(cards().length,0);assert($('organized-summary').textContent.includes('album60'));assert(text($('organized-items')).includes('没有匹配'));
$('organized-breadcrumbs').children[1].onclick();assert($('organized-summary').textContent.startsWith('/copies ·'));assert.equal($('organized-kind').value,'视频');
$('organized-reset').onclick();assert.equal(cards().length,50);assert.equal($('organized-kind').value,'');assert($('organized-descendants').checked);assert($('organized-up').disabled);
records=[];await scope.refreshOrganizedResults();assert.equal(cards().length,0);assert(text($('organized-items')).includes('还没有成功复制记录'));assert($('organized-next').disabled);assert($('organized-folder-next').disabled);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_refresh_ignores_out_of_order_results_and_old_errors_preserving_scope_and_page(self):
        self.node(r'''
(async()=>{
records=Array.from({length:65},(_,i)=>copy('p'+i,['/copies','旅行'],'photo'+i+'.jpg'));
await scope.refreshOrganizedResults();scope.navigateOrganizedResults(JSON.stringify(['/copies','旅行']));
$('organized-next').onclick();assert.equal(scope.organizedPage,1);
const pending=[];
scope.api=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
const old=scope.refreshOrganizedResults(),latest=scope.refreshOrganizedResults();
assert($('organized-refresh').disabled);assert($('organized-message').textContent.includes('正在读取'));
pending[0].resolve({items:[],message:'旧空结果'});await old;
assert($('organized-refresh').disabled);assert.equal(cards().length,15);
pending[1].resolve({items:records,message:'当前结果'});await latest;
assert(!$('organized-refresh').disabled);assert.equal(scope.organizedPage,1);
assert.equal(scope.organizedNode,JSON.stringify(['/copies','旅行']));
assert($('organized-message').textContent.includes('当前结果'));
const stale=scope.refreshOrganizedResults(),fresh=scope.refreshOrganizedResults();
pending[3].resolve({items:records,message:'更新结果'});await fresh;
pending[2].reject(Error('旧连接错误'));await stale;
assert($('organized-message').textContent.includes('更新结果'));
assert(!$('organized-message').textContent.includes('旧连接错误'));
const before=$('organized-summary').textContent,failed=scope.refreshOrganizedResults();
pending[4].reject(Error('磁盘暂时断开'));await failed;
assert(!$('organized-refresh').disabled);assert($('organized-message').textContent.includes('可以重试'));
assert.equal($('organized-summary').textContent,before);assert.equal(cards().length,15);
const retry=scope.refreshOrganizedResults();
pending[5].resolve({items:records,message:'恢复成功'});await retry;
assert($('organized-message').textContent.includes('恢复成功'));assert.equal(scope.organizedPage,1);
assert.equal(calls.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_copy_launch_lock_survives_rerender_page_roundtrip_and_recovers_after_failure(self):
        self.node(r'''
(async()=>{
records=Array.from({length:65},(_,i)=>copy('v'+i,['/copies'],'video'+i+'.mp4','视频',100000000000));
await scope.refreshOrganizedResults();
let resolve,reject,requests=0;
scope.api=()=>{requests++;return new Promise((done,no)=>{resolve=done;reject=no;});};
const play=()=>cards()[0].children.find(button=>button.getAttribute('data-copy-action')==='open');
const first=play(),pending=first.onclick();
assert(first.disabled);assert(first.textContent.includes('正在打开'));
scope.renderOrganizedResults();assert.equal(play(),first);
$('organized-next').onclick();$('organized-previous').onclick();
assert(play().disabled);await play().onclick();assert.equal(requests,1);
reject(Error('模拟播放器不可用'));await pending;
assert(!play().disabled);assert.equal(requests,1);assert(calls.some(call=>call.error==='模拟播放器不可用'));
const retry=play().onclick();assert.equal(requests,2);
scope.operating=true;resolve({opened:true});await retry;
assert(play().disabled);scope.operating=false;scope.syncOrganizedCards();assert(!play().disabled);
assert(records.every(item=>item.kind==='视频'));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_different_copies_of_one_source_have_independent_pending_actions(self):
        self.node(r'''
(async()=>{
const first=copy('first',['/copies/a'],'photo.jpg');
records=[first,{...copy('second',['/copies/b'],'photo.jpg'),path:first.path}];
await scope.refreshOrganizedResults();
const pending=[];
scope.api=(route,body)=>new Promise(resolve=>pending.push({body,resolve}));
const opens=()=>cards().map(card=>card.children.find(button=>button.getAttribute('data-copy-action')==='open'));
const one=opens()[0].onclick();assert(!opens()[1].disabled);
const two=opens()[1].onclick();assert.equal(pending.length,2);
assert.notEqual(pending[0].body.id,pending[1].body.id);
pending[0].resolve({opened:true});await one;
assert(!opens()[0].disabled);assert(opens()[1].disabled);
pending[1].resolve({opened:true});await two;
assert(opens().every(button=>!button.disabled));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_ui_search_scope_sort_and_safe_copy_actions(self):
        self.node(r'''
(async()=>{
records=[copy('p2',['/copies','旅行'],'photo2.jpg'),copy('p10',['/copies','旅行','child'],'photo10.jpg','照片',20),copy('v',['/copies','旅行'],'video.mp4','视频',50),{...copy('old',['/copies','旅行'],'old.jpg'),can_open:false}];
await scope.refreshOrganizedResults();$('organized-folders').children[0].onclick();$('organized-folders').children[0].onclick();
$('organized-descendants').checked=false;$('organized-descendants').onchange();assert.equal(cards().length,3);
$('organized-search').value='PHOTO10';$('organized-search').oninput();assert.equal(cards().length,1);assert($('organized-summary').textContent.includes('搜索当前目录及子目录'));
await scope.refreshOrganizedResults();assert.equal($('organized-search').value,'PHOTO10');assert.equal(cards().length,1);
$('organized-search').value='';$('organized-search').oninput();$('organized-sort').value='size';$('organized-sort').onchange();assert(cards()[0].children[0].textContent==='video.mp4');
const play=cards()[0].children.find(e=>e.textContent==='播放整理视频');scope.operating=true;await play.onclick();assert.equal(calls.length,0);scope.operating=false;await play.onclick();assert.equal(calls[0].body.id,'v');assert.equal(calls[0].body.action,'open');
const reveal=cards()[0].children.find(e=>e.textContent==='打开所在文件夹');failure='副本已变化';await reveal.onclick();assert.equal(calls[2].error,'副本已变化');assert(!reveal.disabled);
const old=cards().find(card=>text(card).includes('old.jpg'));assert.equal(old.children.filter(e=>e.tag==='button').length,1);assert(old.children[0].attrs['aria-label'].startsWith('预览整理照片'));
$('organized-up').onclick();assert.equal($('organized-search').value,'');assert.equal($('organized-sort').value,'size');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
