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
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.attrs={};this.disabled=false;}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=values;}
  setAttribute(key,value){this.attrs[key]=value;}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E('div'));return nodes.get(id);};
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const cards=()=>$('organized-items').children.filter(node=>node.tag==='article');
let records=[],calls=[],failure=null;
$('organized-descendants').checked=true;$('organized-sort').value='name';
const scope={...browse,$,make:(tag,cls,value)=>new E(tag,value||''),organizedItems:[],organizedNode:'',organizedPage:0,organizedFolderPage:0,operating:false,saving:false,mediaIds:{},picture(){},fileSize:value=>value+' B',error:message=>calls.push({error:message}),api:async(route,body)=>{
  if(route==='api/organized-results')return {items:records,message:'generated records'};
  calls.push({route,body});if(failure)throw Error(failure);return {opened:true};
}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[2],'utf8');
vm.runInContext(source.slice(source.indexOf('async function refreshOrganizedResults'),source.indexOf('loadQuickSettings();refreshOrganizedResults();renderQuickIssues();')),scope);
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
const old=cards().find(card=>text(card).includes('old.jpg'));assert(!old.children.some(e=>e.tag==='button'));
$('organized-up').onclick();assert.equal($('organized-search').value,'');assert.equal($('organized-sort').value,'size');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
