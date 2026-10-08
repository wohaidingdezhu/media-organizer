"""Generated-data tests for batch proposals and the actual shared browser code."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

import file_operations
import library_server
from media_actions import MediaActions, file_signature, media_id
from organization_plan import OrganizationPlan, STATE_FILE
from test_library_server import MemoryServer, request


class ManagementUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source, self.report, self.destination = [self.base/name for name in ('samples', 'scan-example', 'copies')]
        for path in (self.source, self.report, self.destination):
            path.mkdir()
        files = []
        for number, (name, kind, folder) in enumerate((('photo2.png', '照片', '照片/旧'), ('photo10.png', '照片', '照片/旧'), ('video1.mp4', '视频', '视频/旧'))):
            path = self.source/name
            path.write_bytes(bytes([number+1])*(13+number))
            info = path.stat()
            files.append({'path': str(path), 'kind': kind, 'bytes': info.st_size, 'mtime': info.st_mtime,
                          'source_signature': file_signature(info), 'suggested_path': folder+'/'+name})
        self.document = {'files': files, 'roots': [str(self.source)], 'duplicates': [], 'video_library': {'groups': []}}
        self.ids = [media_id(item['path']) for item in files]
        self.originals = {item['path']: (Path(item['path']).read_bytes(), Path(item['path']).stat().st_mtime_ns) for item in files}
        self.plan = OrganizationPlan(self.report, self.document)
        (self.report/'report.json').write_text(json.dumps(self.document), encoding='utf-8')
        (self.report/'report.html').write_text('<html>generated report</html>', encoding='utf-8')

    def assert_originals_unchanged(self):
        self.assertEqual(self.originals, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.originals})

    def server(self):
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(self.report, self.base)
        return server, urlsplit(url).path.removesuffix('library.html')

    def test_batch_preview_and_atomic_save_preserve_names_originals_and_unselected_state(self):
        self.plan.set_states(self.ids[:2], 'include')
        self.plan.set_states(self.ids[2:], 'hold')
        before = (self.report/STATE_FILE).read_bytes()
        preview = self.plan.preview_folder(self.ids[:2], '照片/旅行/待整理')
        self.assertEqual(preview['changed_count'], 2)
        self.assertEqual(preview['reset_count'], 2)
        self.assertEqual([item['after'] for item in preview['items']], ['照片/旅行/待整理/photo2.png', '照片/旅行/待整理/photo10.png'])
        self.assertEqual((self.report/STATE_FILE).read_bytes(), before)
        self.assertEqual(list(self.destination.iterdir()), [])
        result = self.plan.apply_folder(self.ids[:2], preview['folder'], preview['revision'])
        self.assertEqual(result['counts'], {'total': 3, 'pending': 2, 'include': 0, 'hold': 1})
        self.assertEqual(result['items'][2]['suggested_path'], '视频/旧/video1.mp4')
        self.assertEqual(OrganizationPlan(self.report, self.document).snapshot(), result)
        self.assert_originals_unchanged()

    def test_mixed_photo_video_batch_can_be_reviewed_and_copied_with_full_hashes(self):
        preview = self.plan.preview_folder([self.ids[0], self.ids[2]], '旅行/照片与影片')
        self.plan.apply_folder([self.ids[0], self.ids[2]], preview['folder'], preview['revision'])
        self.plan.set_states([self.ids[0], self.ids[2]], 'include')
        ops = file_operations.FileOperations(self.report, MediaActions(self.document), lambda: self.plan)
        execution = ops.preview('copy', [self.ids[0], self.ids[2]], str(self.destination))
        job = ops.start(execution['token'])
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            result = ops.snapshot(job['id'])
            if result['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'complete', result)
        for item in result['items']:
            self.assertEqual(Path(item['target']).read_bytes(), self.originals[item['path']][0])
            self.assertEqual(item['sha256'], hashlib.sha256(self.originals[item['path']][0]).hexdigest())
        self.assert_originals_unchanged()

    def test_batch_keeps_custom_filename_and_unchanged_review_decision(self):
        self.plan.set_target(self.ids[0], '照片/归档/custom.png')
        self.plan.set_states(self.ids[:2], 'include')
        preview = self.plan.preview_folder(self.ids[:2], '照片/旧')
        self.assertEqual(preview['items'][0]['after'], '照片/旧/custom.png')
        self.assertEqual(preview['changed_count'], 1)
        self.assertEqual(preview['reset_count'], 1)
        result = self.plan.apply_folder(self.ids[:2], preview['folder'], preview['revision'])
        self.assertEqual([item['state'] for item in result['items'][:2]], ['pending', 'include'])
        self.assert_originals_unchanged()

    def test_case_unicode_and_file_directory_conflicts_reject_entire_batch(self):
        for targets in (('old/a.png', 'other/A.png'), ('old/café.png', 'other/cafe\u0301.png'), ('old/file.png', 'new/file.png/child.png')):
            document = json.loads(json.dumps(self.document))
            for record, target in zip(document['files'], targets):
                record['suggested_path'] = target
            plan = OrganizationPlan(self.report, document)
            (self.report/STATE_FILE).unlink(missing_ok=True)
            plan.set_states(self.ids, 'pending')
            before = (self.report/STATE_FILE).read_bytes()
            with self.assertRaisesRegex(ValueError, '冲突|重名'):
                plan.preview_folder(self.ids[:2] if not targets[1].startswith('new/') else self.ids[:1], 'new')
            self.assertEqual((self.report/STATE_FILE).read_bytes(), before)
        self.assert_originals_unchanged()

    def test_invalid_folder_selection_and_missing_names_do_not_save(self):
        before = self.plan.snapshot()
        for folder in ('', '/absolute', 'C:/absolute', '..', '旅行//照片', '旅行/CON', '旅行/尾点.', '旅行\\照片', 'A\x00B'):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.plan.preview_folder(self.ids, folder)
        for ids in ([], [self.ids[0]]*2, ['unknown'], [None], self.ids*70):
            with self.assertRaises(ValueError):
                self.plan.preview_folder(ids, '安全目录')
        document = {'files': [dict(self.document['files'][0], suggested_path='')]}
        with self.assertRaisesRegex(ValueError, '原建议路径无效'):
            OrganizationPlan(self.report, document).preview_folder(self.ids[:1], '安全目录')
        self.assertEqual(self.plan.snapshot(), before)
        self.assertFalse((self.report/STATE_FILE).exists())
        self.assert_originals_unchanged()

    def test_stale_revision_and_other_window_changes_preserve_saved_plan(self):
        preview = self.plan.preview_folder(self.ids, '全部媒体/旅行')
        other = OrganizationPlan(self.report, self.document)
        other.set_states(self.ids[0:1], 'hold')
        before = (self.report/STATE_FILE).read_bytes()
        for revision in (preview['revision'], None, 'invalid', True):
            with self.assertRaisesRegex(ValueError, '计划已变化'):
                self.plan.apply_folder(self.ids, preview['folder'], revision)
        self.assertEqual((self.report/STATE_FILE).read_bytes(), before)
        self.assert_originals_unchanged()

    def test_concurrent_batch_confirmation_allows_one_winner(self):
        preview = self.plan.preview_folder(self.ids, '全部媒体/旅行')
        barrier = threading.Barrier(2)
        results, errors = [], []
        def save(folder):
            plan = OrganizationPlan(self.report, self.document)
            barrier.wait(timeout=5)
            try:
                results.append(plan.apply_folder(self.ids, folder, preview['revision']))
            except ValueError as error:
                errors.append(str(error))
        threads = [threading.Thread(target=save, args=(folder,)) for folder in ('第一窗口', '第二窗口')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('计划已变化', errors[0])
        self.assertEqual(self.plan.snapshot(), results[0])
        self.assert_originals_unchanged()

    def test_foreign_windows_unc_and_posix_report_paths_show_correct_names_and_folders(self):
        for folder in ('C:\\照片', '\\\\server\\share\\照片', '/照片'):
            separator = '\\' if '\\' in folder else '/'
            path = folder+separator+'photo10.png'
            document = {'files': [{'path': path, 'kind': '照片', 'bytes': 1, 'suggested_path': '照片/原/photo10.png'}]}
            plan = OrganizationPlan(self.report, document)
            snapshot = plan.snapshot()
            self.assertEqual(snapshot['items'][0]['name'], 'photo10.png')
            self.assertEqual(snapshot['items'][0]['source_folder'], folder)
            self.assertEqual(plan.preview_folder([media_id(path)], '照片/新')['items'][0]['after'], '照片/新/photo10.png')

    def test_http_preview_confirm_reload_and_stale_update_keep_originals_unchanged(self):
        server, prefix = self.server()
        headers, body = request(server, prefix+'api/organization', 'POST', {'action': 'folder-preview', 'ids': self.ids, 'folder': '全部媒体/旅行'})
        self.assertIn('200', headers)
        preview = json.loads(body)
        self.assertFalse((self.report/STATE_FILE).exists())
        payload = {'action': 'folder-apply', 'ids': self.ids, 'folder': preview['folder'], 'revision': preview['revision']}
        headers, body = request(server, prefix+'api/organization', 'POST', payload)
        self.assertIn('200', headers)
        result = json.loads(body)
        reopened, other_prefix = self.server()
        self.assertEqual(json.loads(request(reopened, other_prefix+'api/organization')[1]), result)
        before = (self.report/STATE_FILE).read_bytes()
        headers, body = request(reopened, other_prefix+'api/organization', 'POST', payload)
        self.assertIn('400', headers)
        self.assertIn('计划已变化', json.loads(body)['error'])
        self.assertEqual((self.report/STATE_FILE).read_bytes(), before)
        self.assert_originals_unchanged()

    def test_http_batch_is_blocked_while_file_operation_owns_report(self):
        server, prefix = self.server()
        revision = self.plan.snapshot()['revision']
        file_operations._BATCH_LOCK.acquire()
        try:
            for action in ('folder-preview', 'folder-apply'):
                headers, body = request(server, prefix+'api/organization', 'POST', {'action': action, 'ids': self.ids, 'folder': '全部媒体/旅行', 'revision': revision})
                self.assertIn('400', headers)
                self.assertIn('等待完成', json.loads(body)['error'])
        finally:
            file_operations._BATCH_LOCK.release()
        self.assertFalse((self.report/STATE_FILE).exists())

    def node(self, code, payload=None, *files):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Both-platform CI installs Node.js for shared UI behavior tests')
        result = subprocess.run([node, '-e', code, *(str(Path(__file__).parent/name) for name in files)], input=json.dumps(payload),
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def test_browser_sorting_handles_unknown_dates_and_natural_names_without_mutation(self):
        items = [{'id': str(number), 'path': path, 'name': name, 'mtime': stamp, 'bytes': size} for number, (name, path, stamp, size) in enumerate((('photo10.png', 'D:\\旅行\\photo10.png', 20, 40), ('photo2.png', '/旅行/photo2.png', 10, 60), ('unknown.png', '/旅行/unknown.png', None, 5)))]
        code = "const b=require(process.argv[1]),input=JSON.parse(require('fs').readFileSync(0,'utf8'));const before=JSON.stringify(input);const sorted=b.sortManagementItems(input.items,input.order);if(before!==JSON.stringify(input))throw Error('mutated report');process.stdout.write(JSON.stringify(sorted.map(item=>item.name)));"
        for order, expected in (('name', ['photo2.png', 'photo10.png', 'unknown.png']), ('newest', ['photo10.png', 'photo2.png', 'unknown.png']), ('oldest', ['photo2.png', 'photo10.png', 'unknown.png']), ('size', ['photo2.png', 'photo10.png', 'unknown.png']), ('', ['photo10.png', 'photo2.png', 'unknown.png'])):
            self.assertEqual(self.node(code, {'items': items, 'order': order}, 'management_browse.js'), expected)
        self.assertEqual(self.node(code, {'items': [dict(items[0], mtime=True), items[1]], 'order': 'newest'}, 'management_browse.js'), ['photo2.png', 'photo10.png'])

    def test_photo_navigation_covers_entire_filtered_sequence_and_excludes_videos(self):
        visible = [{'id': str(number), 'kind': '照片' if number != 50 else '视频'} for number in range(65)]
        code = "const b=require(process.argv[1]),i=JSON.parse(require('fs').readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(b.photoBrowseSequence(i.visible,i.start).map(item=>item.id)));"
        self.assertEqual(self.node(code, {'visible': visible, 'start': visible[49]}, 'management_browse.js'), [str(number) for number in range(65) if number != 50])
        self.assertEqual(self.node(code, {'visible': [], 'start': {'id': 'outside', 'kind': '照片'}}, 'management_browse.js'), ['outside'])

    def test_rendered_page_contains_actual_shared_scripts_and_valid_javascript(self):
        server, prefix = self.server()
        headers, body = request(server, prefix+'photos.html')
        self.assertIn('200', headers)
        self.assertNotIn(b'@@MANAGEMENT', body)
        self.assertIn(b'function sortManagementItems', body)
        self.assertIn('批量调整分类目录'.encode(), body)
        script = body.decode('utf-8').split('<script>', 1)[1].split('</script>', 1)[0]
        self.node("new(require('vm').Script)(JSON.parse(require('fs').readFileSync(0,'utf8')));", script)

    def test_actual_photo_viewer_boundaries_keyboard_comparison_and_reset(self):
        code = r"""
