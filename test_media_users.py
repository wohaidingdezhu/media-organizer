"""Username management, report-only plans and actual shared UI with generated files."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

from file_operations import FileOperations
import library_server
from media_actions import MediaActions, file_signature, media_id
from media_users import UserCatalog, NAME, clean_name, validate_users, units_from
from organization_plan import OrganizationPlan, STATE_FILE
from test_library_server import MemoryServer, request


class MediaUserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.report, self.source, self.destination = [self.root / name for name in ('scan-users', 'samples', 'copies')]
        for path in (self.report, self.source, self.destination):
            path.mkdir()
        records = []
        for number, name in enumerate(('photo.png', 'other.png', 'ABC-123-CD1.mp4', 'ABC-123-CD2.mp4', 'ABC-123-4K.mp4')):
            path = self.source / name
            path.write_bytes(bytes([number+1]) * (11+number))
            info = path.stat()
            records.append({'path': str(path), 'kind': '照片' if number < 2 else '视频', 'bytes': info.st_size,
                            'mtime': info.st_mtime, 'source_signature': file_signature(info),
                            'suggested_path': ('照片/旧/' if number < 2 else '视频/旧/') + name})
        self.key = 'a' * 64
        self.document = {'files': records, 'roots': [str(self.source)], 'duplicates': [],
            'video_library': {'groups': [{'tag_key': self.key, 'title': 'ABC-123', 'edition': edition,
                                          'files': records[start:end]} for edition,start,end in (('1080p',2,4),('4K',4,5))]}}
        self.catalog = UserCatalog(self.root, lambda: self.document)
        self.plan = OrganizationPlan(self.report, self.document)
        self.originals = {item['path']: (Path(item['path']).read_bytes(), Path(item['path']).stat().st_mtime_ns) for item in records}
        self.photo = 'photo:' + media_id(records[0]['path'])
        self.movie = 'movie:' + self.key
        (self.report/'report.json').write_text(json.dumps(self.document), encoding='utf-8')
        (self.report/'report.html').write_text('<html>generated sample</html>', encoding='utf-8')
        (self.report/'library.html').write_text('<html>generated library</html>', encoding='utf-8')

    def unchanged(self):
        self.assertEqual(self.originals, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.originals})

    def update(self, action, **fields):
        return self.catalog.update({'action': action, 'revision': self.catalog.snapshot()['revision'], **fields})

    def create(self, name='张三'):
        self.update('create', name=name)
        return next(user['id'] for user in self.catalog.snapshot()['users'] if user['name'] == name)

    def selected(self):
        return {'ids': [self.photo, self.movie], 'revision': self.catalog.snapshot()['revision']}

    def server(self):
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(self.report, self.root)
        return server, urlsplit(url).path.removesuffix('library.html')

    def test_crud_reassignment_deletion_and_same_path_new_scan_persistence(self):
        owner = self.create()
        other = self.create('李四')
        result = self.update('assign', ids=[self.photo, self.movie], user_id=owner)
        self.assertEqual(next(user for user in result['users'] if user['id'] == owner)['file_count'], 4)
        result = self.update('rename', user_id=owner, name='张三的新名字')
        self.assertEqual([item['username'] for item in result['items'] if item['id'] in (self.photo,self.movie)], ['张三的新名字']*2)
        self.document = copy.deepcopy(self.document)
        self.assertEqual(UserCatalog(self.root, lambda: self.document).snapshot(), result)
        self.update('assign', user_id=other, ids=[self.movie])
        result = self.update('delete', user_id=owner)
        self.assertEqual(next(item for item in result['items'] if item['id'] == self.photo)['user_id'], '')
        self.assertEqual(next(item for item in result['items'] if item['id'] == self.movie)['user_id'], other)
        self.update('assign', user_id='', ids=[self.movie])
        self.assertEqual(self.catalog.snapshot()['unassigned'], 3)
        self.unchanged()

    def test_portable_names_and_case_unicode_duplicates_do_not_write(self):
        for name in ('', '.', '..', 'CON', 'PRN.jpg', 'COM¹', 'a/b', 'a\\b', 'a:b', 'tail.', '\x00name', 'a'*61):
            with self.subTest(name=name), self.assertRaises(ValueError):
                clean_name(name)
        self.create('café')
        before = (self.root/NAME).read_bytes()
        for name in ('CAFE\u0301', 'café'):
            with self.assertRaisesRegex(ValueError, '已存在'):
                self.update('create', name=name)
            self.assertEqual((self.root/NAME).read_bytes(), before)
        self.assertEqual(clean_name(' 张三 '), '张三')

    def test_old_window_and_concurrent_mutation_allow_one_winner(self):
        snapshot = self.catalog.snapshot()
        barrier = threading.Barrier(2)
        results=[]
        def create(name):
            barrier.wait(timeout=5)
            try:
                UserCatalog(self.root, lambda: self.document).update({'action':'create','name':name,'revision':snapshot['revision']})
                results.append('saved')
            except ValueError:
                results.append('stale')
        threads=[threading.Thread(target=create,args=(name,)) for name in ('张三','李四')]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results,['saved','stale'])
        before=(self.root/NAME).read_bytes()
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.catalog.update({'action':'create','name':'王五','revision':snapshot['revision']})
        self.assertEqual((self.root/NAME).read_bytes(),before)

    def test_whole_movie_membership_changes_require_refresh_and_report_mixed_owners(self):
        owner = self.create()
        self.update('assign', user_id=owner, ids=[self.movie])
        old = self.catalog.snapshot()
        split = copy.deepcopy(self.document['video_library']['groups'][0])
        split['tag_key'] = 'b'*64
        split['files'] = [split['files'].pop()]
        self.document['video_library']['groups'][0]['files'].pop()
        self.document['video_library']['groups'].append(split)
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.catalog.update({'action':'assign','user_id':'','ids':[self.movie],'revision':old['revision']})
        self.update('assign',user_id='',ids=['movie:'+'b'*64])
        self.document['video_library']['groups'][0]['files'].extend(split['files'])
        self.document['video_library']['groups'].pop()
        mixed=self.catalog.snapshot()
        self.assertEqual(mixed['mixed'],1)
        with self.assertRaisesRegex(ValueError,'分配一个用户名'):
            self.catalog.plan(self.plan, {'ids':[self.movie],'revision':mixed['revision']})
        self.update('assign',user_id=owner,ids=[self.movie])
        self.assertEqual(self.catalog.snapshot()['mixed'],0)

    def test_preview_save_and_copy_preserve_originals_and_movie_filenames(self):
        owner=self.create()
        self.update('assign',user_id=owner,ids=[self.photo,self.movie])
        self.plan.set_target(media_id(self.document['files'][0]['path']), '照片/旧/custom.png')
        before=(self.report/STATE_FILE).read_bytes()
        payload=self.selected()
        preview=self.catalog.plan(self.plan,payload)
        self.assertEqual((self.report/STATE_FILE).read_bytes(),before)
        self.assertEqual(len(preview['items']),4)
        self.assertEqual(preview['items'][0]['after'],'用户/张三/照片/custom.png')
        for item in preview['items'][1:]:
            self.assertTrue(item['after'].startswith('用户/张三/电影/ABC-123-'))
            self.assertEqual(Path(item['path']).name,item['after'].split('/')[-1])
        result=self.catalog.plan(self.plan,{**payload,'plan_revision':preview['revision']},apply=True)
        self.assertTrue(result['saved'])
        self.plan.set_states(result['ids'],'include')
        ops=FileOperations(self.report,MediaActions(self.document),lambda:self.plan)
        execution=ops.preview('copy',result['ids'],str(self.destination))
        job=ops.start(execution['token'])
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            status=ops.snapshot(job['id'])
            if status['status']!='running': break
            time.sleep(.01)
        self.assertEqual(status['status'],'complete',status)
        for item in status['items']:
            self.assertEqual(Path(item['target']).read_bytes(),self.originals[item['path']][0])
            self.assertEqual(item['sha256'],hashlib.sha256(self.originals[item['path']][0]).hexdigest())
        self.unchanged()

    def test_rename_or_plan_edit_invalidates_confirmation_and_keeps_old_plan(self):
        owner=self.create()
        self.update('assign',user_id=owner,ids=[self.photo])
        payload={'ids':[self.photo],'revision':self.catalog.snapshot()['revision']}
        preview=self.catalog.plan(self.plan,payload)
        self.update('rename',user_id=owner,name='新名字')
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.catalog.plan(self.plan,{**payload,'plan_revision':preview['revision']},apply=True)
        self.assertFalse((self.report/STATE_FILE).exists())
        payload['revision']=self.catalog.snapshot()['revision']
        preview=self.catalog.plan(self.plan,payload)
        self.plan.set_states([media_id(self.document['files'][1]['path'])],'hold')
        before=(self.report/STATE_FILE).read_bytes()
        with self.assertRaisesRegex(ValueError,'计划已变化'):
            self.catalog.plan(self.plan,{**payload,'plan_revision':preview['revision']},apply=True)
        self.assertEqual((self.report/STATE_FILE).read_bytes(),before)

    def test_photo_name_collision_rejects_entire_batch(self):
        owner=self.create()
        self.document['files'][1]['suggested_path']='another/photo.png'
        plan=OrganizationPlan(self.report,self.document)
        ids=[item['id'] for item in self.catalog.snapshot()['items'] if item['kind']=='照片']
        self.update('assign',user_id=owner,ids=ids)
        with self.assertRaisesRegex(ValueError,'重名'):
            self.catalog.plan(plan,{'ids':ids,'revision':self.catalog.snapshot()['revision']})
        self.assertFalse((self.report/STATE_FILE).exists())

    def test_invalid_selection_unknown_owner_or_corrupt_data_are_preserved(self):
        owner=self.create()
        before=(self.root/NAME).read_bytes()
        for ids in ([],[self.photo,self.photo],['photo:unknown'],[None],[self.photo]*201):
            with self.assertRaises(ValueError):
                self.update('assign',user_id=owner,ids=ids)
            self.assertEqual((self.root/NAME).read_bytes(),before)
        with self.assertRaises(ValueError): self.update('assign',user_id='no-user',ids=[self.photo])
        (self.root/NAME).write_bytes(b'{invalid')
        with self.assertRaises(ValueError): self.catalog.snapshot()
        self.assertEqual((self.root/NAME).read_bytes(),b'{invalid')
        for value in ({'version':True,'users':{},'assignments':{}}, {'version':1,'users':{},'assignments':{'a'*64:'missing'}}):
            with self.assertRaises(ValueError): validate_users(value)

    def test_windows_unc_and_posix_reports_use_shared_group_names(self):
        for path in ('C:\\photos\\旅行.png', '\\\\server\\share\\旅行.png', '/photos/旅行.png'):
            units=units_from({'files':[{'path':path,'kind':'照片'}]})
            self.assertEqual(units[0]['title'],'旅行.png')

    def test_legacy_missing_movie_catalog_and_duplicate_paths(self):
        record=self.document['files'][2]
        unit,=units_from({'files':[record],'video_library':None,'previews':None})
        self.assertEqual(unit['kind'],'电影')
        with self.assertRaisesRegex(ValueError,'重复媒体路径'):
            units_from({'files':[record,record]})

    def test_expanded_movie_limit_is_checked_before_any_plan_save(self):
        owner=self.create()
        group=self.document['video_library']['groups'][0]
        self.document['video_library']['groups']=[group]
        for number in range(200):
            record={**self.document['files'][2], 'path':str(self.source/f'generated-{number}.mp4'), 'suggested_path':f'视频/old/generated-{number}.mp4'}
            self.document['files'].append(record)
            group['files'].append(record)
        self.update('assign',user_id=owner,ids=[self.movie])
        plan=OrganizationPlan(self.report,self.document)
        with self.assertRaisesRegex(ValueError,'1–200'):
            self.catalog.plan(plan,{'ids':[self.movie],'revision':self.catalog.snapshot()['revision']})
        self.assertFalse((self.report/STATE_FILE).exists())

    def test_http_management_origin_stale_page_and_rendered_resources(self):
        server,prefix=self.server()
        origin='http://127.0.0.1:43210'
        snapshot=json.loads(request(server,prefix+'api/users')[1])
        payload={'action':'create','name':'张三','revision':snapshot['revision']}
        self.assertIn('403',request(server,prefix+'api/users','POST',payload)[0])
        headers,body=request(server,prefix+'api/users','POST',payload,origin=origin)
        self.assertIn('200',headers)
        owner=json.loads(body)['users'][0]['id']
        self.assertIn('400',request(server,prefix+'api/users','POST',payload,origin=origin)[0])
        snapshot=json.loads(body)
        headers,body=request(server,prefix+'api/users','POST',{'action':'assign','ids':[self.photo,self.movie],'user_id':owner,'revision':snapshot['revision']},origin=origin)
        self.assertIn('200',headers)
        snapshot=json.loads(body)
        payload={'action':'plan-preview','ids':[self.photo,self.movie],'revision':snapshot['revision']}
        headers,body=request(server,prefix+'api/users','POST',payload,origin=origin)
        self.assertIn('200',headers)
        preview=json.loads(body)
        payload.update(action='plan-apply',plan_revision=preview['revision'])
        self.assertIn('200',request(server,prefix+'api/users','POST',payload,origin=origin)[0])
        headers,body=request(server,prefix+'users.html')
        self.assertIn('200',headers)
        self.assertNotIn(b'@@MEDIA_USERS@@',body)
        self.assertIn(b'function filteredUserItems',body)
        self.assertIn('确认删除用户名'.encode(),body)
        self.node("new(require('vm').Script)(JSON.parse(require('fs').readFileSync(0,'utf8')));",body.decode().split('<script>')[1].split('</script>')[0])
        self.unchanged()

    def test_mutations_respect_existing_operation_mutex(self):
        server,prefix=self.server()
        import file_operations
        snapshot=json.loads(request(server,prefix+'api/users')[1])
        file_operations._BATCH_LOCK.acquire()
        try:
            headers,body=request(server,prefix+'api/users','POST',{'action':'create','name':'张三','revision':snapshot['revision']},origin='http://127.0.0.1:43210')
            self.assertIn('400',headers)
            self.assertFalse((self.root/NAME).exists())
        finally:
            file_operations._BATCH_LOCK.release()

    def test_backup_round_trip_keeps_users_and_requires_rescan_for_operations(self):
        import media_scan
        from library_backup import export_backup,validate_backup,restore_backup
        reports=self.root/'backup-samples'
        with mock.patch('builtins.print'):
            self.assertEqual(media_scan.main([str(self.source),'--output',str(reports),'--no-image-metadata','--no-video-covers']),0)
        report=next(reports.glob('scan-*/report.json'))
        document=json.loads(report.read_text())
        catalog=UserCatalog(reports,lambda:document)
        state=catalog.update({'action':'create','name':'张三','revision':catalog.snapshot()['revision']})
        state=catalog.update({'action':'assign','user_id':state['users'][0]['id'],'ids':[state['items'][0]['id']],'revision':state['revision']})
        files,_=validate_backup(export_backup(reports))
        self.assertIn(NAME,files)
        restored=restore_backup(reports,files)
        document=json.loads(next(restored.glob('scan-*/report.json')).read_text())
        self.assertTrue(document['restored_snapshot'])
        self.assertEqual(UserCatalog(restored,lambda:document).snapshot()['users'],state['users'])
        self.unchanged()

    def node(self, code, value=None):
        result=subprocess.run(['node','-e',code,str(Path(__file__).with_name('media_users.js'))],input=json.dumps(value),text=True,encoding='utf-8',capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_user_ui_crud_filter_preview_and_error_guards(self):
        code=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.value='';this.events={};this.open=false;}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}get childNodes(){return this.children;}get options(){return this.children;}setAttribute(){}addEventListener(n,h){this.events[n]=h;}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};
const scope={document:{getElementById:get,createElement:t=>new E(t)},fetch:async()=>({ok:true,json:async()=>({revision:'r',users:[],items:[],jobs:[],warnings:[]})})};vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
(async()=>{
await new Promise(setImmediate);
const photo={id:'photo:1',title:'旅行.png',kind:'照片',username:'张三',user_id:'u',files:[{path:'C:\\pics\\旅行.png'}]},movie={id:'movie:2',title:'电影',kind:'电影',username:'未分配',user_id:'',files:[{path:'/films/movie.mp4'}]};
const data={revision:'r',users:[{id:'u',name:'张三',file_count:1,media_count:1}],items:[photo,movie]};
assert.equal(scope.filteredUserItems(data,'张三','','').length,1);assert.equal(scope.filteredUserItems(data,'','unassigned','电影')[0].id,movie.id);assert.equal(scope.filteredUserItems(data,'C:\\pics','','')[0].id,photo.id);
scope.userApi=async(p,route)=>route?{jobs:[],warnings:[]}:data;await scope.refreshUsers();assert.equal(get('user-plan').disabled,true);
scope.editUser(data.users[0]);get('user-name').value='张三改名';let calls=[];scope.userApi=async p=>{calls.push(p);return data;};await get('user-form').onsubmit({preventDefault(){}});assert.equal(calls[0].action,'rename');assert.equal(calls[0].revision,'r');assert.equal(get('user-editor').open,false);
get('user-select-page').onclick();assert.equal(get('user-selected').textContent.includes('2'),true);get('assign-user').value='u';await get('user-assign').onclick();assert.deepEqual([...calls[1].ids],['photo:1','movie:2']);
scope.userApi=async p=>{calls.push(p);return {revision:'plan-r',changed_count:2,ids:['a'.repeat(64)],items:[{path:'sample',before:'old',after:'用户/张三/photo.png'}]};};await get('user-plan').onclick();assert.equal(get('user-plan-dialog').open,true);await get('user-plan-save').onclick();assert.equal(calls.at(-1).plan_revision,'plan-r');assert.equal(get('user-plan-dialog').open,false);assert.ok(get('user-notice').children.at(-1).href.startsWith('organize.html#user-plan='));
await get('user-plan').onclick();scope.userApi=async()=>{throw Error('用户名已变化');};await get('user-plan-save').onclick();assert.equal(get('user-plan-save').disabled,true);assert.ok(get('user-plan-error').textContent.includes('已变化'));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        self.node(code)
