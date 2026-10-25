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
from movie_grouping import source_path
from media_center import CenterState, center_catalog, validate_state, directory_catalog, NAME
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

    def test_directory_tree_stays_inside_scanned_roots_and_preserves_duplicate_names(self):
        paths=['/samples/旅行/a.jpg','/samples/旅行/子目录/b.jpg','/samples/旅行2/c.jpg',
               r'C:\Media\旅行\a.jpg',r'D:\Media\旅行\a.jpg',r'\\server\share\旅行\a.jpg',
               '/samples/café/a.jpg','/samples/café/b.jpg','/samples/Case/a.jpg','/samples/case/b.jpg']
        items=[{'id':str(index),'files':[{'path':path}]} for index,path in enumerate(paths)]
        roots=['/samples',r'C:\Media',r'D:\Media',r'\\server\share']
        with mock.patch.object(Path,'iterdir',side_effect=AssertionError('No filesystem traversal')):
            nodes=directory_catalog(items,roots)
        self.assertEqual({path for path,node in nodes.items() if not node['parent']},{str(source_path(root)) for root in roots})
        self.assertEqual(nodes['/samples/旅行']['ids'],['0']);self.assertEqual(nodes['/samples/旅行/子目录']['parent'],'/samples/旅行')
        self.assertNotIn('/samples/旅行2',nodes['/samples/旅行']['children'])
        self.assertEqual(nodes[r'C:\Media\旅行']['ids'],['3']);self.assertEqual(nodes[r'D:\Media\旅行']['ids'],['4'])
        self.assertIn('/samples/café',nodes);self.assertIn('/samples/café',nodes);self.assertIn('/samples/Case',nodes);self.assertIn('/samples/case',nodes)
        self.assertEqual(nodes[r'\\server\share\旅行']['parent'],str(source_path(r'\\server\share')))
        self.unchanged()

    def test_multi_directory_movie_appears_in_each_album_once_and_old_reports_have_tree(self):
        paths=['/samples/A/part1.mp4','/samples/B/part2.mp4','/samples/B/part3.mp4'];identifier='a'*64
        doc={'roots':['/samples'],'files':[{'path':path,'kind':'视频','bytes':1,'mtime':1} for path in paths],
             'video_library':{'groups':[{'group_key':identifier,'metadata':{'title':'Film'},'files':[{'path':path} for path in paths]}]}}
        catalog=center_catalog(doc,self.state.snapshot());self.assertEqual(catalog['folders'],{'/samples/A':[identifier],'/samples/B':[identifier]})
        self.assertEqual(catalog['directories']['/samples/B']['ids'],[identifier]);self.assertEqual(catalog['directories']['/samples']['ids'],[])
        del doc['roots'];old=center_catalog(doc,self.state.snapshot());self.assertIn('/',old['directories']);self.assertEqual(old['directories']['/']['parent'],'')

    def test_frontend_search_filters_safe_art_and_group_file_ids_are_shared_across_platforms(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Both CI platforms install Node for UI tests')
        code=r'''
const assert=require('assert/strict'),b=require(process.argv[1]);const items=[{id:'a',kind:'movie',title:'Movie',tags:['旅行'],files:[{id:'one',path:'C:\\movie.mp4'},{id:'two',path:'C:\\movie2.mp4'}],metadata:{actors:['演员']},personal:{note:'观影'},folder:'C:\\',mtime:1,favorite:true},{id:'b',kind:'photo',title:'photo.jpg',tags:[],files:[{id:'three',path:'/photo.jpg'}],metadata:{camera:'Camera'},personal:{note:''},folder:'/',mtime:2,favorite:false}];
const before=JSON.stringify(items);assert.equal(b.centerFilter(items,{view:'movies'}).length,1);assert.equal(b.centerFilter(items,{view:'photos'}).length,1);assert.equal(b.centerFilter(items,{view:'favorites'}).length,1);assert.equal(b.centerFilter(items,{view:'movies',query:'Camera',global:true})[0].id,'b');assert.equal(b.centerFilter(items,{query:'演员'})[0].id,'a');assert.equal(b.centerFilter(items,{folder:'C:\\'})[0].id,'a');assert.equal(b.centerFilter(items,{tag:'旅行'})[0].id,'a');assert.deepEqual(b.centerMediaIds([items[0],items[0]]),['one','two']);assert.equal(b.centerArt('https://evil/image.png'),'');assert.equal(b.centerArt('../previews/a.png'),'');assert.equal(b.centerArt('previews/a.png'),'previews/a.png');assert.equal(JSON.stringify(items),before);
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
$('search-scope').value='all';$('query').value='Sample Camera';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-title').textContent,'全部媒体 · 搜索结果');const card=$('center-content').children[0].children[0];assert(card.children.some(n=>n.textContent==='Photo'));
card.children[0].onclick();const favorite=$('detail-actions').children.find(n=>n.textContent==='☆ 收藏');await favorite.onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'api/center');assert.equal(calls[0].body.action,'favorite');assert.equal(operations.length,0);
''')

    def test_open_uses_safe_action_and_immediately_refreshes_recent_metadata(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();
const info=$('detail-content').children[0].children[1],file=info.children.find(n=>n.className==='file');
await file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='播放').onclick();assert.equal(calls[0].path,'api/media/action');assert.equal(calls[0].body.action,'open');assert.equal(calls[1].path,'api/center');assert.equal(calls[1].body.action,'recent');assert.equal(operations.length,0);
$('media-detail').close();location.hash='#home';events.hashchange();assert($('center-content').children.some(n=>n.textContent==='最近打开'));
''')

    def test_current_page_search_keeps_media_kind_and_favorites_until_explicit_global_search(self):
        self.run_ui(r"""
movie.title=photo.title='共同标题';location.hash='#photos';events.hashchange();await $('center-refresh').onclick();
$('query').value='共同标题';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-title').textContent,'照片 · 搜索结果');assert.equal($('center-content').children[0].children.length,1);
$('search-scope').value='all';$('search-scope').onchange();assert.equal($('center-content').children[0].children.length,2);assert($('center-subtitle').textContent.includes('全局搜索'));$('media-kind').value='photo';$('media-kind').onchange();assert.equal($('center-content').children[0].children.length,1);
$('clear-search').onclick();assert.equal($('center-title').textContent,'照片');location.hash='#favorites';events.hashchange();state.favorites[movie.id]=true;await $('center-refresh').onclick();$('query').value='共同标题';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-content').children[0].children.length,1);assert($('center-content').children[0].children[0].children.some(n=>n.textContent===movie.title));assert.equal(calls.length,0);assert.equal(operations.length,0);
""")

    def test_folder_search_preserves_path_subfolder_choice_and_returns_from_global_without_operations(self):
        self.run_ui(r"""
photo.title='旅行照片';photo.folder='/samples/旅行';photo.files[0].path='/samples/旅行/photo.jpg';const nested={...photo,id:'1'.repeat(64),folder:'/samples/旅行/子目录',files:[{...photo.files[0],id:'2'.repeat(64),path:'/samples/旅行/子目录/nested.jpg'}]},outside={...photo,id:'3'.repeat(64),folder:'/other/旅行',files:[{...photo.files[0],id:'4'.repeat(64),path:'/other/旅行/outside.jpg'}]};items=[photo,nested,outside];directories={'/samples/旅行':{name:'旅行',parent:'',ids:[photo.id],children:['/samples/旅行/子目录']},'/samples/旅行/子目录':{name:'子目录',parent:'/samples/旅行',ids:[nested.id],children:[]},'/other/旅行':{name:'旅行',parent:'',ids:[outside.id],children:[]}};
location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children.find(card=>card.attrs['aria-label'].includes('/samples/旅行')).onclick();
$('query').value='旅行照片';$('center-search').onsubmit({preventDefault(){}});const wall=()=>$('center-content').children.find(n=>n.className.startsWith('wall'));assert.equal(wall().children.length,1);assert($('center-breadcrumb').children.some(n=>n.textContent==='/samples/旅行'));assert(!$('recursive-label').hidden);
$('include-subfolders').checked=true;$('include-subfolders').onchange();assert.equal(wall().children.length,2);await $('center-refresh').onclick();assert.equal(wall().children.length,2);assert.equal($('query').value,'旅行照片');
$('search-scope').value='all';$('search-scope').onchange();assert.equal(wall().children.length,3);assert(wall().children.every(card=>card.children.some(n=>n.className==='meta search-path'&&n.attrs.title)));assert(wall().children.some(card=>card.children[0].attrs['aria-label'].includes('/other/旅行')));assert($('recursive-label').hidden);$('center-breadcrumb').children[0].onclick();assert.equal($('query').value,'');assert.equal($('center-title').textContent,'旅行');assert($('include-subfolders').checked);assert($('center-breadcrumb').children.some(n=>n.textContent==='/samples/旅行'));assert.equal(calls.length,0);assert.equal(operations.length,0);
""")

    def test_search_keeps_collection_and_tag_context_but_global_results_cannot_remove_collection_members(self):
        self.run_ui(r"""
state.collections['1'.repeat(32)]={name:'我的相册',ids:[photo.id]};movie.tags=photo.tags=['旅行'];movie.title=photo.title='周末';location.hash='#collections';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='我的相册')).onclick();
$('query').value='周末';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-title').textContent,'我的相册 · 搜索结果');assert.equal($('center-content').children[0].children.length,1);$('manage-toggle').onclick();const pick=$('center-content').children[0].children[0].children.find(n=>n.className==='pick').children[0];pick.checked=true;pick.onchange();assert($('selected-count').textContent.includes('1'));
$('search-scope').value='all';$('search-scope').onchange();assert($('selected-count').textContent.includes('0'));assert($('remove-collection').hidden);await $('remove-collection').onclick();assert.equal(calls.length,0);$('clear-search').onclick();assert.equal($('center-title').textContent,'我的相册');assert(!$('remove-collection').hidden);
location.hash='#tags';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();$('query').value='周末';$('center-search').onsubmit({preventDefault(){}});assert($('center-subtitle').textContent.includes('标签：旅行'));$('clear-search').onclick();assert($('center-subtitle').textContent.includes('标签：旅行'));assert.equal(operations.length,0);
""")

    def test_review_search_retains_whole_group_actions_and_similar_mode_on_return(self):
        self.run_ui(r"""
const second={...photo,id:'1'.repeat(64),title:'Second',files:[{...photo.files[0],id:'2'.repeat(64),path:'C:\\Copies\\archive.jpg'}]};photo.files[0].path='/trips/Café.jpg';items=[photo,second];access.ids_by_path[photo.files[0].path]=photo.files[0].id;access.ids_by_path[second.files[0].path]=second.files[0].id;photo.tags=['旅行'];
const files=items.map(item=>({...item.files[0],name:item.title,kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata,preview:item.preview}));review={exact:[{sha256:'3'.repeat(64),files}],similar:[{distance:3,files}],warnings:[]};location.hash='#review';events.hashchange();await $('center-refresh').onclick();
$('query').value='CAFE\u0301';$('center-search').onsubmit({preventDefault(){}});const group=()=>$('center-content').children.find(n=>n.className==='review-group'),cards=()=>group().children.find(n=>n.className==='review-files').children;assert.equal(cards().length,2);assert.equal($('center-title').textContent,'重复与相似');assert($('center-subtitle').textContent.includes('1 组'));cards()[0].children.find(n=>n.className==='actions').children.find(n=>n.textContent==='保留这份，勾选其余').onclick();$('center-content').children.find(n=>n.className==='actions review-batch').children.find(n=>n.textContent==='删除所选…').onclick();assert.deepEqual(Array.from(operations[0].ids),[second.files[0].id]);assert.equal(calls.length,0);
$('query').value='旅行';$('center-search').onsubmit({preventDefault(){}});assert.equal(cards().length,2);$('center-content').children[0].children[1].onclick();assert($('center-content').children.some(n=>n.textContent.includes('视觉相似候选')));assert.equal(cards().length,2);
$('search-scope').value='all';$('search-scope').onchange();assert(!group());assert(!$('manage-toggle').hidden);$('center-breadcrumb').children[0].onclick();assert(group());assert($('manage-toggle').hidden);assert($('center-content').children.some(n=>n.textContent.includes('视觉相似候选')));assert.equal(calls.length,0);
""")

    def test_review_group_search_combines_filters_per_file_and_sorts_without_mutation(self):
        self.run_ui(r"""
const b=require(process.argv[1]),file=(id,name,bytes,mtime,year,kind='photo')=>({id,name,path:'C:\\Generated\\'+name,bytes,mtime,media_kind:kind,metadata:{year,genres:['旅行']}});
const first={files:[file('a','Photo10.jpg',10,1,'2024'),file('b','KEEP.jpg',10,4,'2025')]},second={files:[file('c','Photo2.jpg',20,8,'2025')]},third={files:[file('d','电影.mp4',30,2,'2025','movie')]},review={exact:[first,second,third],similar:[]},before=JSON.stringify(review);
assert.deepEqual(b.centerReviewGroups(review,{query:'keep',year:'2024'}),[]);assert.equal(b.centerReviewGroups(review,{query:'keep',year:'2025'})[0],first);assert.equal(b.centerReviewGroups(review,{query:'C:\\Generated',kind:'movie'})[0],third);assert.deepEqual(b.centerReviewGroups(review,{order:'size'}),[third,second,first]);assert.deepEqual(b.centerReviewGroups(review,{order:'recent'}),[second,first,third]);assert.equal(b.centerReviewGroups(review,{order:'oldest'})[0],first);assert.equal(b.centerReviewGroups(review,{order:'name',kind:'photo'})[0],second);assert.equal(JSON.stringify(review),before);
""")

    def test_review_keeps_full_hash_and_visual_candidates_distinct_and_projects_success_records(self):
        paths=['/generated/a.jpg','/generated/b.jpg','/generated/c.jpg'];digest='a'*64
        doc={'roots':['/generated'],'files':[{'path':path,'kind':'照片','bytes':12,'mtime':1,'sha256':digest if index<2 else 'c'*64} for index,path in enumerate(paths)],
             'duplicates':[{'paths':paths[:2],'bytes_each':12,'sha256':digest}],
             'similar':{'pairs':[{'left':paths[0],'right':paths[2],'distance':2},{'left':paths[2],'right':paths[0],'distance':2},{'left':paths[0],'right':paths[0],'distance':0}],'truncated':False}}
        before=json.dumps(doc,sort_keys=True);catalog=center_catalog(doc,self.state.snapshot())
        self.assertEqual(len(catalog['review']['exact']),1);self.assertEqual(len(catalog['review']['similar']),1)
        self.assertEqual(catalog['review']['exact'][0]['sha256'],digest);self.assertNotIn('sha256',catalog['review']['similar'][0]);self.assertTrue(catalog['review']['warnings'])
        projected=center_catalog(doc,self.state.snapshot(),handled_trash=[paths[0]])
        self.assertEqual(projected['hidden_files'],1);self.assertEqual(len(projected['items']),2)
        self.assertEqual(projected['review']['exact'],[]);self.assertEqual(projected['review']['similar'],[])
        self.assertEqual(projected['folders']['/generated'],[media_id(path) for path in paths[1:]])
        doc['files'][1]['sha256']='f'*64;invalid=center_catalog(doc,self.state.snapshot());self.assertEqual(invalid['review']['exact'],[]);self.assertTrue(invalid['review']['warnings'])
        doc['files'][1]['sha256']=digest;self.assertEqual(json.dumps(doc,sort_keys=True),before);doc['similar']['truncated']=True;self.assertIn('上限',center_catalog(doc,self.state.snapshot())['review']['similar_note']);doc['similar']['enabled']=False;self.assertIn('未启用',center_catalog(doc,self.state.snapshot())['review']['similar_note']);self.unchanged()

    def test_center_api_uses_validated_operation_history_and_reports_running_and_warning_states(self):
        path=next(record['path'] for record in self.doc['files'] if record['kind']=='照片')
        history={'jobs':[{'status':'running'}],'warnings':['Review record'],'handled':{'copy':[],'trash':[path]}}
        with mock.patch.object(library_server,'ThreadingHTTPServer',MemoryServer),mock.patch.object(library_server.FileOperations,'history',return_value=history):
            server,url=library_server.create_library_server(self.report,self.output);prefix=urlsplit(url).path.removesuffix('library.html')
            headers,body=request(server,prefix+'api/center');self.assertIn('200 OK',headers);catalog=json.loads(body)
        self.assertEqual(catalog['hidden_files'],1);self.assertTrue(catalog['operation_state']['running']);self.assertEqual(catalog['operation_state']['warnings'],['Review record'])
        self.assertFalse(any(file['path']==path for item in catalog['items'] for file in item['files']));self.unchanged()

    def test_confirmed_mock_trash_updates_center_from_real_journal_without_changing_source(self):
        import file_operations
        import time
        path=next(record['path'] for record in self.doc['files'] if record['kind']=='照片');identifier=media_id(path)
        with mock.patch.object(library_server,'ThreadingHTTPServer',MemoryServer),mock.patch.object(file_operations,'trash_helper',return_value=Path('/generated/mock-trash')),mock.patch.object(file_operations,'trash_one',return_value={'trashed_path':'/generated/mock-trash/photo.jpg'}) as trash:
            server,url=library_server.create_library_server(self.report,self.output);prefix=urlsplit(url).path.removesuffix('library.html');origin='http://127.0.0.1:43210'
            headers,body=request(server,prefix+'api/operations/preview','POST',{'mode':'trash','ids':[identifier]},origin=origin);self.assertIn('200 OK',headers);preview=json.loads(body)
            trash.assert_not_called()
            headers,body=request(server,prefix+'api/operations/start','POST',{'token':preview['token']},origin=origin);self.assertIn('200 OK',headers);job=json.loads(body)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                _,body=request(server,prefix+'api/operations/'+job['id']);job=json.loads(body)
                if job['status']!='running':break
                time.sleep(.01)
            self.assertEqual(job['status'],'complete');trash.assert_called_once()
            _,body=request(server,prefix+'api/center');catalog=json.loads(body)
            self.assertEqual(catalog['hidden_files'],1);self.assertFalse(catalog['operation_state']['running']);self.assertFalse(any(file['path']==path for item in catalog['items'] for file in item['files']))
        self.unchanged()

    def test_manage_comparison_and_return_refresh_preserve_scope_and_remove_only_successful_item(self):
        self.run_ui(r"""
const second={...photo,id:'f'.repeat(64),title:'Second',files:[{...photo.files[0],id:'1'.repeat(64),path:'/second.jpg'}]};items.push(second);access.ids_by_path['/second.jpg']='1'.repeat(64);location.hash='#photos';events.hashchange();await $('center-refresh').onclick();
$('manage-toggle').onclick();const picks=$('center-content').children[0].children.map(card=>card.children.find(n=>n.className==='pick').children[0]);for(const pick of picks){pick.checked=true;pick.onchange();}
assert(!$('compare-selected').disabled);$('compare-selected').onclick();assert.equal(operations[0].action,'compare');assert.deepEqual(Array.from(operations[0].ids),['b'.repeat(64),'1'.repeat(64)]);assert.equal(calls.length,0);
items=items.filter(item=>item.id!==photo.id);hiddenFiles=1;await operations[0].onClose();assert.equal($('center-title').textContent,'照片');assert.equal($('center-content').children[0].children.length,1);assert($('selected-count').textContent.includes('1'));assert(!$('center-status').hidden);assert.equal(scope.window.scrollY,350);assert.equal(calls.length,0);
""")

    def test_direct_card_delete_is_guarded_during_jobs_warnings_and_refresh_failure(self):
        self.run_ui(r"""
$('manage-toggle').onclick();const trash=()=>$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='删除…');
assert(!trash().disabled);operationState={running:true,warnings:[]};await $('center-refresh').onclick();assert(trash().disabled);trash().onclick();assert.equal(operations.length,0);assert.equal(typeof pendingRefresh,'function');
operationState={running:false,warnings:['Check records']};await pendingRefresh();assert(trash().disabled);assert($('center-status').textContent.includes('暂停清理'));
operationState={running:false,warnings:[]};await $('center-refresh').onclick();assert(!trash().disabled);failCenter=true;await $('center-refresh').onclick();assert(trash().disabled);trash().onclick();assert.equal(operations.length,0);assert($('center-error').textContent.includes('Disconnected'));
failCenter=false;await $('center-refresh').onclick();trash().onclick();assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');assert.equal(calls.length,0);
""")

    def test_review_tabs_compare_and_delete_use_same_confirmation_without_posting(self):
        self.run_ui(r"""
const second={...photo,id:'f'.repeat(64),title:'Second',files:[{...photo.files[0],id:'1'.repeat(64),path:'/second.jpg'}]};items.push(second);access.ids_by_path['/second.jpg']='1'.repeat(64);
const files=[photo,second].map(item=>({...item.files[0],name:item.title,kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata,preview:item.preview}));review={exact:[{sha256:'2'.repeat(64),files}],similar:[{distance:2,files}],warnings:[]};location.hash='#review';events.hashchange();await $('center-refresh').onclick();
const group=()=>$('center-content').children.find(n=>n.className==='review-group');assert($('manage-toggle').hidden);assert($('center-content').children.some(n=>n.textContent.includes('SHA-256')));
group().children.find(n=>n.className==='actions review-group-actions').children.find(n=>n.textContent==='对比这两张').onclick();assert.equal(operations[0].action,'compare');assert.equal(operations[0].ids.length,2);
const first=group().children.find(n=>n.className==='review-files').children[0];first.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='删除…').onclick();assert.equal(operations[1].action,'delete');assert.deepEqual(Array.from(operations[1].ids),[photo.files[0].id]);
$('center-content').children[0].children[1].onclick();assert($('center-content').children.some(n=>n.textContent.includes('视觉相似候选')));assert.equal(calls.length,0);
""")

    def test_quick_favorite_and_play_are_explicit_actions_and_keep_scroll(self):
        self.run_ui(r"""
const actions=$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions');assert(!actions.children.some(n=>n.textContent==='删除…'));await actions.children.find(n=>n.textContent==='☆ 收藏').onclick();assert.equal(calls[0].body.action,'favorite');assert.equal(scope.window.scrollY,350);
const refreshed=$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions');await refreshed.children.find(n=>n.textContent==='播放').onclick();assert.equal(calls[1].path,'api/media/action');assert.equal(calls[1].body.action,'open');assert.equal(calls[2].body.action,'recent');assert.equal(operations.length,0);
""")

    def test_keep_chosen_exact_copy_previews_only_others_and_never_applies_to_similar(self):
        self.run_ui(r"""
const copies=[photo,...['1','2'].map((digit,index)=>({...photo,id:digit.repeat(64),title:'Copy '+index,files:[{...photo.files[0],id:(index?'3':'4').repeat(64),path:'C:\\生成样例\\副本'+index+'.jpg'}]}))];items=copies;for(const item of copies)access.ids_by_path[item.files[0].path]=item.files[0].id;
const files=copies.map(item=>({...item.files[0],name:item.title,kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata,preview:item.preview}));review={exact:[{sha256:'5'.repeat(64),files}],similar:[{distance:0,files:files.slice(0,2)}],warnings:[]};location.hash='#review';events.hashchange();await $('center-refresh').onclick();const original=JSON.stringify(review);
const group=()=>$('center-content').children.find(n=>n.className==='review-group'),cards=()=>group().children.find(n=>n.className==='review-files').children,keep=card=>card.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='保留这份，勾选其余');
assert(!keep(cards()[1]).disabled);keep(cards()[1]).onclick();$('center-content').children.find(n=>n.className==='actions review-batch').children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');assert.deepEqual(Array.from(operations[0].ids),[files[0].id,files[2].id]);assert(operations[0].summary.includes(files[1].path));assert(operations[0].summary.includes('2 份'));assert.equal(JSON.stringify(review),original);assert.equal(calls.length,0);
operationState={running:true,warnings:[]};await $('center-refresh').onclick();assert(keep(cards()[1]).disabled);keep(cards()[1]).onclick();assert.equal(operations.length,1);
operationState={running:false,warnings:[]};review.exact=[];items=[copies[1]];hiddenFiles=2;await operations[0].onClose();assert($('center-content').children.some(n=>n.className==='empty'));assert($('center-status').textContent.includes('2 个文件'));assert.equal($('center-title').textContent,'重复与相似');assert.equal(scope.window.scrollY,350);
$('center-content').children[0].children[1].onclick();assert(cards().every(card=>!keep(card)));assert.equal(calls.length,0);
""")

    def test_keep_copy_limits_large_groups_and_works_for_movie_files(self):
        self.run_ui(r"""
const copies=Array.from({length:202},(_,n)=>({...movie,id:(n+10).toString(16).padStart(64,'0'),files:[{...movie.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'C:\\生成样例\\影片'+n+'.mp4'}]}));items=copies;for(const item of copies)access.ids_by_path[item.files[0].path]=item.files[0].id;
const files=copies.map(item=>({...item.files[0],name:item.title,kind:'movie',media_kind:'movie',owner:item.id,metadata:item.metadata}));review={exact:[{sha256:'5'.repeat(64),files}],similar:[],warnings:[]};location.hash='#review';events.hashchange();await $('center-refresh').onclick();const keep=()=>$('center-content').children.find(n=>n.className==='review-group').children.find(n=>n.className==='review-files').children[0].children.find(n=>n.className==='actions').children.find(n=>n.textContent==='保留这份，勾选其余');
assert(keep().disabled);assert(keep().attrs.title.includes('超过200'));keep().onclick();assert.equal(operations.length,0);
review.exact[0].files=files.slice(0,201);await $('center-refresh').onclick();assert(!keep().disabled);keep().onclick();$('center-content').children.find(n=>n.className==='actions review-batch').children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations.length,1);assert.equal(operations[0].ids.length,200);assert(!operations[0].ids.includes(files[0].id));assert.equal(calls.length,0);
""")

    def test_year_genre_size_order_and_recursive_folder_ids_do_not_change_input(self):
        self.run_ui(r"""
const b=require(process.argv[1]),rows=[{...movie,id:'one',title:'Film10',mtime:10,bytes:100,metadata:{year:'2025',genres:['剧情']}},{...movie,id:'two',title:'Film2',mtime:5,bytes:200,metadata:{year:'2024',genres:['科幻']}},{...photo,id:'three',metadata:{date_original:'2025:10:22 12:00:00'},mtime:2,bytes:50}];
const before=JSON.stringify(rows);assert.equal(b.centerFilter(rows,{year:'2025',genre:'剧情'}).length,1);assert.equal(b.centerFilter(rows,{year:'2025'}).length,2);assert.equal(b.centerFilter(rows,{order:'size'})[0].id,'two');assert.equal(b.centerFilter(rows,{order:'oldest'})[0].id,'three');assert.equal(b.centerFilter(rows,{view:'movies',order:'name'})[0].id,'two');assert.equal(b.centerYear({...photo,metadata:{}}),'');assert.equal(JSON.stringify(rows),before);
const dirs={'/a':{ids:['one'],children:['/a/sub']},'/a/sub':{ids:['one','two'],children:['/a']},'/ab':{ids:['three'],children:[]}};assert.deepEqual([...b.centerFolderIds(dirs,'/a')],['one']);assert.deepEqual([...b.centerFolderIds(dirs,'/a',true)].sort(),['one','two']);assert.equal(b.centerFolderIds(dirs,'/missing',true).size,0);
""")

    def test_resource_navigation_subfolders_filters_and_refresh_keep_location_without_operations(self):
        self.run_ui(r"""
photo.files=[{...photo.files[0],path:'/samples/旅行/Photo.jpg'}];photo.folder='/samples/旅行';photo.metadata.date_original='2025-10-22';
const nested={...photo,id:'f'.repeat(64),title:'Nested',folder:'/samples/旅行/子目录',files:[{...photo.files[0],id:'1'.repeat(64),path:'/samples/旅行/子目录/Nested.jpg'}]};items=[photo,nested];folders={'/samples/旅行':[photo.id],'/samples/旅行/子目录':[nested.id]};directories={'/samples':{name:'samples',parent:'',ids:[],children:['/samples/旅行']},'/samples/旅行':{name:'旅行',parent:'/samples',ids:[photo.id],children:['/samples/旅行/子目录']},'/samples/旅行/子目录':{name:'子目录',parent:'/samples/旅行',ids:[nested.id],children:[]}};
location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();assert.equal($('center-title').textContent,'samples');$('center-content').children[0].children[0].onclick();assert.equal($('center-title').textContent,'旅行');assert(!$('recursive-label').hidden);
const mediaWall=()=>$('center-content').children.find(n=>['wall','wall photo-wall'].includes(n.className));assert.equal(mediaWall().children.length,1);assert.equal(mediaWall().children[0].children.find(n=>n.tagName==='H3').textContent,'Photo');
$('manage-toggle').onclick();const pick=mediaWall().children[0].children.find(n=>n.className==='pick').children[0];pick.checked=true;pick.onchange();assert($('selected-count').textContent.includes('1'));
$('include-subfolders').checked=true;$('include-subfolders').onchange();assert.equal(mediaWall().children.length,2);assert($('center-subtitle').textContent.includes('含子目录 2 项媒体'));assert($('selected-count').textContent.includes('0'));$('media-order').value='name';$('media-order').onchange();assert.equal(mediaWall().children[0].children.find(n=>n.tagName==='H3').textContent,'Nested');
$('filter-year').value='1999';$('filter-year').onchange();assert.equal(mediaWall().children.length,0);$('reset-filters').onclick();assert.equal($('center-title').textContent,'旅行');assert.equal(mediaWall().children.length,2);await $('center-refresh').onclick();assert.equal($('center-title').textContent,'旅行');assert.equal(mediaWall().children.length,2);
$('center-breadcrumb').children[0].onclick();assert.equal($('center-title').textContent,'samples');assert.equal(operations.length,0);assert.equal(calls.length,0);
""")

    def test_same_named_albums_show_full_paths_and_membership_not_first_file_directory(self):
        self.run_ui(r"""
const second={...photo,id:'f'.repeat(64),title:'Second',folder:'/A/旅行'};items=[photo,second];folders={'/A/旅行':[photo.id],'/B/旅行':[second.id]};location.hash='#collections';events.hashchange();await $('center-refresh').onclick();
const cards=$('center-content').children[0].children;assert.equal(cards.length,2);assert(cards.some(card=>card.children.some(n=>n.textContent==='/A/旅行')));assert(cards.some(card=>card.children.some(n=>n.textContent==='/B/旅行')));
cards.find(card=>card.attrs['aria-label'].includes('/B/旅行')).onclick();assert.equal($('center-content').children[0].children.length,1);assert($('center-content').children[0].children[0].children.some(n=>n.textContent==='Second'));assert.equal(calls.length,0);assert.equal(operations.length,0);
""")

    def test_resource_folder_pagination_reaches_every_root(self):
        self.run_ui(r"""
directories={};for(let n=0;n<61;n++)directories['/sample'+n]={name:'sample'+n,parent:'',children:[],ids:[photo.id]};location.hash='#resources';events.hashchange();await $('center-refresh').onclick();assert.equal($('center-content').children[0].children.length,50);
const pager=$('center-content').children.find(n=>n.className==='pager');pager.children[2].onclick();assert.equal($('center-content').children[0].children.length,11);assert($('center-content').children[0].children.some(card=>card.attrs['aria-label'].includes('/sample60')));const finalPager=$('center-content').children.find(n=>n.className==='pager');assert(finalPager.children[2].disabled);finalPager.children[0].onclick();assert.equal($('center-content').children[0].children.length,50);assert.equal(calls.length,0);assert.equal(operations.length,0);
""")

    def test_video_facts_filters_apply_to_same_file_and_preserve_complete_movie(self):
        self.run_ui(r'''