const fs=require('fs'),vm=require('vm'),b=require(process.argv[2]);
class Element{constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.attributes={};this.events={};this.value='';this.open=false;}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(key,value){this.attributes[key]=value;}getAttribute(key){return this.attributes[key];}addEventListener(name,handler){this.events[name]=handler;}showModal(){this.open=true;}close(){this.open=false;if(this.events.close)this.events.close();}focus(){}querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tagName===tag.toUpperCase()?[child]:[]),...child.querySelectorAll(tag)]);}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const visible=Array.from({length:65},(_,n)=>({id:String(n),kind:n===50?'视频':'照片',name:'photo'+n+'.png',path:'C:\\photos\\photo'+n+'.png',bytes:1}));
const scope={$,make:(tag,cls,text)=>new Element(tag,text||''),photos:new Map(),mediaIds:Object.fromEntries(visible.map(item=>[item.path,item.id])),visible,data:{items:visible},selected:new Set(['0']),saving:false,operating:false,handledTrash:new Set([visible[64].path]),pictureSequence:[],pictureIndex:0,pictureCompare:false,folderPreview:null,folderIds:[],fileSize:n=>String(n),filters:['search','kind','state','risk','folder','photo-month','source-folder','sort'],render:()=>{},photoBrowseSequence:b.photoBrowseSequence};vm.createContext(scope);
const code=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(code.slice(0,code.indexOf('function optionsFor')),scope);
scope.viewPictures([visible[49]]);if(!$('picture-position').textContent.includes('50/64'))throw Error('lost off-page photos');
$('picture-next').onclick();if(scope.pictureSequence[scope.pictureIndex].id!=='51')throw Error('video entered sequence');
if($('picture-body').querySelectorAll('button')[0].disabled)throw Error('another trashed photo disabled current');
let prevented=false;$('picture-viewer').events.keydown({key:'ArrowLeft',target:{tagName:'BUTTON'},preventDefault(){prevented=true;}});if(!prevented||scope.pictureSequence[scope.pictureIndex].id!=='49')throw Error('keyboard navigation failed');
scope.viewPictures([visible[0]]);if(!$('picture-previous').disabled)throw Error('previous should stop at beginning');scope.turnPicture(-1);if(scope.pictureIndex!==0)throw Error('wrapped past beginning');
scope.viewPictures([visible[64]]);if(!$('picture-next').disabled||!$('picture-body').querySelectorAll('button')[0].disabled)throw Error('last/trashed action guard failed');
scope.viewPictures([visible[0],visible[1]]);if(!$('picture-next').hidden||!$('picture-previous').hidden)throw Error('comparison has navigation');scope.turnPicture(1);if(scope.pictureIndex!==0)throw Error('comparison changed');
if([...scope.selected].join()!=='0')throw Error('browsing changed selection');
for(const id of scope.filters)$(id).value='filter';$('view-mode').value='wall';scope.page=3;$('reset-filters').onclick();if(scope.page!==0||scope.selected.size||$('kind').value!=='照片'||$('search').value)throw Error('reset did not preserve photo wall');
"""
        self.node(code, None, 'management.js', 'management_browse.js')

    def test_batch_dialog_requires_current_preview_and_shows_changed_items_after_save(self):
        code = r"""
