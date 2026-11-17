"""Folder navigation uses only report records, shared by both platforms."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class FolderBrowseTests(unittest.TestCase):
    def node(self, code):
        node=shutil.which('node')
        self.assertIsNotNone(node, 'Mac and Windows CI install Node for interface behavior tests')
        result=subprocess.run([node,'-e',code,str(Path(__file__).parent/'management_browse.js')],text=True,
                              encoding='utf-8',capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def fixture(self):
        return r"""
const b=require(process.argv[1]),assert=require('assert/strict');
const items=[{id:'a',kind:'照片',source_folder:'/samples/photos/旅行',suggested_path:'用户/张三/照片/a.jpg',bytes:10},
{id:'b',kind:'视频',source_folder:'/samples/movies/旅行',suggested_path:'用户/张三/电影/b.mp4',bytes:20},
{id:'c',kind:'照片',source_folder:'/samples/photos/旅行/day2',suggested_path:'用户/李四/照片/c.jpg',bytes:30}];
"""

    def test_same_folder_names_different_ancestors_are_distinct_and_parent_sizes_are_totals(self):
        self.node(self.fixture()+r"""
const before=JSON.stringify(items),tree=b.managementFolderTree(items,'source'),folders=[...tree.values()].filter(n=>n.name==='旅行');assert.equal(folders.length,2);assert.notEqual(folders[0].key,folders[1].key);assert.deepEqual(folders.map(n=>n.bytes),[40,20]);assert.equal(tree.get('').count,3);assert.equal(tree.get('').bytes,60);assert.equal(JSON.stringify(items),before);
const album=folders.find(n=>n.photos);assert.equal(album.direct,1);assert(b.managementFolderContains(items[2],album,'source'));assert(!b.managementFolderContains(items[2],album,'source',false));assert(!b.managementFolderContains(items[1],album,'source'));
""")

    def test_drive_unc_posix_and_literal_posix_backslashes_preserve_folder_identity(self):
        self.node(self.fixture()+r"""
for(const [folder,parts]of [['C:\\photos\\旅行',['C:\\','photos','旅行']],['D:\\photos\\旅行',['D:\\','photos','旅行']],['\\\\nas\\share\\旅行',['\\\\nas\\share\\','旅行']],['/photos/a\\b',['/','photos','a\\b']]]){const item={source_folder:folder,kind:'照片',bytes:1};assert.deepEqual(b.managementFolderParts(item,'source'),parts);const tree=b.managementFolderTree([item],'source'),leaf=tree.get(JSON.stringify(parts));assert.equal(leaf.path,folder);assert(b.managementFolderContains(item,leaf,'source',false));}
const different=[{source_folder:'/samples/A'},{source_folder:'/samples/a'},{source_folder:'/samples/é'},{source_folder:'/samples/e\u0301'}];assert.equal([...b.managementFolderTree(different,'source').values()].filter(n=>n.direct).length,4);
""")

    def test_classification_tree_uses_current_suggestions_without_changing_original_folders(self):
        self.node(self.fixture()+r"""
const before=JSON.stringify(items),tree=b.managementFolderTree(items,'target'),user=[...tree.values()].find(n=>n.name==='张三');assert.equal(user.count,2);assert.equal(user.photos,1);assert.equal(user.videos,1);assert(b.managementFolderContains(items[1],user,'target'));assert(!b.managementFolderContains(items[2],user,'target'));assert.equal(user.path,'用户/张三');assert.equal(JSON.stringify(items),before);items[0].suggested_path='用户/李四/照片/a.jpg';assert.equal([...b.managementFolderTree(items,'target').values()].find(n=>n.name==='李四').count,2);
""")

    def test_empty_legacy_and_invalid_sizes_are_readable_without_nan(self):
        self.node(self.fixture()+r"""
assert.equal(b.managementFolderTree([],'source').get('').count,0);const unknown=b.managementFolderTree([{bytes:-1},{bytes:NaN},{bytes:true},{bytes:1.5}],'source');assert.equal(unknown.get('').bytes,0);assert.equal(unknown.get('').count,4);assert.equal([...unknown.values()].filter(n=>n.name==='来源未提供').length,1);
""")

    def ui(self):
        return self.fixture()+r"""
const fs=require('node:fs'),path=require('node:path'),root=path.dirname(process.argv[1]);
const {JSDOM}=require(require.resolve('jsdom',{paths:[root]}));
const dom=new JSDOM('<section id="folders"></section>',{runScripts:'outside-only'});
const {window}=dom,host=window.document.getElementById('folders');
window.eval(fs.readFileSync(path.join(root,'issue_list_bundle.js'),'utf8'));
let busy=false,chosen=[],changes=0,visible=items;
const browser=window.createManagementFolderBrowser({host,items:()=>items,locked:()=>busy,
  changed(){changes++;browser.prepare();visible=items.filter(item=>browser.matches(item));browser.render();},
  selectable:()=>visible,select:files=>chosen=files.map(i=>i.id),formatSize:n=>n+' B'});
