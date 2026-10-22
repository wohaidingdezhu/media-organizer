"""Generated media verifies browsing metadata, safe routes and shared frontend logic."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_backup
import library_server
import media_scan
from media_actions import media_id
from media_center import CenterState, center_catalog, validate_state, NAME
from test_library_server import MemoryServer, request


class MediaCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.source=self.base/'generated';self.source.mkdir();self.output=self.base/'reports'
        for name in ('photo.jpg','VID_20261022.mp4','Feature2025.mp4'):
            (self.source/name).write_bytes(('Generated '+name).encode())
        (self.source/'Feature2025.nfo').write_text('<movie><title>Example Film</title><year>2025</year><actor><name>Example Actor</name></actor><genre>剧情</genre><plot>Generated plot</plot></movie>',encoding='utf-8')
        self.before={p.name:p.read_bytes() for p in self.source.iterdir()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media_scan.main([str(self.source),'--output',str(self.output),'--no-image-metadata','--no-video-covers']),0)
        self.report=next(self.output.glob('scan-*'));self.doc=json.loads((self.report/'report.json').read_text(encoding='utf-8'));self.state=CenterState(self.output)

    def catalog(self):
        return center_catalog(self.doc,self.state.snapshot())

    def unchanged(self):
        self.assertEqual(self.before,{p.name:p.read_bytes() for p in self.source.iterdir()})

    def test_general_movie_video_and_photo_catalog_does_not_require_jav_identifier(self):
        catalog=self.catalog();by_kind={item['kind']:item for item in catalog['items']}
        self.assertEqual(set(by_kind),{'movie','video','photo'})
        self.assertEqual(by_kind['movie']['title'],'Example Film');self.assertIn('Example Actor',by_kind['movie']['metadata']['actors'])
        self.assertEqual(by_kind['video']['title'],'VID_20261022.mp4');self.unchanged()

    def test_favorites_tags_recent_and_collections_persist_without_touching_media(self):
        ids={item['id'] for item in self.catalog()['items']};identifier=next(iter(ids))
        self.state.update({'action':'favorite','id':identifier,'value':True},ids)
        self.state.update({'action':'labels','id':identifier,'tags':['旅行','家人']},ids)
        self.state.update({'action':'recent','id':identifier},ids)
        saved=self.state.update({'action':'create','name':'周末精选'},ids);collection=next(iter(saved['collections']))
        self.state.update({'action':'members','id':collection,'ids':list(ids)},ids)
        self.state.update({'action':'rename','id':collection,'name':'我的精选'},ids)
        current=CenterState(self.output).snapshot();self.assertTrue(current['favorites'][identifier]);self.assertEqual(current['labels'][identifier],['旅行','家人']);self.assertEqual(current['recent'],[identifier]);self.assertEqual(len(current['collections'][collection]['ids']),3)
        self.state.update({'action':'members','id':collection,'ids':[identifier],'remove':True},ids)
        self.state.update({'action':'delete_collection','id':collection},ids);self.assertEqual(self.state.snapshot()['collections'],{});self.unchanged()

    def test_unknown_ids_invalid_names_and_duplicate_unicode_names_never_overwrite_valid_state(self):
        self.state.update({'action':'create','name':'café'},set());path=self.output/NAME;before=path.read_bytes()
        for payload in ({'action':'favorite','id':'f'*64,'value':True},{'action':'create','name':'CAFE\u0301'},{'action':'create','name':'\n'},{'action':'labels','id':'f'*64,'tags':['a']},{'action':'create','name':'x'*81}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):self.state.update(payload,set())
            self.assertEqual(path.read_bytes(),before)
        self.unchanged()

    def test_corrupt_metadata_is_reported_and_preserved(self):
        path=self.output/NAME;path.write_bytes(b'{invalid');before=path.read_bytes()
        with self.assertRaises(ValueError):self.state.snapshot()
        with self.assertRaises(ValueError):self.state.update({'action':'create','name':'New'},set())
        self.assertEqual(path.read_bytes(),before)
        for value in ({'version':1,'favorites':[],'recent':[],'collections':{}},{'version':1,'favorites':{},'recent':['bad'],'collections':{}},{'version':1,'favorites':{},'recent':[],'collections':{'a'*32:{'name':'ok','ids':[{}]}}}):
            with self.assertRaises(ValueError):validate_state(value)

    def test_concurrent_metadata_updates_preserve_both_favorites(self):
        ids=[item['id'] for item in self.catalog()['items']];barrier=threading.Barrier(2);errors=[]
        def update(identifier):
            try:barrier.wait();CenterState(self.output).update({'action':'favorite','id':identifier,'value':True},set(ids))
            except Exception as error:errors.append(error)
        workers=[threading.Thread(target=update,args=(identifier,)) for identifier in ids[:2]]
        for worker in workers:worker.start()
        for worker in workers:worker.join(timeout=5)
        self.assertFalse(errors);self.assertTrue(all(not worker.is_alive() for worker in workers));self.assertEqual(len(self.state.snapshot()['favorites']),2)

    def test_restore_backup_includes_metadata_but_disables_original_file_actions(self):
        from media_actions import MediaActions
        ids={item['id'] for item in self.catalog()['items']};self.state.update({'action':'favorite','id':next(iter(ids)),'value':True},ids)
        body=library_backup.export_backup(self.output);files,_=library_backup.validate_backup(body)
        self.assertIn(NAME,files);target=library_backup.restore_backup(self.output,files)
        self.assertEqual(CenterState(target).snapshot()['favorites'],self.state.snapshot()['favorites'])
        restored=json.loads((target/self.report.name/'report.json').read_text(encoding='utf-8'));self.assertEqual(MediaActions(restored).records,{})
        self.unchanged()

    def test_api_serves_shipped_center_and_rejects_cross_origin_updates(self):
        with mock.patch.object(library_server,'ThreadingHTTPServer',MemoryServer):server,url=library_server.create_library_server(self.report,self.output)
        prefix=urlsplit(url).path.removesuffix('library.html');headers,raw=request(server,prefix+'api/center');self.assertIn('200 OK',headers);doc=json.loads(raw);identifier=doc['items'][0]['id']
        headers,raw=request(server,prefix+'center.html');self.assertIn('200 OK',headers);self.assertNotIn(b'@@MEDIA_CENTER@@',raw);self.assertIn(b'function centerFilter',raw);self.assertIn('整理模式'.encode(),raw)
        payload={'action':'favorite','id':identifier,'value':True}
        headers,_=request(server,prefix+'api/center','POST',payload);self.assertIn('403 Forbidden',headers)
        headers,_=request(server,prefix+'api/center','POST',payload,origin='http://evil.example');self.assertIn('403 Forbidden',headers)
        headers,_=request(server,prefix+'api/center','POST',payload,origin='http://127.0.0.1:43210');self.assertIn('200 OK',headers)
        self.assertTrue(self.state.snapshot()['favorites'][identifier]);self.unchanged()

    def test_windows_paths_keep_correct_names_and_directory_album_identity(self):
        document={'files':[{'path':'C:\\photos\\旅行\\photo.jpg','kind':'照片','bytes':1,'mtime':1},{'path':'D:\\photos\\旅行\\photo.jpg','kind':'照片','bytes':1,'mtime':1}], 'video_library':{'groups':[]}}
        catalog=center_catalog(document,self.state.snapshot());self.assertEqual([item['title'] for item in catalog['items']],['photo.jpg','photo.jpg']);self.assertEqual(len(catalog['folders']),2)

    def test_frontend_search_filters_safe_art_and_group_file_ids_are_shared_across_platforms(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Both CI platforms install Node for UI tests')
        code=r'''
const assert=require('assert/strict'),b=require(process.argv[1]);const items=[{id:'a',kind:'movie',title:'Movie',tags:['旅行'],files:[{id:'one',path:'C:\\movie.mp4'},{id:'two',path:'C:\\movie2.mp4'}],metadata:{actors:['演员']},personal:{note:'观影'},folder:'C:\\',mtime:1,favorite:true},{id:'b',kind:'photo',title:'photo.jpg',tags:[],files:[{id:'three',path:'/photo.jpg'}],metadata:{camera:'Camera'},personal:{note:''},folder:'/',mtime:2,favorite:false}];
const before=JSON.stringify(items);assert.equal(b.centerFilter(items,{view:'movies'}).length,1);assert.equal(b.centerFilter(items,{view:'photos'}).length,1);assert.equal(b.centerFilter(items,{view:'favorites'}).length,1);assert.equal(b.centerFilter(items,{view:'movies',query:'Camera'})[0].id,'b');assert.equal(b.centerFilter(items,{query:'演员'})[0].id,'a');assert.equal(b.centerFilter(items,{folder:'C:\\'})[0].id,'a');assert.equal(b.centerFilter(items,{tag:'旅行'})[0].id,'a');assert.deepEqual(b.centerMediaIds([items[0],items[0]]),['one','two']);assert.equal(b.centerArt('https://evil/image.png'),'');assert.equal(b.centerArt('../previews/a.png'),'');assert.equal(b.centerArt('previews/a.png'),'previews/a.png');assert.equal(JSON.stringify(items),before);
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).with_name('media_center.js'))],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_gallery_opens_detail_in_browse_mode_and_only_selects_in_manage_mode(self):
        self.run_ui(r'''
assert.equal($('center-title').textContent,'电影');const wall=$('center-content').children[0];assert.equal(wall.children.length,1);assert(!wall.children[0].children.some(n=>n.className==='pick'));
wall.children[0].children[0].onclick();assert($('media-detail').open);assert(!$('detail-content').children.some(n=>n.textContent.includes('删除')));assert($('detail-actions').children.some(n=>n.textContent==='☆ 收藏'));
$('media-detail').close();$('manage-toggle').onclick();assert(!$('manage-bar').hidden);assert.equal($('manage-toggle').attrs['aria-pressed'],'true');
const card=$('center-content').children[0].children[0],pick=card.children.find(n=>n.className==='pick').children[0];pick.checked=true;pick.onchange();assert($('selected-count').textContent.includes('1'));assert(!$('delete-selected').disabled);
$('delete-selected').onclick();assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');assert.deepEqual(Array.from(operations[0].ids),['a'.repeat(64)]);
location.hash='#photos';events.hashchange();assert($('manage-bar').hidden);assert.equal($('center-title').textContent,'照片');assert.equal($('center-content').children[0].children.length,1);
''')

    def test_actual_search_crosses_media_types_and_favorite_rpc_never_starts_file_operations(self):
        self.run_ui(r'''
$('query').value='Sample Camera';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-title').textContent,'搜索结果');const card=$('center-content').children[0].children[0];assert(card.children.some(n=>n.textContent==='Photo'));
card.children[0].onclick();const favorite=$('detail-actions').children.find(n=>n.textContent==='☆ 收藏');await favorite.onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'api/center');assert.equal(calls[0].body.action,'favorite');assert.equal(operations.length,0);
''')

    def test_open_uses_safe_action_and_immediately_refreshes_recent_metadata(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();
const info=$('detail-content').children[0].children[1],file=info.children.find(n=>n.className==='file');
await file.children.find(n=>n.textContent==='播放').onclick();assert.equal(calls[0].path,'api/media/action');assert.equal(calls[0].body.action,'open');assert.equal(calls[1].path,'api/center');assert.equal(calls[1].body.action,'recent');assert.equal(operations.length,0);
$('media-detail').close();location.hash='#home';events.hashchange();assert($('center-content').children.some(n=>n.textContent==='最近打开'));
''')

    def run_ui(self, assertions):
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
class E{constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.textContent='';this.value='';this.open=false;this.hidden=false;this.attrs={};this.events={};this.style={};this.classList={add(){}};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}addEventListener(k,v){this.events[k]=v;}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);},calls=[],operations=[],events={},location={hash:'#movies'},state={version:1,favorites:{},labels:{},recent:[],collections:{}};
const movie={id:'c'.repeat(64),kind:'movie',title:'Film',files:[{id:'a'.repeat(64),name:'Film.mp4',path:'C:\\movie.mp4',bytes:12,mtime:1}],preview:'covers/a.png',folder:'C:\\',mtime:1,metadata:{actors:['Actor']},tags:[],local_tags:[],personal:{rating:0,note:''},tag_key:'d'.repeat(64),favorite:false,bytes:12};
const photo={...movie,id:'e'.repeat(64),kind:'photo',title:'Photo',files:[{id:'b'.repeat(64),name:'Photo.jpg',path:'/photo.jpg',bytes:12,mtime:2}],metadata:{camera:'Sample Camera',width:20,height:10},tag_key:'',folder:'/',mtime:2};
const scope={console,location,document:{body:new E(),createElement:tag=>new E(tag),getElementById:$},window:{addEventListener:(k,v)=>events[k]=v},reportActionOpen:(action,ids)=>operations.push({action,ids}),fetch:async(path,options)=>{
if(options?.body){const body=JSON.parse(options.body);calls.push({path,body});if(body.action==='favorite')state.favorites[body.id]=body.value;if(body.action==='recent')state.recent=[body.id];return {ok:true,json:async()=>state};}
return {ok:true,json:async()=>path==='api/media'?{available:true,ids_by_path:{'C:\\movie.mp4':'a'.repeat(64),'/photo.jpg':'b'.repeat(64)}}:{items:[movie,photo].map(i=>({...i,favorite:state.favorites[i.id]||false})),state,folders:{'C:\\':[movie.id],'/':[photo.id]},recent:state.recent,restored:false}};
}};vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
(async()=>{await new Promise(resolve=>setImmediate(resolve));if($('center-error').textContent)throw Error($('center-error').textContent);
'''+assertions+r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).with_name('media_center.js'))],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
