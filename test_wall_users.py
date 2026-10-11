"""Actual shared photo/movie username controls, using report-only fixtures."""
from pathlib import Path
import shutil
import subprocess
import unittest


class WallUserTests(unittest.TestCase):
    def test_shared_toolbar_assignment_filter_refresh_and_failure_guards(self):
        code = r"""
const assert=require('assert'),{createWallUsers}=require(process.argv[1]);
class E{constructor(tag){this.tagName=tag;this.children=[];this.value='';this.disabled=false;this.textContent='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}get options(){return this.children;}setAttribute(){}}
global.document={createElement:tag=>new E(tag)};
const photo={id:'photo:p',username:'张三',user_id:'u',files:[{path:'C:\\照片\\p.png'}]}, movie={id:'movie:m',username:'归属不一致',user_id:'mixed',files:[{path:'/电影/CD1.mp4'},{path:'/电影/CD2.mp4'},{path:'/电影/4K.mp4'}]};
let snapshot={revision:'r',users:[{id:'u',name:'张三'}],items:[photo,movie]},calls=[],fail=false,locked=false,changes=0;
global.fetch=async(route,options)=>{assert.equal(route,'api/users');if(fail)return {ok:false,json:async()=>({error:'用户名已变化'})};if(options?.body){const body=JSON.parse(options.body);calls.push(body);assert.equal(body.revision,snapshot.revision);snapshot={...snapshot,revision:'r2',items:snapshot.items.map(item=>body.ids.includes(item.id)?{...item,user_id:body.user_id,username:body.user_id?'张三':'未分配'}:item)};}return {ok:true,json:async()=>snapshot};};
(async()=>{
const host=new E('div'),wall=createWallUsers({host,changed:()=>changes++,locked:()=>locked});const [filter,assign,save,refresh,message,clear]=host.children;
assert.ok(save.disabled);await wall.load();assert.ok(!message.textContent.includes('不可用'),message.textContent);assert.equal(wall.label(['/电影/4K.mp4']),'归属不一致');assert.ok(changes);filter.value='mixed';filter.onchange();assert.equal(wall.matches(movie.files.map(f=>f.path)),true);assert.equal(wall.matches([photo.files[0].path]),false);
wall.toggle(['/电影/CD1.mp4'],true);assert.ok(wall.checked(['/电影/4K.mp4']));assert.equal(save.disabled,false);assign.value='u';await save.onclick();assert.deepEqual(calls[0].ids,['movie:m']);assert.equal(wall.label(['/电影/CD2.mp4']),'张三');assert.equal(wall.matches(['/电影/CD1.mp4']),false);
filter.value='u';assert.equal(wall.matches(['/电影/4K.mp4']),true);wall.reset();assert.equal(filter.value,'');assert.equal(wall.matches(['/电影/4K.mp4']),true);
locked=true;wall.update();assert.equal(save.disabled,true);assert.equal(wall.selectable(['/电影/CD1.mp4']),false);locked=false;wall.update();
fail=true;await save.onclick();assert.ok(message.textContent.includes('未保存'));assert.ok(message.textContent.includes('刷新'));assert.equal(calls.length,1);
await refresh.onclick();assert.equal(wall.label(['/电影/CD1.mp4']),'用户名未加载');assert.ok(save.disabled);assert.ok(filter.disabled);assert.equal(filter.value,'');fail=false;await refresh.onclick();assert.ok(wall.checked(['/电影/CD1.mp4']));clear.onclick();assert.ok(save.disabled);
let selected=[photo.files[0].path],photoHost=new E('div');const photoWall=createWallUsers({host:photoHost,selectedPaths:()=>selected,changed:()=>{}});await photoWall.load();const [pf,pa,ps]=photoHost.children;assert.equal(ps.disabled,false);pa.value='';await ps.onclick();assert.deepEqual(calls[1].ids,['photo:p']);assert.equal(photoWall.label(selected),'未分配');pf.value='unassigned';assert.equal(photoWall.matches(selected),true);
selected.push('/missing.png');photoWall.update();assert.equal(ps.disabled,true);assert.equal(photoWall.selectable(selected),false);
// A selection spanning more than 200 units cannot silently save a subset.
snapshot={revision:'many',users:[],items:Array.from({length:201},(_,i)=>({id:'photo:'+i,user_id:'',username:'未分配',files:[{path:'/generated/'+i+'.png'}]}))};selected=snapshot.items.map(item=>item.files[0].path);await photoWall.load();assert.ok(ps.disabled);assert.equal(ps.textContent.includes('201'),true);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        executable = shutil.which('node')
        self.assertIsNotNone(executable, 'Both-platform CI installs Node for shared interface behavior tests')
        result = subprocess.run([executable, '-e', code, str(Path(__file__).with_name('wall_users.js'))],
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