browser.render();
const controls=host.children[2],mode=controls.children[0],within=controls.children[1].children[0],
  top=controls.children[2],up=controls.children[3],pick=controls.children[4],summary=host.children[3],
  cards=host.children[4],pager=host.children[5];
function change(element,value){
  if(element.disabled)return;
  const input=element.tagName==='INPUT',prototype=input?window.HTMLInputElement.prototype:window.HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype,'value').set.call(element,value);
  element.dispatchEvent(new window.Event(input?'input':'change',{bubbles:true}));
}

"""

    def test_real_interface_enters_folder_navigates_back_switches_mode_and_only_selects_visible_media(self):
        self.node(self.ui()+r"""
assert.equal(cards.children.length,1);assert(cards.children[0].getAttribute('aria-label').includes('/samples'));cards.children[0].click();assert.equal(cards.children.length,2);[...cards.children].find(n=>n.children[0].textContent.includes('photos')).click();cards.children[0].click();assert.deepEqual(visible.map(i=>i.id),['a','c']);assert(summary.textContent.includes('/samples/photos/旅行'));pick.click();assert.deepEqual(chosen,['a','c']);within.click();assert.deepEqual(visible.map(i=>i.id),['a']);up.click();assert(summary.textContent.includes('/samples/photos'));top.click();assert(top.disabled);change(mode,'target');assert.equal(cards.children[0].children[0].textContent,'📁 用户');cards.children[0].click();[...cards.children].find(n=>n.children[0].textContent.includes('张三')).click();assert(within.checked);assert.deepEqual(visible.map(i=>i.id),['a','b']);assert.equal(items[0].source_folder,'/samples/photos/旅行');assert(changes>0);within.click();browser.reset();browser.render();assert(within.checked);
""")

    def test_navigation_and_selection_blocked_when_busy_limit_refuses_partial_selection(self):
        self.node(self.ui()+r"""
busy=true;browser.render();const before=changes;cards.children[0].click();pick.click();assert.equal(changes,before);assert.deepEqual(chosen,[]);assert(pick.disabled);assert(mode.disabled);busy=false;visible=Array.from({length:201},(_,n)=>({id:String(n)}));browser.render();assert(pick.disabled);pick.click();assert.deepEqual(chosen,[]);visible=[];browser.render();assert(pick.disabled);pick.click();assert.deepEqual(chosen,[]);
""")

    def test_folders_are_paginated_and_disappearing_plan_folder_resets_view(self):
        self.node(self.ui()+r"""
items.splice(0,items.length,...Array.from({length:65},(_,n)=>({id:String(n),source_folder:'/samples/album'+n,suggested_path:'用户/张三/照片/a'+n+'.jpg',bytes:1,kind:'照片'})));browser.reset();browser.render();assert.equal(cards.children.length,1);cards.children[0].click();assert.equal(cards.children.length,30);pager.children[2].click();assert.equal(cards.children.length,30);pager.children[2].click();assert.equal(cards.children.length,5);assert(pager.children[2].disabled);cards.children[0].click();items.splice(0,items.length);browser.render();assert(top.disabled);assert.equal(cards.children.length,1);assert(cards.children[0].textContent.includes('没有符合'));
""")

    def test_directory_search_finds_nested_paths_without_matching_sibling_prefix(self):
        self.node(self.fixture()+r"""
items.push({id:'d',kind:'照片',source_folder:'/samples/photos2/旅行',bytes:99});
const tree=b.managementFolderTree(items,'source'),root=tree.get(''),album=[...tree.values()].find(n=>n.path==='/samples/photos');const before=JSON.stringify([...tree.values()]);
assert.equal(b.managementFolderRows(tree,root,'source','  旅行  ').length,4);
assert.deepEqual(b.managementFolderRows(tree,album,'source','旅行').map(n=>n.path),['/samples/photos/旅行','/samples/photos/旅行/day2']);
assert.deepEqual(b.managementFolderRows(tree,album,'source','DAY2').map(n=>n.name),['day2']);assert.equal(b.managementFolderRows(tree,album,'source','missing').length,0);assert.equal(JSON.stringify([...tree.values()]),before);
""")

    def test_directory_search_handles_windows_unc_unicode_and_never_merges_results(self):
        self.node(self.fixture()+r"""
