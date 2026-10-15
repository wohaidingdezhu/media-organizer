# coding: utf-8
"""Generated media: source preferences, personal videos, confirmed queues/results."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock
from urllib.parse import urlsplit

import file_operations
import test_simple_organize as fixtures
import library_server
from file_operations import FileOperations
from media_actions import MediaActions, media_id
from organize_settings import OrganizeSettings
from organized_results import catalog, perform
from test_library_server import MemoryServer, request
from test_support import make_symlink


class WorkflowTests(unittest.TestCase):
    setUp = fixtures.SimpleOrganizeTests.setUp
    file = fixtures.SimpleOrganizeTests.file
    scan = fixtures.SimpleOrganizeTests.scan
    wait = fixtures.SimpleOrganizeTests.wait

    def setup_workflow(self):
        report, document, plan, _ = self.scan()
        settings = OrganizeSettings(self.output, lambda: document)
        operations = FileOperations(report, MediaActions(document), lambda: plan, document=lambda: document, options=settings.snapshot)
        return report, document, plan, settings, operations

    def save(self, settings, **payload):
        return settings.update({'revision': settings.snapshot()['revision'], **payload})

    def batch_sample(self, count=202):
        for number in range(count):
            self.file('20210506_%04d.jpg' % number)
        return self.setup_workflow()

    def test_personal_camera_videos_by_month_but_explicit_movies_stay_movies(self):
        self.file('IMG_20210506_123456.MOV'); self.file('VID_20220304.mp4')
        self.file('ABC-123.mp4'); self.file('UnknownMovie.mp4')
        _, _, _, _, ops = self.setup_workflow()
        preview = ops.quick_preview(str(self.destination), True)
        targets = {Path(item['path']).name: item['relative'] for item in preview['items']}
        self.assertEqual(targets['IMG_20210506_123456.MOV'], '家庭视频/2021/05/IMG_20210506_123456.MOV')
        self.assertEqual(targets['VID_20220304.mp4'], '家庭视频/2022/03/VID_20220304.mp4')
        self.assertTrue(targets['ABC-123.mp4'].startswith('电影/'))
        self.assertTrue(targets['UnknownMovie.mp4'].startswith('电影/待确认地区/'))
        self.assertEqual(self.wait(ops, ops.start(preview['token']))['status'], 'complete')

    def test_saved_preferences_reload_are_source_scoped_and_use_revision_conflicts(self):
        photo = self.file('a.jpg')
        _, document, _, settings, ops = self.setup_workflow()
        initial = settings.snapshot()
        self.save(settings, action='preferences', destination=str(self.destination), all_batches=True)
        reopened = OrganizeSettings(self.output, document)
        self.assertEqual(reopened.snapshot()['destination'], str(self.destination))
        with self.assertRaisesRegex(ValueError, '已变化'):
            settings.update({'revision': initial['revision'], 'action': 'preferences', 'destination':'', 'all_batches':True})
        other = {**document, 'files': [{**item, 'root': str(self.base/'another-source')} for item in document['files']]}
        self.assertEqual(OrganizeSettings(self.output, other).snapshot()['destination'], '')
        for changes in ({'destination':'relative'}, {'all_batches':'true'}):
            with self.assertRaises(ValueError):
                self.save(settings, action='preferences', **{'destination':str(self.destination), 'all_batches':True, **changes})
        self.assertEqual(photo.read_bytes(), b'generated a.jpg')

    def test_corrections_persist_region_home_video_and_photo_pair_month_without_plan_mutation(self):
        jpg=self.file('IMG.jpg'); raw=self.file('IMG.dng'); movie=self.file('ABC-123.mp4'); phone=self.file('VID_20220304.mp4')
        _, _, plan, settings, ops = self.setup_workflow(); before=plan.snapshot()
        self.save(settings, action='correct', ids=[media_id(str(jpg))], field='month', value='2019/04')
        self.save(settings, action='correct', ids=[media_id(str(movie))], field='region', value='国产')
        self.save(settings, action='correct', ids=[media_id(str(phone))], field='month', value='2020/02')
        preview=ops.quick_preview(str(self.destination), True)
        values={item['path']:item['relative'] for item in preview['items']}
        self.assertTrue(values[str(jpg)].startswith('照片/2019/04/'))
        self.assertTrue(values[str(raw)].startswith('照片/2019/04/'))
        self.assertTrue(values[str(movie)].startswith('电影/国产/'))
        self.assertTrue(values[str(phone)].startswith('家庭视频/2020/02/'))
        self.assertEqual(plan.snapshot(), before)
        self.save(settings, action='correct', ids=[media_id(str(raw))], field='reset', value='')
        self.assertNotIn(str(jpg), settings.snapshot()['corrections'])
        with self.assertRaises(ValueError):
            self.save(settings, action='correct', ids=[media_id(str(jpg))], field='month', value='2026/13')

    def test_explicit_phone_movie_override_does_not_duplicate_same_named_photo(self):
        video=self.file('IMG_20210506.mp4'); self.file('IMG_20210506.jpg')
        _, _, _, settings, ops=self.setup_workflow()
        self.save(settings, action='correct', ids=[media_id(str(video))], field='region', value='日韩')
        preview=ops.quick_preview(str(self.destination), True)
        self.assertEqual(len(preview['items']), 2)
        self.assertEqual({item['role'] for item in preview['items']}, {'照片','影片'})
        self.assertTrue(next(item for item in preview['items'] if item['role']=='影片')['relative'].startswith('电影/日韩/'))

    def test_one_confirmation_runs_two_durable_batches_and_reopened_results_have_identities(self):
        report, document, plan, _, ops = self.batch_sample()
        preview=ops.quick_preview(str(self.destination), True)
        self.assertTrue(preview['queue']); self.assertEqual(preview['batch_count'],2)
        self.assertEqual(len(preview['items']),202); self.assertFalse(list(self.destination.iterdir()))
        self.assertNotIn('_batches', preview)
        job=self.wait(ops,ops.start(preview['token']))
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(job['completed_batches'],2); self.assertEqual(len(job['items']),202)
        reopened=FileOperations(report,MediaActions(document),lambda:plan,document=document)
        self.assertEqual(reopened.snapshot(job['id'])['status'],'complete')
        self.assertEqual(len(reopened.history()['jobs']),1)
        self.assertEqual(len(reopened.history()['handled']['copy']),202)
        copies=catalog(reopened);self.assertEqual(len(copies),202)
        self.assertTrue(all(item['can_open'] for item in copies))
        with mock.patch('system_integration.open_path') as opening:
            perform(reopened,copies[0]['id'],'open');opening.assert_called_once_with(copies[0]['target'],reveal=False)
        self.assertFalse(reopened.quick_preview(str(self.destination),True)['items'])

    def test_queue_safe_stop_before_first_file_records_all_unstarted_files(self):
        _, _, _, _, ops=self.batch_sample()
        preview=ops.quick_preview(str(self.destination),True); queued=[]
        with mock.patch('file_operations.threading.Thread.start',autospec=True,side_effect=queued.append):
            job=ops.start(preview['token'])
        try:
            ops.request_stop(job['id'])
        finally:
            queued[0].run()
        final=ops.snapshot(job['id'])
        self.assertEqual(final['status'],'cancelled')
        self.assertEqual(len(final['planned_items']),202)
        self.assertFalse(final['items']);self.assertFalse(list(self.destination.iterdir()))
        self.assertEqual(len(ops.quick_preview(str(self.destination),True)['items']),202)

    def test_source_changed_in_second_batch_stops_queue_and_does_not_retry(self):
        _, _, _, _, ops=self.batch_sample();preview=ops.quick_preview(str(self.destination),True)
        original=file_operations.copy_one;calls=[]
        def changed_after_copy(*args,**kwargs):
            answer=original(*args,**kwargs);calls.append(args[1])
            if len(calls)==200:
                Path(preview['items'][200]['path']).write_bytes(b'changed source after first batch')
            return answer
        with mock.patch('file_operations.copy_one',side_effect=changed_after_copy):
            final=self.wait(ops,ops.start(preview['token']))
        self.assertEqual(final['status'],'stopped')
        self.assertEqual(sum(item['status']=='success' for item in final['items']),200)
        self.assertEqual(final['items'][-1]['status'],'failed')
        self.assertEqual(len(calls),200)
        self.assertTrue(all(Path(item['path']).is_file() for item in preview['items']))
        self.assertTrue(any('不会自动重试' in item['reason'] for item in ops.quick_preview(str(self.destination),True)['skipped']))

    def test_confirmation_rechecks_corrections_and_queue_journal_missing_child_is_not_complete(self):
        report, document, plan, settings, ops=self.batch_sample()
        preview=ops.quick_preview(str(self.destination),True)
        self.save(settings,action='correct',ids=[preview['items'][0]['id']],field='month',value='2018/03')
        with self.assertRaisesRegex(ValueError,'重新预览'):
            ops.start(preview['token'])
        self.assertFalse(list(self.destination.iterdir()))
        preview=ops.quick_preview(str(self.destination),True);queued=[]
        with mock.patch('file_operations.threading.Thread.start',autospec=True,side_effect=queued.append):
            job=ops.start(preview['token'])
        try:
            with self.assertRaisesRegex(ValueError,'正在执行'):
                ops.update_plan(lambda:None)
            ops.request_stop(job['id'])
        finally:
            queued[0].run()
        (report/'operations'/(job['batch_ids'][0]+'.json')).unlink()
        reopened=FileOperations(report,MediaActions(document),lambda:plan,document=document)
        self.assertTrue(reopened.history()['warnings'])
        with self.assertRaises(ValueError):
            reopened.quick_preview(str(self.destination),True)

    def test_result_open_refuses_replaced_or_linked_copy_and_old_records_have_no_open(self):
        self.file('a.jpg');report,document,plan,_,ops=self.setup_workflow()
        preview=ops.quick_preview(str(self.destination),True);final=self.wait(ops,ops.start(preview['token']))
        item,=catalog(ops);Path(item['target']).write_bytes(b'changed copied file')
        with mock.patch('system_integration.open_path') as opening:
            with self.assertRaises(ValueError):perform(ops,item['id'],'open')
            opening.assert_not_called()
        journal=report/'operations'/(final['id']+'.json');record=json.loads(journal.read_text(encoding='utf-8'))
        record['items'][0].pop('target_signature');journal.write_text(json.dumps(record),encoding='utf-8')
        reopened=FileOperations(report,MediaActions(document),lambda:plan,document=document)
        self.assertFalse(catalog(reopened)[0]['can_open'])
        with self.assertRaises(ValueError):perform(reopened,catalog(reopened)[0]['id'],'reveal')

    def test_http_settings_results_and_queue_preview_require_current_origin(self):
        self.file('VID_20220304.mp4');report,_,_,_,_=self.setup_workflow()
        with mock.patch.object(library_server,'ThreadingHTTPServer',MemoryServer):server,url=library_server.create_library_server(report,self.output)
        prefix=urlsplit(url).path.removesuffix('library.html');origin='http://127.0.0.1:43210'
        headers,body=request(server,prefix+'api/organize-settings');self.assertIn('200',headers);settings=json.loads(body)
        payload={'action':'preferences','revision':settings['revision'],'destination':str(self.destination),'all_batches':True}
        for route,value in [('api/organize-settings',payload),('api/organized-results/action',{'id':'x','action':'open'})]:
            self.assertIn('403',request(server,prefix+route,'POST',value)[0])
            self.assertIn('403',request(server,prefix+route,'POST',value,origin='https://example.com')[0])
        self.assertIn('200',request(server,prefix+'api/organize-settings','POST',payload,origin=origin)[0])
        headers,body=request(server,prefix+'api/operations/quick-preview','POST',{'destination':str(self.destination),'all_batches':True},origin=origin)
        self.assertIn('200',headers);self.assertTrue(json.loads(body)['items'][0]['relative'].startswith('家庭视频/'))
        self.assertIn('200',request(server,prefix+'api/organized-results')[0])
        self.assertFalse(list(self.destination.iterdir()))

    def test_saved_settings_are_backed_up_and_restored_snapshots_cannot_open_original_copies(self):
        from library_backup import export_backup, validate_backup, restore_backup
        self.file('a.jpg'); report, document, plan, settings, ops=self.setup_workflow()
        self.save(settings, action='preferences', destination=str(self.destination), all_batches=True)
        files,_=validate_backup(export_backup(self.output))
        self.assertIn('organize-settings.json', files)
        restored=restore_backup(self.output,files)
        restored_doc=json.loads((restored/report.name/'report.json').read_text(encoding='utf-8'))
        restored_ops=FileOperations(restored/report.name,MediaActions(restored_doc),lambda:plan,document=restored_doc)
        self.assertFalse(catalog(restored_ops))
        with self.assertRaises(ValueError):
            OrganizeSettings(restored,restored_doc).update({'action':'preferences'})

    def test_cyclic_or_nested_queue_references_are_rejected_without_recursion(self):
        report, _, _, _, ops=self.batch_sample();preview=ops.quick_preview(str(self.destination),True);queued=[]
        with mock.patch('file_operations.threading.Thread.start',autospec=True,side_effect=queued.append):
            job=ops.start(preview['token'])
        ops.request_stop(job['id']);queued[0].run()
        parent=json.loads((report/'operations'/(job['id']+'.json')).read_text(encoding='utf-8'))
        child=report/'operations'/(job['batch_ids'][0]+'.json')
        child.write_text(json.dumps({**parent,'id':job['batch_ids'][0]}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'嵌套'):
            ops.snapshot(job['id'])

    def test_actual_preview_renderer_pages_every_confirmed_and_skipped_item(self):
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r'''
const fs=require('fs'),vm=require('vm');
class E{constructor(t,s){this.textContent=s||'';this.children=[];}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};
const items=Array.from({length:203},(_,i)=>({path:'/src/'+i+'.jpg',relative:'照片/2021/05/'+i+'.jpg',target:'/copies/'+i+'.jpg',role:'照片'}));
const skipped=Array.from({length:151},(_,i)=>({path:'/skip/'+i,reason:'问题'+i}));const scope={$,make:(t,c,s)=>new E(t,s),executionPreview:{quick:true,queue:true,batch_count:2,items,skipped,groups:[]}};vm.createContext(scope);
const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(source.indexOf('let executionPage='),source.indexOf('async function previewOperation')),scope);scope.renderExecutionPreview();
if(!JSON.stringify($('execution-items').children).includes('自动执行 2 批'))throw Error('queue scope missing');
for(let page=1;page<4;page++)$('execution-next').onclick();
if(!JSON.stringify($('execution-items').children).includes('/skip/150'))throw Error('last skipped item unreachable');if(!$('execution-next').disabled)throw Error('last page must stop');if(!$('execution-page').textContent.includes('4/4'))throw Error('wrong page count');
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).parent/'management.js')],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_issue_and_result_ui_support_search_paging_csv_and_copy_actions(self):
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r'''
const fs=require('fs'),vm=require('vm'),browse=require(process.argv[2]);
class E{constructor(t,s){this.textContent=s||'';this.children=[];this.value='';this.attrs={};}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}click(){this.clicked=true;}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};let csv,action;
const copies=Array.from({length:51},(_,i)=>({id:'copy'+i,path:'/src/'+i+'.jpg',target:'/copies/2026/10/file'+i+'.jpg',folder:'/copies/2026/10',name:'file'+i+'.jpg',kind:'照片',bytes:2,can_open:true}));
const scope={...browse,$,make:(t,c,s)=>new E(t,s),saving:false,operating:false,selected:new Set(),mediaIds:{},picture(){},fileSize:String,render(){},error(m){throw Error(m);},Blob,URL:{createObjectURL(blob){csv=blob;return 'blob:generated';},revokeObjectURL(){}},setTimeout(f){f();},api:async(route,body)=>{if(route==='api/organized-results')return {items:copies,message:'success records'};if(route==='api/organized-results/action'){action=body;return {opened:true};}return {destination:'/copies',all_batches:true,revision:'saved'};}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(source.indexOf('// Source-scoped preferences')),scope);
(async()=>{await scope.refreshOrganizedResults();if(!$('organized-summary').textContent.includes('51 项'))throw Error('result count');$('organized-folders').children[0].onclick();$('organized-next').onclick();if(!JSON.stringify($('organized-items').children).includes('file50.jpg'))throw Error('result pagination');const button=$('organized-items').children[0].children.find(e=>e.textContent==='查看整理照片');await button.onclick();if(action.id!=='copy50'||action.action!=='open')throw Error('wrong copy action');$('organized-search').value='file10.jpg';$('organized-search').oninput();if(!$('organized-summary').textContent.includes('1 项'))throw Error('result search');
const skipped=Array.from({length:151},(_,i)=>({path:i===150?'=unsafe':'/skip/'+i,reason:i%2?'缺少身份':'已暂缓'}));scope.updateQuickIssues({items:[],skipped});$('quick-issues-next').onclick();$('quick-issues-next').onclick();$('quick-issues-next').onclick();if(!JSON.stringify($('quick-issues-list').children).includes('=unsafe'))throw Error('issue pagination');$('quick-issues-search').value='=unsafe';$('quick-issues-search').oninput();if(!$('quick-issues-summary').textContent.includes('1/151'))throw Error('issue search');$('quick-issues-export').onclick();const body=await csv.text();if(!body.includes('/skip/0')||!body.includes("'=unsafe")||body.split('\r\n').length!==152)throw Error('complete safe CSV');})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).parent/'management.js'),str(Path(__file__).parent/'management_browse.js')],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_copy_result_tree_starts_at_chosen_destination_and_links_are_refused(self):
        photo=self.file('20210506_IMG.jpg');_,_,_,_,ops=self.setup_workflow()
        preview=ops.quick_preview(str(self.destination),True)
        self.assertEqual(self.wait(ops,ops.start(preview['token']))['status'],'complete')
        item,=catalog(ops)
        self.assertEqual(item['result_parts'],[str(self.destination),'照片','2021','05'])
        copied=Path(item['target']);copied.unlink();make_symlink(copied,photo)
        with mock.patch('system_integration.open_path') as opening:
            with self.assertRaises((OSError,ValueError)):
                perform(ops,item['id'],'open')
            opening.assert_not_called()

    def test_unreadable_size_journal_is_refused_before_creating_operation_records(self):
        self.file('a.jpg');report,_,_,_,ops=self.setup_workflow()
        with self.assertRaisesRegex(ValueError,'大小上限'):
            ops._save_job({'id':'a'*24,'data':'x'*(4*1024*1024)})
        self.assertFalse((report/'operations').exists())
        self.assertFalse(list(self.destination.iterdir()))


if __name__=='__main__':unittest.main()