const second={...movie,id:'f'.repeat(64),title:'Long movie',files:[{...movie.files[0],id:'1'.repeat(64),video:{duration_seconds:3600,width:3840,height:2160}}]};
movie.files[0].video={duration_seconds:90,width:1920,height:1080};movie.files.push({...movie.files[0],id:'2'.repeat(64),name:'Alternate.mp4',video:{duration_seconds:7200,width:1280,height:720}});items.push(second);await $('center-refresh').onclick();
$('filter-duration').value='long';$('filter-duration').onchange();$('filter-resolution').value='fhd';$('filter-resolution').onchange();assert.equal($('center-content').children[0].children.length,0);
$('filter-duration').value='short';$('filter-duration').onchange();const card=$('center-content').children[0].children[0];assert(card.children.some(n=>n.textContent.includes('2 个视频文件')));card.children[0].onclick();
const files=$('detail-content').children[0].children[1].children.filter(n=>n.className==='file');assert.equal(files.length,2);assert(files[0].children.some(n=>n.textContent.includes('1:30 · 1920 × 1080')));assert(files[1].children.some(n=>n.textContent.includes('2:00:00 · 1280 × 720')));assert.equal(operations.length,0);
$('media-detail').close();$('reset-filters').onclick();assert.equal($('filter-duration').value,'');assert.equal($('filter-resolution').value,'');assert.equal($('center-content').children[0].children.length,2);
location.hash='#photos';events.hashchange();assert.equal($('center-content').children[0].children.length,1);
''')

    def test_unknown_video_information_does_not_treat_photos_as_unknown_videos(self):
        self.run_ui(r'''
