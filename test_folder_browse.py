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
class Element{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.attributes={};}append(...nodes){this.children.push(...nodes);}prepend(...nodes){this.children.unshift(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attributes[k]=v;}}
global.document={createElement:tag=>new Element(tag)};
const host=new Element('section');let busy=false,chosen=[],changes=0,visible=items;
const browser=b.createManagementFolderBrowser({host,items:()=>items,locked:()=>busy,changed(){changes++;browser.render();visible=items.filter(item=>browser.matches(item));browser.render();},selectable:()=>visible,select:files=>chosen=files.map(i=>i.id),formatSize:n=>n+' B'});
const controls=host.children[2],mode=controls.children[0],within=controls.children[1].children[0],top=controls.children[2],up=controls.children[3],pick=controls.children[4],summary=host.children[3],cards=host.children[4],pager=host.children[5];browser.render();
"""

    def test_real_interface_enters_folder_navigates_back_switches_mode_and_only_selects_visible_media(self):
        self.node(self.ui()+r"""
assert.equal(cards.children.length,1);assert(cards.children[0].attributes['aria-label'].includes('/samples'));cards.children[0].onclick();assert.equal(cards.children.length,2);cards.children.find(n=>n.children[0].textContent.includes('photos')).onclick();cards.children[0].onclick();assert.deepEqual(visible.map(i=>i.id),['a','c']);assert(summary.textContent.includes('/samples/photos/旅行'));pick.onclick();assert.deepEqual(chosen,['a','c']);within.checked=false;within.onchange();assert.deepEqual(visible.map(i=>i.id),['a']);up.onclick();assert(summary.textContent.includes('/samples/photos'));top.onclick();assert(top.disabled);mode.value='target';mode.onchange();assert.equal(cards.children[0].children[0].textContent,'📁 用户');cards.children[0].onclick();cards.children.find(n=>n.children[0].textContent.includes('张三')).onclick();within.checked=true;within.onchange();assert.deepEqual(visible.map(i=>i.id),['a','b']);assert.equal(items[0].source_folder,'/samples/photos/旅行');assert(changes>0);within.checked=false;within.onchange();browser.reset();browser.render();assert(within.checked);
""")

    def test_navigation_and_selection_blocked_when_busy_limit_refuses_partial_selection(self):
        self.node(self.ui()+r"""
busy=true;browser.render();const before=changes;cards.children[0].onclick();pick.onclick();assert.equal(changes,before);assert.deepEqual(chosen,[]);assert(pick.disabled);assert(mode.disabled);busy=false;visible=Array.from({length:201},(_,n)=>({id:String(n)}));browser.render();assert(pick.disabled);pick.onclick();assert.deepEqual(chosen,[]);visible=[];browser.render();assert(pick.disabled);pick.onclick();assert.deepEqual(chosen,[]);
""")

    def test_folders_are_paginated_and_disappearing_plan_folder_resets_view(self):
        self.node(self.ui()+r"""
items.splice(0,items.length,...Array.from({length:65},(_,n)=>({id:String(n),source_folder:'/samples/album'+n,suggested_path:'用户/张三/照片/a'+n+'.jpg',bytes:1,kind:'照片'})));browser.reset();browser.render();assert.equal(cards.children.length,1);cards.children[0].onclick();assert.equal(cards.children.length,30);pager.children[2].onclick();assert.equal(cards.children.length,30);pager.children[2].onclick();assert.equal(cards.children.length,5);assert(pager.children[2].disabled);cards.children[0].onclick();items.splice(0,items.length);browser.render();assert(top.disabled);assert.equal(cards.children.length,1);assert(cards.children[0].textContent.includes('没有符合'));
""")