const records=[{source_folder:'C:\\photos\\旅行',bytes:1},{source_folder:'D:\\photos\\旅行',bytes:2},{source_folder:'\\\\nas\\share\\旅行',bytes:3},{source_folder:'/photos/é',bytes:4},{source_folder:'/photos/e\u0301',bytes:5}];const tree=b.managementFolderTree(records,'source'),root=tree.get('');assert.equal(b.managementFolderRows(tree,root,'source','旅行').length,3);assert.equal(b.managementFolderRows(tree,root,'source','NAS').length,2);const accent=b.managementFolderRows(tree,root,'source','é');assert.equal(accent.length,2);assert.notEqual(accent[0].key,accent[1].key);
""")

    def test_directory_sorting_uses_subtree_sizes_and_counts_with_stable_names(self):
        self.node(self.fixture()+r"""
const records=[{source_folder:'/samples/album10',bytes:4},{source_folder:'/samples/album2',bytes:4},{source_folder:'/samples/small',bytes:1},{source_folder:'/samples/small/day2',bytes:1},{source_folder:'/samples/big',bytes:100}];const tree=b.managementFolderTree(records,'source'),active=[...tree.values()].find(n=>n.path==='/samples');assert.deepEqual(b.managementFolderRows(tree,active,'source','','name').map(n=>n.name),['album2','album10','big','small']);assert.deepEqual(b.managementFolderRows(tree,active,'source','','size').map(n=>n.name),['big','album2','album10','small']);assert.equal(b.managementFolderRows(tree,active,'source','','count')[0].name,'small');
""")

    def test_directory_search_and_sort_only_change_cards_then_click_enters_chosen_folder(self):
        self.node(self.ui()+r"""
const search=controls.children[5],sort=controls.children[6],before=changes;chosen=['b'];change(search,'day2');assert.equal(changes,before);assert.deepEqual(chosen,['b']);assert.equal(cards.children.length,1);assert(summary.textContent.includes('全部文件夹'));assert(pager.children[1].textContent.includes('匹配目录'));change(sort,'size');assert.equal(changes,before);assert.deepEqual(chosen,['b']);cards.children[0].click();assert.equal(search.value,'');assert.deepEqual(visible.map(i=>i.id),['c']);assert(summary.textContent.includes('/samples/photos/旅行/day2'));assert.equal(sort.value,'size');change(search,'missing');assert(cards.children[0].textContent.includes('没有匹配'));assert.deepEqual(visible.map(i=>i.id),['c']);
""")

    def test_source_and_target_modes_remember_independent_locations_and_search_settings(self):
        self.node(self.ui()+r"""
const search=controls.children[5],sort=controls.children[6];change(search,'day2');cards.children[0].click();within.click();change(sort,'size');change(mode,'target');cards.children[0].click();[...cards.children].find(n=>n.children[0].textContent.includes('张三')).click();change(search,'照片');change(mode,'source');assert(summary.textContent.includes('/samples/photos/旅行/day2'));assert.equal(within.checked,false);assert.equal(sort.value,'size');assert.equal(search.value,'');change(mode,'target');assert(summary.textContent.startsWith('用户/张三'));assert.equal(search.value,'照片');assert.equal(sort.value,'name');assert.equal(within.checked,true);browser.reset();browser.render();change(mode,'source');assert(top.disabled);assert.equal(search.value,'');assert.equal(sort.value,'name');assert(within.checked);
""")

    def test_remembered_folder_removed_from_plan_falls_back_and_busy_controls_do_nothing(self):
        self.node(self.ui()+r"""
const search=controls.children[5],sort=controls.children[6];cards.children[0].click();[...cards.children].find(n=>n.children[0].textContent.includes('photos')).click();change(mode,'target');items.splice(0,items.length);change(mode,'source');assert(top.disabled);assert(summary.textContent.includes('全部文件夹'));busy=true;browser.render();const before=changes;change(search,'x');change(sort,'count');change(mode,'target');within.click();pager.children[2].click();assert.equal(changes,before);assert(search.disabled);assert(sort.disabled);assert(mode.disabled);
""")

    def test_directory_search_pagination_does_not_truncate_nested_results_and_enter_resets_page(self):
        self.node(self.ui()+r"""
const search=controls.children[5];items.splice(0,items.length,...Array.from({length:65},(_,n)=>({id:String(n),kind:'照片',source_folder:'/samples/album'+n,bytes:1})));browser.render();change(search,'album');assert.equal(cards.children.length,30);assert(pager.children[1].textContent.includes('65'));pager.children[2].click();pager.children[2].click();assert.equal(cards.children.length,5);change(mode,'target');change(mode,'source');assert.equal(cards.children.length,5);assert.equal(search.value,'album');assert(pager.children[1].textContent.includes('第 3/3 页'));cards.children[0].click();assert.equal(search.value,'');assert(pager.children[1].textContent.includes('第 1/1 页'));
""")