location.hash='#home';events.hashchange();$('filter-duration').value='unknown';$('filter-duration').onchange();const card=$('center-content').children[0].children[0];assert.equal($('center-content').children[0].children.length,1);assert(card.children.some(n=>n.textContent==='时长未知 · 尺寸未知'));card.children[0].onclick();assert($('detail-content').children[0].children[1].children.some(n=>n.textContent.includes('重新扫描')));$('media-detail').close();
$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('1'));$('filter-resolution').value='fhd';$('filter-resolution').onchange();assert($('selected-count').textContent.includes('0'));assert.equal($('center-content').children[0].children.length,0);assert.equal(operations.length,0);
''')

    def test_video_duration_boundaries_and_duplicate_filters_keep_all_group_members(self):
        self.run_ui(r'''
assert.equal(scope.centerDuration(59.9),'1:00');assert.equal(scope.centerDuration(.2),'0:01');assert.equal(scope.centerDuration(NaN),'时长未知');
for(const [duration,expected]of [[599.9,'short'],[600,'medium'],[3599,'medium'],[3600,'long']])for(const filter of ['short','medium','long'])assert.equal(scope.centerVideoMatch({video:{duration_seconds:duration}},filter,''),filter===expected);
movie.files[0].video={duration_seconds:75,width:1080,height:1920};review.exact=[{files:[{...movie.files[0],owner:movie.id,kind:'video',media_kind:'movie',metadata:movie.metadata},{...movie.files[0],id:'f'.repeat(64),name:'Another.mp4',video:{}}]}];await $('center-refresh').onclick();location.hash='#review';events.hashchange();$('filter-duration').value='short';$('filter-resolution').value='fhd';$('filter-duration').onchange();const section=$('center-content').children.find(n=>n.className==='review-group');assert.equal(section.children.find(n=>n.className==='review-files').children.length,2);assert.equal(operations.length,0);
''')

    def test_video_filters_keep_folder_collection_and_search_context(self):
        self.run_ui(r'''
