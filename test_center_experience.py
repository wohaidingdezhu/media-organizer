"""User workflows using generated files and the shipped shared interface."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server
from media_actions import MediaActions, media_id, file_signature
from media_center import CenterState, center_catalog, validate_state
from movie_grouping import apply_grouping
from test_library_server import MemoryServer, request
import test_media_center as center_tests


class CenterExperienceTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_folder_tag_covers_mixed_media_and_future_scans_without_other_same_named_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = CenterState(Path(temporary))
            parent = r'C:\People\小明'
            state.update({'action': 'folder_label', 'path': parent, 'label': '小明'}, set(), [parent])
            paths = [parent+r'\one.jpg', parent+r'\子目录\two.mp4', r'D:\People\小明\three.jpg', parent+'2\\four.jpg']
            document = {'roots': [r'C:\People', r'D:\People'], 'files': [dict(path=p, kind='视频' if p.endswith('.mp4') else '照片', bytes=1, mtime=1) for p in paths]}
            items = center_catalog(document, state.snapshot())['items']
            self.assertEqual([item['tags'] for item in items], [['小明'], ['小明'], [], []])
            document['files'].append(dict(path=parent+r'\new.jpg', kind='照片', bytes=2, mtime=2))
            self.assertEqual(center_catalog(document, CenterState(Path(temporary)).snapshot())['items'][-1]['tags'], ['小明'])
            with self.assertRaises(ValueError):state.update({'action': 'folder_label', 'path': '/outside', 'label': 'bad'}, set(), [parent])
            state.update({'action': 'folder_label', 'path': parent, 'label': ''}, set(), [parent])
            self.assertTrue(all(not item['tags'] for item in center_catalog(document, state.snapshot())['items']))
            invalid = state.snapshot();invalid['folder_labels'] = {'relative': 'tag'}
            with self.assertRaises(ValueError):validate_state(invalid)

    def test_timeline_uses_saved_month_and_labels_inferred_dates(self):
        state = {'favorites': {}, 'collections': {}, 'recent': []}
        doc = {'files': [{'path': '/sample/a.jpg', 'kind': '照片', 'bytes': 1, 'mtime': 1,
                          'suggested_path': '照片/2020/02/a.jpg', 'date_source': '文件名日期（推测）'}]}
        item = center_catalog(doc, state)['items'][0]
        self.assertEqual(item['metadata']['timeline_month'], '2020-02')
        self.assertEqual(item['metadata']['timeline_source'], '文件名日期（推测）')
        self.run_ui(r'''
photo.metadata.timeline_month='2020-02';photo.metadata.timeline_source='文件名日期（推测）';items=[photo,{...photo,id:'f'.repeat(64),title:'Older',metadata:{timeline_month:'2019-01',timeline_source:'EXIF 拍摄日期'}}];location.hash='#photos';events.hashchange();await $('center-refresh').onclick();$('photo-layout').value='timeline';$('photo-layout').onchange();const headings=$('center-content').children.filter(n=>n.tagName==='H2');assert.equal(headings[0].textContent,'2020-02');assert.equal(headings[1].textContent,'2019-01');assert($('center-content').children.some(n=>n.textContent.includes('推测')));$('center-content').children.find(n=>n.className==='wall photo-wall').children[0].children[0].onclick();assert($('media-detail').open);assert.equal(operations.length,0);$('detail-close').onclick();$('query').value='a';$('search-scope').value='all';$('search-scope').onchange();assert($('photo-layout').hidden);assert(!$('center-content').children.some(n=>n.tagName==='H2'));
''')

    def test_inline_video_is_explicit_and_keeps_external_player_with_spaced_controls(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],file=info.children.find(n=>n.className==='file'),actions=file.children.find(n=>n.className==='actions');assert(!file.children.some(n=>n.tagName==='VIDEO'));actions.children.find(n=>n.textContent==='页内预览').onclick();let players=file.children.filter(n=>n.tagName==='VIDEO');assert.equal(players.length,1);assert.equal(players[0].src,'api/video/'+movie.files[0].id);assert(players[0].controls);assert(actions.children.some(n=>n.textContent==='关闭预览'));assert.equal(file.children.filter(n=>n.tagName==='VIDEO').length,1);players[0].onerror();assert($('detail-status').textContent.includes('浏览器'));assert(info.children.some(n=>n.className==='actions detail-controls'));assert(actions.children.some(n=>n.textContent==='播放'));assert.equal(calls.length,0);assert.equal(operations.length,0);
''')

    def test_review_bulk_selection_spans_pages_and_refuses_all_exact_copies(self):
        self.run_ui(r'''
items=Array.from({length:22},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+30).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;const file=item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata});review.exact=Array.from({length:11},(_,n)=>({files:items.slice(n*2,n*2+2).map(file)}));location.hash='#review';events.hashchange();await $('center-refresh').onclick();$('center-content').children.find(n=>n.className==='actions review-batch').children.find(n=>n.textContent==='清空选择').onclick();
const cards=()=>$('center-content').children.find(n=>n.className==='review-group').children.find(n=>n.className==='review-files').children,choose=n=>{const pick=cards()[n].children.find(n=>n.className==='review-pick').children[0];pick.checked=true;pick.onchange();},batch=()=>$('center-content').children.find(n=>n.className==='actions review-batch');choose(0);$('center-next').onclick();choose(0);assert(batch().children[0].textContent.includes('2 个文件'));batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations[0].ids.length,2);assert(!operations[0].ids.includes(items[1].files[0].id));choose(1);batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations.length,1);assert($('center-error').textContent.includes('至少保留'));assert.equal(calls.length,0);$('filter-year').onchange();assert(batch().children[0].textContent.includes('0 个文件'));assert(batch().children.find(n=>n.textContent==='删除所选…').disabled);
''')

    def test_review_default_left_bulk_invert_and_visible_refusal_with_104_groups(self):
        self.run_ui(r'''
items=Array.from({length:208},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;const file=item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata});review.exact=Array.from({length:104},(_,n)=>({files:items.slice(n*2,n*2+2).map(file)}));location.hash='#review';events.hashchange();await $('center-refresh').onclick();
const batch=()=>$('center-content').children.find(n=>n.className==='actions review-batch'),tools=()=>$('center-content').children.find(n=>n.className==='actions review-selection-tools');assert(batch().children[0].textContent.includes('104'));batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations[0].ids.length,104);assert(operations[0].ids.includes(items[0].files[0].id));assert(!operations[0].ids.includes(items[1].files[0].id));
tools().children.find(n=>n.textContent==='反选当前结果').onclick();batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations[1].ids.length,104);assert(!operations[1].ids.includes(items[0].files[0].id));assert(operations[1].ids.includes(items[1].files[0].id));
tools().children.find(n=>n.textContent.startsWith('全选左侧')).onclick();const section=$('center-content').children.find(n=>n.className==='review-group'),check=section.children.find(n=>n.className==='review-files').children[1].children[0].children[0];check.checked=true;check.onchange();batch().children.find(n=>n.textContent==='删除所选…').onclick();assert.equal(operations.length,2);assert(batch().children.some(n=>n.textContent.includes('至少保留')));assert(batch().children[0].textContent.includes('105'));
batch().children.find(n=>n.textContent==='清空选择').onclick();assert(batch().hidden);$('center-next').onclick();$('center-prev').onclick();assert(batch().hidden);await $('center-refresh').onclick();assert(batch().hidden);$('filter-year').onchange();assert(batch().hidden);tools().children.find(n=>n.textContent.startsWith('全选左侧')).onclick();assert(batch().children[0].textContent.includes('104'));assert.equal(calls.length,0);
''')

    def test_similar_never_auto_selects_and_bulk_selection_is_limited(self):
        self.run_ui(r'''
items=Array.from({length:204},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));for(const item of items)access.ids_by_path[item.files[0].path]=item.files[0].id;const file=item=>({...item.files[0],kind:'photo',media_kind:'photo',owner:item.id,metadata:item.metadata});review.similar=Array.from({length:102},(_,n)=>({id:'group'+n,files:items.slice(n*2,n*2+2).map(file)}));location.hash='#review';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[1].onclick();const batch=()=>$('center-content').children.find(n=>n.className==='actions review-batch'),tools=()=>$('center-content').children.find(n=>n.className==='actions review-selection-tools');assert(batch().hidden);tools().children.find(n=>n.textContent==='全选当前结果').onclick();assert(batch().children[0].textContent.includes('200'));assert(batch().children.some(n=>n.textContent.includes('分批')));assert.equal(operations.length,0);
''')

    def test_background_missing_file_update_defers_for_selection_and_open_dialog(self):
        self.run_ui(r'''
let revision=0,centerReads=0;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/availability')return {ok:true,json:async()=>({revision,missing_count:0,scanning:false})};const response=await original(path,options);if(path!=='api/center'||options?.body)return response;centerReads++;const data=await response.json();return {ok:true,json:async()=>({...data,availability_revision:revision})};};await $('center-refresh').onclick();location.hash='#photos';events.hashchange();
revision=1;items=[movie];await availabilityPoll();assert.equal(centerReads,2);assert($('center-subtitle').textContent.includes('0 项媒体'));
items=[movie,photo];revision=2;await availabilityPoll();assert.equal(centerReads,3);assert($('center-subtitle').textContent.includes('1 项媒体'));
$('manage-toggle').onclick();$('select-page').onclick();revision=3;items=[movie];await availabilityPoll();assert.equal(centerReads,3);assert($('selected-count').textContent.includes('1'));assert($('center-status').textContent.includes('当前选择或编辑会保留'));
$('clear-selection').onclick();$('manage-toggle').onclick();$('media-detail').open=true;await availabilityPoll();assert.equal(centerReads,3);$('media-detail').open=false;await availabilityPoll();assert.equal(centerReads,4);assert($('center-subtitle').textContent.includes('0 项媒体'));assert(!$('center-status').textContent.includes('当前选择或编辑会保留'));
''',setup="let availabilityPoll=null;scope.setInterval=(fn,ms)=>{assert.equal(ms,10000);availabilityPoll=fn;};")

    def test_background_refresh_rechecks_selection_after_network_response(self):
        self.run_ui(r'''
let revision=0,hold=false,release;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/availability')return {ok:true,json:async()=>({revision})};const response=await original(path,options);if(path!=='api/center'||options?.body)return response;const data=await response.json();if(hold)await new Promise(resolve=>{release=resolve;});return {ok:true,json:async()=>({...data,availability_revision:revision})};};await $('center-refresh').onclick();location.hash='#photos';events.hashchange();revision=1;hold=true;const pending=availabilityPoll();await new Promise(setImmediate);$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('1'));release();await pending;assert($('selected-count').textContent.includes('1'));assert($('center-status').textContent.includes('当前选择或编辑会保留'));
''',setup="let availabilityPoll=null;scope.setInterval=fn=>{availabilityPoll=fn;};")

    def test_stream_range_identity_and_snapshot_restrictions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();report = root/'reports'/'scan-generated';report.mkdir(parents=True)
            video = root/'generated.mp4';video.write_bytes(b'0123456789')
            record = dict(path=str(video), kind='视频', bytes=10, mtime=video.stat().st_mtime, source_signature=file_signature(video.stat()))
            doc = {'roots': [str(root)], 'files': [record]}
            def server_for(value):
                (report/'report.json').write_text(json.dumps(value), encoding='utf-8')
                with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
                    server, url = library_server.create_library_server(report, report.parent)
                return server, urlsplit(url).path.removesuffix('library.html')+'api/video/'+media_id(str(video))
            server, route = server_for(doc)
            _, page = request(server, route.split('api/video/')[0] + 'center.html')
            self.assertIn(b"media-src 'self';", page)
            self.assertNotIn(b"media-src *", page)
            self.assertIn(b"api/video/", page)
            headers, body = request(server, route)
            self.assertIn('200 OK', headers);self.assertEqual(body, b'0123456789')
            for value, expected in [('bytes=2-5', b'2345'), ('bytes=8-', b'89'), ('bytes=-3', b'789')]:
                headers, body = request(server, route, extra_headers={'Range': value})
                self.assertIn('206 Partial Content', headers);self.assertEqual(body, expected)
            for value in ('bytes=99-', 'bytes=5-2', 'bytes=-0', 'bytes=1-2,4-5', 'bytes='+('9'*500)+'-'):
                headers, _ = request(server, route, extra_headers={'Range': value});self.assertIn('416', headers)
            self.assertIn('404', request(server, route, host='evil.example')[0])
            self.assertIn('400', request(server, route+'invalid')[0])
            self.assertEqual(video.read_bytes(), b'0123456789')
            doc['restored_snapshot'] = True
            restored, restored_route = server_for(doc)
            self.assertIn('400', request(restored, restored_route)[0])
            video.write_bytes(b'changed generated video')
            self.assertIn('400', request(server, route)[0])

    def test_attribute_only_change_is_explained_without_weakening_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve()/'sample.mp4';path.write_bytes(b'generated')
            signature = file_signature(path.stat());signature[-1] -= 1
            media = MediaActions({'roots': [str(path.parent)], 'files': [dict(path=str(path), kind='视频', bytes=9, mtime=path.stat().st_mtime, source_signature=signature)]})
            with self.assertRaisesRegex(ValueError, '文件属性'):
                media.validate(media_id(str(path)))

    def test_missing_video_preview_preserves_scan_reason(self):
        path = '/generated/clip.mp4'
        record = dict(path=path, kind='视频', bytes=10, mtime=1)
        file = dict(record, issues=[], duplicate_group=None, sidecars=[])
        library = {'groups': [{'tag_key': 'a'*64, 'title': 'clip', 'files': [file],
                               'poster_status': '达到封面生成数量上限'}]}
        grouped = apply_grouping(library, {})
        doc = {'files': [record], 'video_library': grouped}
        state = {'favorites': {}, 'collections': {}, 'recent': []}
        self.assertEqual(center_catalog(doc, state)['items'][0]['preview_status'], '达到封面生成数量上限')
        doc['options'] = {'video_covers_enabled': False}
        self.assertIn('未启用', center_catalog(doc, state)['items'][0]['preview_status'])

    def test_saving_favorite_and_tags_preserves_preview_and_unsaved_input(self):
        self.run_ui(r"""