const fs=require('fs'),vm=require('vm'),b=require(process.argv[2]);
class Element{constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.attributes={};this.events={};this.value='';this.open=false;}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(key,value){this.attributes[key]=value;}getAttribute(key){return this.attributes[key];}addEventListener(name,handler){this.events[name]=handler;}showModal(){this.open=true;}close(){this.open=false;}focus(){}querySelectorAll(){return [];}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const scope={$,make:(tag,cls,text)=>new Element(tag,text||''),photos:new Map(),mediaIds:{},visible:[],data:{items:[{id:'1',path:'source/photo.png',suggested_path:'old/photo.png',selectable:true}]},selected:new Set(['1']),saving:false,operating:false,handledTrash:new Set(),pictureSequence:[],pictureIndex:0,pictureCompare:false,folderPreview:null,folderIds:[],filters:['search','kind','state','risk','folder','photo-month','source-folder','sort'],render:()=>scope.syncBrowseControls(),folderOf:()=> 'old',buildFolders:()=>{},photoBrowseSequence:b.photoBrowseSequence};vm.createContext(scope);
const code=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(code.slice(0,code.indexOf('function optionsFor')),scope);
(async()=>{
let writes=0;scope.request=async payload=>{if(payload.action==='folder-apply'){writes++;return scope.data;}return {folder:payload.folder,revision:'revision',items:[{path:'source/photo.png',before:'old/photo.png',after:'new/photo.png',changed:true}],changed_count:1,reset_count:1};};
$('batch-folder').onclick();$('batch-folder-input').value='new';await $('batch-folder-preview').onclick();if($('batch-folder-save').disabled)throw Error('preview cannot confirm');
$('batch-folder-input').value='changed after preview';$('batch-folder-input').oninput();await $('batch-folder-save').onclick();if(writes||!$('batch-folder-save').disabled)throw Error('edited directory reused old preview');
await $('batch-folder-preview').onclick();await $('batch-folder-save').onclick();if(writes!==1||$('batch-folder-dialog').open||$('risk').value!=='selected'||$('state').value||scope.selected.size!==1)throw Error('saved items hidden or unselected');
$('batch-folder').onclick();await $('batch-folder-preview').onclick();scope.request=async()=>{throw Error('整理计划已变化');};await $('batch-folder-save').onclick();if(!$('batch-folder-save').disabled||!$('batch-folder-error').textContent.includes('计划已变化'))throw Error('stale preview remains confirmable');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        self.node(code, None, 'management.js', 'management_browse.js')

    def test_report_time_validation_keeps_zero_and_rejects_malformed_legacy_values(self):
        for value, expected in ((0, 0), (1.5, 1.5), (True, None), ('yesterday', None), (float('inf'), None), (10**500, None)):
            document = {'files': [dict(self.document['files'][0], mtime=value)]}
            self.assertEqual(OrganizationPlan(self.report, document).snapshot()['items'][0]['mtime'], expected)

    def test_review_state_save_keeps_batch_selection_visible_for_copy(self):
        code = r"""
const fs=require('fs'),vm=require('vm'),html=fs.readFileSync(process.argv[1],'utf8');
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:''});return nodes.get(id);};
const scope={$,selected:new Set(['photo','video']),saving:false,operating:false,names:{include:'已纳入'},render:()=>{},error:()=>{},request:async body=>({items:body.ids.map(id=>({id,state:body.state}))})};vm.createContext(scope);
vm.runInContext(html.slice(html.indexOf('async function save('),html.indexOf('function openEditor(')),scope);
(async()=>{
$('risk').value='selected';await scope.save('include');if(scope.selected.size!==2||scope.data.items.some(item=>item.state!=='include'))throw Error('batch selection disappeared before copy');
$('risk').value='';await scope.save('include');if(scope.selected.size)throw Error('ordinary filter keeps hidden selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        self.node(code, None, 'organization.html')