movie.files[0].video={duration_seconds:75,width:1920,height:1080};directories={'C:\\':{name:'Movies',parent:'',children:[],ids:[movie.id]},'/':{name:'Photos',parent:'',children:[],ids:[photo.id]}};await $('center-refresh').onclick();location.hash='#resources';events.hashchange();$('filter-duration').value='short';$('filter-duration').onchange();const wall=$('center-content').children[0];assert.equal(wall.children.length,1);wall.children[0].onclick();assert.equal($('center-title').textContent,'C:');$('query').value='Film';$('center-search').onsubmit({preventDefault(){}});assert.equal($('center-content').children[0].children.length,1);await $('center-refresh').onclick();assert.equal($('filter-duration').value,'short');
state.collections['1'.repeat(32)]={name:'Collection',ids:[movie.id,photo.id]};location.hash='#collections';events.hashchange();$('filter-duration').value='long';$('filter-duration').onchange();assert.equal($('center-content').children[0].children.length,0);$('reset-filters').onclick();assert($('center-content').children[0].children.length>0);assert.equal(operations.length,0);
''')

    def run_ui(self, assertions):
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r'''
const assert=require('assert/strict'),fs=require('fs'),vm=require('vm');
class E{constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.textContent='';this.value='';this.open=false;this.hidden=false;this.attrs={};this.events={};this.style={};this.classList={add(){}};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}addEventListener(k,v){this.events[k]=v;}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);},calls=[],operations=[],events={},location={hash:'#movies'},state={version:1,favorites:{},labels:{},recent:[],collections:{}};
const movie={id:'c'.repeat(64),kind:'movie',title:'Film',files:[{id:'a'.repeat(64),name:'Film.mp4',path:'C:\\movie.mp4',bytes:12,mtime:1}],preview:'covers/a.png',folder:'C:\\',mtime:1,metadata:{actors:['Actor']},tags:[],local_tags:[],personal:{rating:0,note:''},tag_key:'d'.repeat(64),favorite:false,bytes:12};
const photo={...movie,id:'e'.repeat(64),kind:'photo',title:'Photo',files:[{id:'b'.repeat(64),name:'Photo.jpg',path:'/photo.jpg',bytes:12,mtime:2}],metadata:{camera:'Sample Camera',width:20,height:10},tag_key:'',folder:'/',mtime:2};
let items=[movie,photo],folders={'C:\\':[movie.id],'/':[photo.id]},directories={};
let access={available:true,ids_by_path:{'C:\\movie.mp4':'a'.repeat(64),'/photo.jpg':'b'.repeat(64)}},failCenter=false;
let operationState={running:false,warnings:[]},review={exact:[],similar:[],warnings:[]},hiddenFiles=0,pendingRefresh=null;
const scope={console,location,IntersectionObserver:class{observe(){}unobserve(){}disconnect(){}},clearTimeout(){},setTimeout(fn){pendingRefresh=fn;return 1;},document:{body:new E(),createElement:tag=>new E(tag),getElementById:$},window:{scrollX:0,scrollY:350,scrollTo(x,y){this.scrollX=x;this.scrollY=y;},addEventListener:(k,v)=>events[k]=v},reportActionOpen:(action,ids,onClose,summary)=>operations.push({action,ids,onClose,summary}),fetch:async(path,options)=>{
if(options?.body){const body=JSON.parse(options.body);calls.push({path,body});if(body.action==='favorite')state.favorites[body.id]=body.value;if(body.action==='recent')state.recent=[body.id];return {ok:true,json:async()=>state};}
if(failCenter&&path==='api/center')return {ok:false,json:async()=>({error:'Disconnected'})};
return {ok:true,json:async()=>path==='api/media'?access:{items:items.map(i=>({...i,favorite:state.favorites[i.id]||false})),state,folders,directories,review,operation_state:operationState,hidden_files:hiddenFiles,recent:state.recent,restored:false}};
}};vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
(async()=>{await new Promise(resolve=>setImmediate(resolve));if($('center-error').textContent)throw Error($('center-error').textContent);
'''+assertions+r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).with_name('media_center.js'))],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
