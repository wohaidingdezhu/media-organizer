"""Browse generated copy records without ever opening original personal media."""
from pathlib import Path
import shutil
import subprocess
import unittest

from test_organized_browse import HARNESS

VIEWER = r'''
Object.assign(scope,{photos:new Map(),visible:[],data:{items:[]},selected:new Set(['preserved']),handledTrash:new Set(),bundleReviewInfo:null,bundleReviewAnchor:null,bundleReviewRevision:0,pictureSequence:[],pictureIndex:0,pictureCompare:false,pictureCopies:false,folderPreview:null,folderIds:[],filters:[],render(){},openMedia(){throw Error('original-media action called from copy viewer');}});
vm.runInContext(source.slice(0,source.indexOf('function optionsFor')),scope);
function button(label){const found=$('picture-body').querySelectorAll('button').find(node=>node.textContent===label);assert(found,'missing button '+label);return found;}
function key(name,extra={}){let prevented=false;$('picture-viewer').events.keydown({key:name,target:{tagName:'BUTTON'},preventDefault(){prevented=true;},...extra});return prevented;}
'''


class OrganizedViewerTests(unittest.TestCase):
    def node(self, script):
        node=shutil.which('node')
        self.assertIsNotNone(node, 'UI behavior tests require the declared Node test runtime')
        root=Path(__file__).parent
        result=subprocess.run([node,'-e',HARNESS+VIEWER+script,str(root/'management_browse.js'),str(root/'management.js')],
                              capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_card_preview_crosses_pages_in_filter_order_and_retains_list_selection(self):
        self.node(r'''
(async()=>{
records=Array.from({length:65},(_,i)=>copy('p'+i,['C:\\copies','旅行'],'photo'+i+'.jpg',i===50?'视频':'照片',i+1));
records.push(copy('outside',['D:\\copies','旅行'],'other.jpg'));
await scope.refreshOrganizedResults();scope.navigateOrganizedResults(JSON.stringify(['C:\\copies','旅行']));
cards()[49].children[0].onclick();assert($('picture-viewer').open);assert($('picture-title').textContent.includes('整理后'));assert($('picture-position').textContent.includes('50/64'));
assert(key('ArrowRight'));assert.equal(scope.pictureSequence[scope.pictureIndex].id,'p51');assert(text($('picture-body')).includes('C:\\copies\\旅行\\photo51.jpg'));assert(!text($('picture-body')).includes('/generated/'));
assert.equal(scope.organizedPage,0);assert(!key('ArrowLeft',{ctrlKey:true}));assert.equal(scope.pictureIndex,50);
assert(!key('ArrowLeft',{target:{tagName:'INPUT'}}));$('picture-close').onclick();assert.equal(scope.pictureSequence.length,0);assert.equal(scope.pictureCopies,false);assert.equal(scope.selected.size,1);assert.equal(scope.organizedNode,JSON.stringify(['C:\\copies','旅行']));
$('organized-sort').value='size';$('organized-sort').onchange();cards()[0].children[0].onclick();assert.equal(scope.pictureSequence[0].id,'p64');assert($('picture-previous').disabled);scope.turnPicture(-1);assert.equal(scope.pictureIndex,0);
$('picture-close').onclick();$('organized-search').value='photo2';$('organized-search').oninput();cards()[0].children[0].onclick();assert.equal(scope.pictureSequence.length,11);assert(scope.pictureSequence.every(item=>item.name.includes('photo2')));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_scan_preview_uses_source_thumbnail_but_actions_always_use_unique_copy_id(self):
        self.node(r'''
(async()=>{
const a=copy('first',['/copies/a'],'one.jpg'),b={...copy('second',['/copies/b'],'two.jpg'),path:a.path};
records=[a,b];scope.mediaIds[a.path]='source-id';scope.photos.set('source-id',{preview:'previews/sample.png'});scope.handledTrash.add(a.path);
await scope.refreshOrganizedResults();cards()[1].children[0].onclick();assert.equal(scope.pictureIndex,1);assert($('picture-next').disabled);
assert.equal($('picture-body').querySelectorAll('img')[0].src,'previews/sample.png');assert(text($('picture-body')).includes('不代表副本当前内容'));
assert(!button('打开整理副本').disabled);await button('打开整理副本').onclick();assert.equal(calls[0].route,'api/organized-results/action');assert.equal(calls[0].body.id,'second');
await button('定位整理副本').onclick();assert.equal(calls[1].body.action,'reveal');assert.equal(calls[1].body.id,'second');
$('picture-previous').onclick();assert.equal(scope.pictureSequence[scope.pictureIndex].id,'first');await button('打开整理副本').onclick();assert.equal(calls[2].body.id,'first');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_busy_missing_identity_and_late_errors_do_not_open_wrong_file_or_pollute_view(self):
        self.node(r'''
(async()=>{
records=[copy('a',['/copies'],'a.jpg'),copy('b',['/copies'],'b.jpg'),{...copy('old',['/copies'],'old.jpg'),can_open:false}];await scope.refreshOrganizedResults();cards()[0].children[0].onclick();
scope.operating=true;scope.syncBrowseControls();assert(button('打开整理副本').disabled);await button('打开整理副本').onclick();assert.equal(calls.length,0);
scope.operating=false;scope.syncBrowseControls();failure='副本已变化，请重新扫描';await button('打开整理副本').onclick();assert(!$('picture-error').hidden);assert($('picture-error').textContent.includes('副本已变化'));
failure=null;await button('打开整理副本').onclick();assert($('picture-error').hidden);
let reject,requests=0;scope.api=()=>{requests++;return new Promise((resolve,no)=>reject=no);};const open=button('打开整理副本'),pending=open.onclick();scope.syncBrowseControls();assert(open.disabled);await open.onclick();assert.equal(requests,1,'duplicate launch during polling');
$('picture-next').onclick();reject(Error('late failure for a'));await pending;assert($('picture-error').hidden);assert.equal(scope.pictureSequence[scope.pictureIndex].id,'b');
const closed=button('打开整理副本').onclick();$('picture-close').onclick();reject(Error('late after close'));await closed;assert($('picture-error').hidden);
cards()[2].children[0].onclick();assert(text($('picture-body')).includes('缺少副本身份'));assert.equal($('picture-body').querySelectorAll('button').length,0);assert(text($('picture-body')).includes('扫描预览未生成'));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


    def test_late_photo_catalog_populates_copy_thumbnails_without_manual_refresh(self):
        self.node(r'''
(async()=>{
records=[copy('a',['/copies'],'a.jpg')];scope.mediaIds[records[0].path]='source-id';await scope.refreshOrganizedResults();
assert.equal(cards()[0].querySelectorAll('img').length,0);
Object.assign(scope,{optionsFor(){},checkDisks(){},refreshOperations(){},refreshBasket(){}});
scope.api=async route=>route==='api/photos'?{items:[{id:'source-id',preview:'previews/late.png',month:'2021/05'}]}:{message:'generated comparison',items:[]};
vm.runInContext(source.slice(source.indexOf('async function loadExtras'),source.indexOf('const oldBuildFolders')),scope);
await scope.loadExtras();assert.equal(cards()[0].querySelectorAll('img')[0].src,'previews/late.png');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__=='__main__':
    unittest.main()