$('center-content').children[0].children[0].children[0].onclick();
const info=$('detail-content').children[0].children[1],tags=info.children.find(n=>n.className==='actions detail-controls').children[0],file=info.children.find(n=>n.className==='file');
file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='页内预览').onclick();
const player=file.children.find(n=>n.tagName==='VIDEO');let stopped=0;player.pause=()=>stopped++;player.removeAttribute=()=>stopped++;player.load=()=>stopped++;player.currentTime=5;
$('detail-content').querySelectorAll=()=>[player];tags.value='Unfinished tag';
await $('detail-actions').children[0].onclick();assert.equal(stopped,0);assert.equal($('detail-content').children[0].children[1],info);assert.equal(tags.value,'Unfinished tag');assert.equal(player.currentTime,5);assert($('detail-feedback').textContent.includes('加入收藏'));
await $('detail-actions').children[0].onclick();assert.equal(stopped,0);assert($('detail-feedback').textContent.includes('取消收藏'));assert.equal(calls.filter(c=>c.body.action==='favorite').at(-1).body.value,false);
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body){const body=JSON.parse(options.body);if(body.action==='labels'){movie.local_tags=body.tags;movie.tags=body.tags;}}return originalFetch(path,options);};
await info.children.find(n=>n.className==='actions detail-controls').children[1].onclick();assert.equal(stopped,0);assert.equal(tags.value,'Unfinished tag');assert($('detail-feedback').textContent.includes('标签已保存'));assert(info.children.find(n=>n.className==='chips').children.some(n=>n.textContent==='Unfinished tag'));
$('media-detail').events.close();assert(stopped>0);
""")

    def test_favorite_keeps_movie_rating_and_note_drafts(self):
        self.run_ui(r"""
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],rating=info.children.find(n=>n.tagName==='SELECT'),note=info.children.find(n=>n.tagName==='TEXTAREA');rating.value='4';note.value='Unfinished note';await $('detail-actions').children[0].onclick();assert.equal($('detail-content').children[0].children[1],info);assert.equal(rating.value,'4');assert.equal(note.value,'Unfinished note');assert.equal(calls.filter(c=>c.path==='api/notes').length,0);
""")

    def test_first_add_opens_chooser_and_cancel_never_starts_scan(self):
        import subprocess,shutil
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),html=fs.readFileSync(process.argv[1]+'/dashboard.html','utf8');let panes=[],choices=0,scans=0;const nodes={'add-folder':{onclick:async()=>{choices++;return null;}},scan:{onclick:()=>scans++}};const scope={$:id=>nodes[id],showPane:name=>panes.push(name)};vm.createContext(scope);vm.runInContext(html.slice(html.indexOf('async function beginFirstScan('),html.indexOf("$('nav-scan').onclick")),scope);(async()=>{await scope.beginFirstScan();assert.deepEqual(panes,['scan']);assert.equal(choices,1);assert.equal(scans,0);})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result=subprocess.run([node,'-e',code,str(Path(__file__).parent)],text=True,encoding='utf-8',capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_folder_selection_explains_next_action_and_cancel_keeps_state(self):
        import media_gui
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();source=root/'generated';source.mkdir()
            state=media_gui.DashboardState(root/'reports');self.addCleanup(state.close)
            with mock.patch.object(media_gui.scan,'choose_folders',return_value=[str(source)]),mock.patch.object(state,'start_scan') as start:
                selected=state.add_folders();self.assertIn('已选择 1 个文件夹',selected['status']);self.assertIn('开始只读扫描',selected['status']);start.assert_not_called()
            with mock.patch.object(media_gui.scan,'choose_folders',return_value=[]):
                self.assertEqual(state.add_folders()['status'],selected['status']);self.assertEqual(state.folders,[str(source)])
            self.assertIn('请先添加',state.remove_folder(0)['status'])
