"""User feedback regressions: acknowledgement, categories, missing art and progress."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

from PIL import Image
import library_server
import media_gui
import media_scan
from media_actions import MediaActions, file_signature, media_id
from media_center import CenterState, center_catalog, validate_state
from preview_repair import PreviewRepair
from scan_progress import PREFIX, parse, progress
from scan_cache import ScanCache, valid_thumbnail
from test_library_server import MemoryServer, request
import test_media_center as center_tests


class MediaFeedbackTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve();self.source = self.root/'generated';self.source.mkdir()
        self.output = self.root/'reports';self.report = self.output/'scan-generated';self.report.mkdir(parents=True)
        self.photos = [self.source/(str(index)+'.jpg') for index in range(3)]
        for index,path in enumerate(self.photos):Image.new('RGB',(40,30),(40+index*30,100,150)).save(path)
        self.video = self.source/'ordinary.mp4';self.video.write_bytes(b'Generated test video')
        self.records = [dict(path=str(path),kind='视频' if path==self.video else '照片',bytes=path.stat().st_size,
                             mtime=path.stat().st_mtime,source_signature=file_signature(path.stat()),
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in [*self.photos,self.video]]
        self.doc = dict(roots=[str(self.source)],files=self.records,similar={'enabled':True,'pairs':[
            {'left':str(self.photos[0]),'right':str(self.photos[1]),'distance':3}]},previews={})
        self.state = CenterState(self.output)
        self.originals = {path:path.read_bytes() for path in [*self.photos,self.video]}

    def catalog(self):return center_catalog(self.doc,self.state.snapshot())

    def assert_unchanged(self):self.assertEqual(self.originals,{path:path.read_bytes() for path in self.originals})

    def test_acknowledgement_persists_restores_and_changed_identity_returns_for_review(self):
        pair = self.catalog()['review']['similar'][0]
        self.state.update({'action':'review_similarity','id':pair['id'],'dismissed':True},set(),pairs={pair['id']})
        self.assertFalse(self.catalog()['review']['similar']);self.assertEqual(len(self.catalog()['review']['dismissed_similar']),1)
        self.state = CenterState(self.output);self.doc['similar']['pairs'][0].update(left=str(self.photos[1]),right=str(self.photos[0]))
        self.assertFalse(self.catalog()['review']['similar'])
        self.state.update({'action':'review_similarity','id':pair['id'],'dismissed':False},set(),pairs={pair['id']})
        self.assertEqual(len(self.catalog()['review']['similar']),1)
        self.state.update({'action':'review_similarity','id':pair['id'],'dismissed':True},set(),pairs={pair['id']})
        self.records[0]['source_signature'][-1] += 1
        self.assertNotEqual(self.catalog()['review']['similar'][0]['id'],pair['id']);self.assert_unchanged()

    def test_manual_movie_classification_preserves_file_labels_favorite_and_originals(self):
        identifier = media_id(str(self.video));ids = {item['id'] for item in self.catalog()['items']}
        self.state.update({'action':'favorite','id':identifier,'value':True},ids)
        self.state.update({'action':'labels','id':identifier,'tags':['Local tag']},ids)
        for kind in ('movie','video'):
            self.state.update({'action':'media_kind','ids':[identifier],'kind':kind},ids,videos={identifier})
            item = next(item for item in self.catalog()['items'] if item['id']==identifier)
            self.assertEqual(item['kind'],kind);self.assertTrue(item['favorite']);self.assertEqual(item['tags'],['Local tag'])
        for payload in ({'action':'media_kind','ids':[media_id(str(self.photos[0]))],'kind':'movie'},
                        {'action':'media_kind','ids':[identifier],'kind':[]},
                        {'action':'review_similarity','id':'f'*64,'dismissed':True}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):self.state.update(payload,ids,videos={identifier})
        self.assert_unchanged()

    def test_auto_movie_with_manual_override_never_crashes_on_empty_group(self):
        self.doc['video_library']={'groups':[{'files':[self.records[-1]],'group_key':'c'*64,'metadata':{'title':'Generated Film'}}]}
        identifier=media_id(str(self.video))
        self.state.update({'action':'media_kind','ids':[identifier],'kind':'video'},set(),videos={identifier})
        video, = [item for item in self.catalog()['items'] if item['kind']!='photo']
        self.assertEqual(video['kind'],'video');self.assertEqual(video['id'],identifier)

    def test_center_api_checks_pair_ids_video_kind_and_origin(self):
        (self.report/'report.json').write_text(json.dumps(self.doc),encoding='utf-8')
        with mock.patch.object(library_server,'ThreadingHTTPServer',MemoryServer):server,url=library_server.create_library_server(self.report,self.output)
        prefix=urlsplit(url).path.removesuffix('library.html');pair=self.catalog()['review']['similar'][0]
        payload={'action':'review_similarity','id':pair['id'],'dismissed':True}
        self.assertIn('403',request(server,prefix+'api/center','POST',payload,origin='http://foreign.invalid')[0])
        self.assertIn('200',request(server,prefix+'api/center','POST',payload,origin='http://127.0.0.1:43210')[0])
        current=json.loads(request(server,prefix+'api/center')[1]);self.assertFalse(current['review']['similar'])
        self.assertIn('200',request(server,prefix+'api/center','POST',{'action':'review_similarity','id':pair['id'],'dismissed':False},origin='http://127.0.0.1:43210')[0])
        self.assertIn('400',request(server,prefix+'api/center','POST',{**payload,'id':'f'*64},origin='http://127.0.0.1:43210')[0])
        self.assertIn('400',request(server,prefix+'api/center','POST',{'action':'media_kind','kind':'movie','ids':[media_id(str(self.photos[0]))]},origin='http://127.0.0.1:43210')[0]);self.assert_unchanged()

    def test_photo_preview_real_decoder_survives_restart_and_fills_similar_pairs(self):
        manager=PreviewRepair(self.report,MediaActions(self.doc),self.doc);identifier=media_id(str(self.photos[0]))
        relative=manager.generate(identifier)['preview'];self.assertTrue((self.report/relative).is_file())
        reopened=PreviewRepair(self.report,MediaActions(self.doc),self.doc);current=reopened.apply(self.catalog())
        self.assertEqual(next(item for item in current['items'] if item['id']==identifier)['preview'],relative)
        self.assertEqual(current['review']['similar'][0]['files'][0]['preview'],relative)
        self.assert_unchanged()

    def test_repaired_photo_beyond_scan_budget_is_reused_without_decoding_again(self):
        self.doc['options']={'analysis_fingerprint':'generated'}
        (self.report/'report.json').write_text(json.dumps(self.doc),encoding='utf-8');(self.report/'report.html').write_text('Generated')
        manager=PreviewRepair(self.report,MediaActions(self.doc),self.doc);manager.generate(media_id(str(self.photos[2])))
        cache=ScanCache(self.output,[str(self.source)],'generated');destination=self.root/'new-report';destination.mkdir()
        records=[{**record,'_signature':tuple(record['source_signature'])} for record in self.records]
        def generated(path,thumbnail,**kwargs):Image.new('RGB',(10,10)).save(thumbnail)
        with mock.patch.object(media_scan,'ImageProbeWorker') as worker:
            worker.return_value.request.side_effect=generated
            previews=media_scan.export_previews(destination,records,{'pairs':[]},Path('generated-helper'),[],photo_limit=1,cache=cache)
        self.assertEqual(set(previews),{str(self.photos[0]),str(self.photos[2])});self.assertEqual(worker.return_value.request.call_count,1);self.assert_unchanged()

    def test_broken_scan_preview_uses_repair_after_restart_and_next_scan(self):
        path = str(self.photos[0]);identifier = media_id(path)
        self.doc['previews'][path] = 'previews/broken.png'
        (self.report/'previews').mkdir();(self.report/'previews'/'broken.png').write_bytes(b'truncated png')
        self.doc['options'] = {'analysis_fingerprint':'generated'}
        (self.report/'report.json').write_text(json.dumps(self.doc),encoding='utf-8')
        (self.report/'report.html').write_text('Generated')
        relative = PreviewRepair(self.report,MediaActions(self.doc),self.doc).generate(identifier)['preview']
        catalog = PreviewRepair(self.report,MediaActions(self.doc),self.doc).apply(self.catalog())
        self.assertEqual(next(item for item in catalog['items'] if item['id']==identifier)['preview'],relative)
        self.assertEqual(catalog['review']['similar'][0]['files'][0]['preview'],relative)
        cache = ScanCache(self.output,[str(self.source)],'generated');target = self.root/'reused.png'
        self.assertTrue(cache.thumbnail({**self.records[0],'_signature':tuple(self.records[0]['source_signature'])},target))
        self.assertTrue(valid_thumbnail(target.read_bytes()));self.assert_unchanged()

    def test_missing_and_corrupt_repair_can_regenerate_without_touching_originals(self):
        manager = PreviewRepair(self.report,MediaActions(self.doc),self.doc);identifier = media_id(str(self.photos[0]))
        target = self.report/manager.generate(identifier)['preview']
        for damage in ('missing','corrupt','restart'):
            if damage=='missing':target.unlink()
            else:target.write_bytes(b'broken generated cache')
            if damage=='restart':manager=PreviewRepair(self.report,MediaActions(self.doc),self.doc)
            with self.subTest(damage=damage):
                self.assertEqual(self.report/manager.generate(identifier)['preview'],target)
                self.assertTrue(valid_thumbnail(target.read_bytes()))
        self.assertFalse(list(target.parent.glob('.repair-tmp-*')));self.assert_unchanged()

    def test_repair_refuses_non_file_destination_and_cleans_temporary(self):
        manager = PreviewRepair(self.report,MediaActions(self.doc),self.doc);identifier=media_id(str(self.photos[0]))
        target=self.report/'covers'/('repair-'+identifier+'.png');target.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'预览无法保存'):manager.generate(identifier)
        self.assertTrue(target.is_dir());self.assertEqual(list(target.parent.iterdir()),[target]);self.assert_unchanged()

    def test_report_write_failure_is_explained_and_keeps_existing_preview(self):
        manager = PreviewRepair(self.report,MediaActions(self.doc),self.doc);identifier=media_id(str(self.photos[0]))
        target=self.report/'covers'/('repair-'+identifier+'.png');target.parent.mkdir();target.write_bytes(b'old damaged report image')
        with mock.patch('preview_repair.fs.write_private_file',side_effect=PermissionError('Generated permission failure')):
            with self.assertRaisesRegex(ValueError,'预览无法保存'):manager.generate(identifier)
        self.assertEqual(target.read_bytes(),b'old damaged report image')
        self.assertFalse(list(target.parent.glob('.repair-tmp-*')));self.assertNotIn(identifier,manager.previews);self.assert_unchanged()

    def test_navigation_skips_detached_queued_previews_and_revisiting_requeues(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
const pending=[];const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>pending.push(()=>resolve({ok:true,json:async()=>({preview:'covers/generated.png'})})));}return originalFetch(path,options);};
photo.preview='';const other={...photo,id:'f'.repeat(64),files:[{...photo.files[0],id:'9'.repeat(64),path:'/other.jpg'}]};items=[photo,other];access.ids_by_path['/other.jpg']=other.files[0].id;location.hash='#photos';events.hashchange();await $('center-refresh').onclick();
const cards=$('center-content').children[0].children,first=cards[0].children[0],second=cards[1].children[0],observer=observers[0];observer.callback([{target:first,isIntersecting:true},{target:second,isIntersecting:true}]);assert.equal(calls.length,1);assert($('preview-progress-label').textContent.includes('0/2'));second.isConnected=false;pending.shift()();await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);assert.equal($('preview-progress').value,100);assert($('preview-progress-label').textContent.includes('1/1'));
second.isConnected=true;observer.callback([{target:second,isIntersecting:true}]);assert.equal(calls.length,2);pending.shift()();await new Promise(r=>setImmediate(r));assert.equal(second.children[0].tagName,'IMG');assert.equal(operations.length,0);
''')

    def test_manual_and_automatic_preview_share_queue_and_preserve_detail_draft(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>({preview:'covers/generated.png'})}));}return originalFetch(path,options);};
movie.preview='';await $('center-refresh').onclick();const card=$('center-content').children[0].children[0],cover=card.children[0];observers[0].callback([{target:cover,isIntersecting:true}]);cover.onclick();const grid=$('detail-content').children[0],art=grid.children[0],info=grid.children[1],tags=info.children.find(n=>n.className==='actions detail-controls').children[0];tags.value='Unsaved draft';const repair=info.children.at(-1).children[0];const waiting=repair.onclick();assert.equal(calls.filter(c=>c.path==='api/media/cover').length,1);finish();await waiting;await new Promise(r=>setImmediate(r));assert.equal($('detail-content').children[0],grid);assert.equal(tags.value,'Unsaved draft');assert.equal(art.children[0].tagName,'IMG');assert(repair.hidden);assert.equal(operations.length,0);
''')

    def test_broken_existing_preview_exposes_retry_and_manual_success_clears_failure(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
let fail=true;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>path==='api/media/cover'?{ok:!fail,json:async()=>fail?{error:'Decoder unavailable'}:{preview:'covers/fixed.png'}}:originalFetch(path,options);
location.hash='#photos';events.hashchange();const card=$('center-content').children[0].children[0],cover=card.children[0],repair=card.children.find(n=>n.className==='card-actions').children[0];assert(repair.hidden);cover.children[0].onerror();observers[0].callback([{target:cover,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert(!repair.hidden&&!repair.disabled);assert(repair.textContent.includes('重试'));assert($('preview-progress-label').textContent.includes('1 项未生成'));
fail=false;await repair.onclick();await new Promise(r=>setImmediate(r));assert.equal(cover.children[0].src,'covers/fixed.png');assert(repair.hidden);assert(!$('preview-progress-label').textContent.includes('未生成'));assert.equal($('preview-progress').value,100);assert.equal(operations.length,0);
''')

    def test_progress_reports_measured_phases_and_rejects_invalid_messages(self):
        text=io.StringIO()
        with mock.patch.dict('os.environ',{'MEDIA_ORGANIZER_PROGRESS':'1'}),contextlib.redirect_stdout(text):
            self.assertEqual(media_scan.main([str(self.source),'--output',str(self.root/'progress-reports'),'--no-video-covers']),0)
        updates=[item for line in text.getvalue().splitlines() if (item:=parse(line)) is not None]
        self.assertTrue(updates);self.assertIsNone(updates[0]['percent'])
        self.assertTrue(any(item['stage']=='photos' and item['percent']==100 for item in updates))
        self.assertTrue(any(item['stage']=='previews' and item['percent']==100 for item in updates))
        state=media_gui.DashboardState(self.root/'progress-reports');self.addCleanup(state.close)
        for item in updates:state.log(PREFIX+json.dumps(item))
        self.assertEqual(state.snapshot()['progress']['stage'],'index');self.assertFalse(state.logs)
        for value in ({'stage':[]},dict(stage='photos',completed=True,total=1),dict(stage='photos',completed=3,total=2),dict(stage='invalid',completed=0,total=1)):
            self.assertIsNone(parse(PREFIX+json.dumps(value)))
        self.assertIsNone(parse(PREFIX+'{broken'));self.assertEqual(progress('photos',3,4)['percent'],75);self.assert_unchanged()

    def test_cancelled_scan_keeps_last_progress_instead_of_claiming_complete(self):
        state=media_gui.DashboardState(self.root/'progress-reports');self.addCleanup(state.close);state.running=True
        process=mock.Mock();process.stdout=io.StringIO(PREFIX+json.dumps(progress('photos',1,2))+'\n');process.wait.return_value=130
        with mock.patch.object(media_gui,'compile_helpers'),mock.patch.object(media_gui.subprocess,'Popen',return_value=process):
            state._run_scan(['generated'],False,False)
        self.assertFalse(state.running);self.assertEqual(state.progress['percent'],50);self.assertIn('取消',state.status);self.assertTrue(process.stdout.closed)

    def test_invalid_acknowledgement_storage_is_rejected(self):
        for name,value in (('dismissed_similar',{'x':True}),('dismissed_similar',{'a'*64:'true'}),('media_kinds',{'a'*64:'photo'})):
            data=self.state.snapshot();data[name]=value
            with self.subTest(name=name,value=value),self.assertRaises(ValueError):validate_state(data)

    def test_disabled_decoding_keeps_scan_readonly_and_does_not_auto_generate(self):
        self.doc['image_inspection']={'enabled':False};self.doc['options']={'video_covers_enabled':False}
        self.assertEqual(self.catalog()['preview_policy'],{'images':False,'videos':False})
        self.run_ui(r'''
scope.IntersectionObserver=class{constructor(){throw Error('Disabled analysis must not enqueue decoding');}};const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const response=await originalFetch(path,options);if(path==='api/center'&&!options?.body){const body=await response.json();return {ok:true,json:async()=>({...body,preview_policy:{images:false,videos:false}})};}return response;};photo.preview='';items=[photo];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();assert(!$('center-error').textContent);assert.equal(calls.filter(c=>c.path==='api/media/cover').length,0);
''')

    def test_ui_acknowledge_hides_pair_and_restore_does_not_delete(self):
        self.run_ui(r'''
const other={...photo,id:'f'.repeat(64),files:[{...photo.files[0],id:'9'.repeat(64),path:'/other.jpg'}]};items.push(other);access.ids_by_path['/other.jpg']=other.files[0].id;
const pair={id:'1'.repeat(64),files:[photo,other].map(p=>({...p.files[0],kind:'photo',media_kind:'photo',owner:p.id,metadata:p.metadata})),distance:3};review.similar=[pair];review.dismissed_similar=[];
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body){const body=JSON.parse(options.body);if(body.action==='review_similarity'){review.similar=body.dismissed?[]:[pair];review.dismissed_similar=body.dismissed?[pair]:[];}}return originalFetch(path,options);};
location.hash='#review';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[1].onclick();const section=()=>$('center-content').children.find(n=>n.className==='review-group');await section().children.find(n=>n.className==='actions review-group-actions').children.find(n=>n.textContent==='确认不相似').onclick();assert(!section());assert.equal(operations.length,0);$('center-content').children[0].children.find(n=>n.textContent.startsWith('已确认不相似')).onclick();await section().children.find(n=>n.className==='actions review-group-actions').children.find(n=>n.textContent==='恢复到待核对').onclick();assert.equal(review.similar.length,1);assert.equal(operations.length,0);
''')

    def test_ui_empty_tags_and_movies_have_actions_without_rescan(self):
        self.run_ui(r'''
location.hash='#tags';events.hashchange();assert($('center-subtitle').textContent.includes('0 个标签'));assert($('center-content').children.some(n=>n.textContent.includes('不需要重新扫描')));assert($('center-content').children.some(n=>n.children.some(n=>n.textContent.includes('建标签'))));
movie.kind='video';items=[movie];await $('center-refresh').onclick();location.hash='#movies';events.hashchange();assert($('center-content').children.some(n=>n.textContent==='查看视频并标为电影'));$('center-content').children.find(n=>n.textContent==='查看视频并标为电影').onclick();assert.equal(location.hash,'#videos');$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1];await info.children.find(n=>n.textContent==='标为电影').onclick();assert(calls.some(c=>c.body.action==='media_kind'&&c.body.ids[0]===movie.files[0].id));assert.equal(operations.length,0);
''')

    def test_visible_photo_auto_preview_is_queued_once_and_has_progress(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>({preview:'covers/repaired.png'})};}return originalFetch(path,options);};
photo.preview='';items=[photo];location.hash='#photos';events.hashchange();await $('center-refresh').onclick();const observer=observers[0],target=observer.nodes.at(-1);observer.callback([{target,isIntersecting:false}]);assert.equal(calls.filter(c=>c.path==='api/media/cover').length,0);observer.callback([{target,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert.equal(calls.filter(c=>c.path==='api/media/cover').length,1);assert.equal(target.children[0].tagName,'IMG');assert.equal($('preview-progress').value,100);assert($('preview-progress-label').textContent.includes('完成'));assert.equal(operations.length,0);
''')

    def test_preview_failure_finishes_queue_and_keeps_retry_without_deleting(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>path==='api/media/cover'?{ok:false,json:async()=>({error:'Unsupported generated sample'})}:originalFetch(path,options);
photo.preview='';items=[photo];location.hash='#photos';events.hashchange();await $('center-refresh').onclick();const observer=observers[0],target=observer.nodes.at(-1);observer.callback([{target,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert($('preview-progress-label').textContent.includes('1 项未生成'));assert(target.children[0].children[1].textContent.includes('Unsupported'));assert.equal(operations.length,0);assert($('center-content').children[0].children[0].children.find(n=>n.className==='card-actions').children.some(n=>n.textContent==='重试补预览'&&!n.disabled&&!n.hidden));
''')

    def test_dashboard_progress_uses_measured_percent_and_unknown_total_is_indeterminate(self):
        import shutil,subprocess
        node=shutil.which('node');self.assertIsNotNone(node)
        code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),html=fs.readFileSync(process.argv[1]+'/dashboard.html','utf8');
class E{constructor(){this.children=[];this.classList={toggle(){}};}replaceChildren(){this.children=[];}append(...x){this.children.push(...x);}removeAttribute(key){delete this[key];}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);},scope={$: $,folderSelection:new Map(),lastFolders:'',lastLog:'',monitorEditing:false,document:{activeElement:null,createElement:()=>new E()},act(){}};
vm.createContext(scope);vm.runInContext(html.slice(html.indexOf('function syncScanScope('),html.indexOf('async function refresh(')),scope);
const data={folders:[],running:true,status:'Scanning',logs:[],monitor:{enabled:false},progress:{stage:'photos',label:'Photos',completed:2,total:4,percent:50,current:'sample.jpg'}};
scope.renderStatus(data);assert.equal($('scan-progress').value,50);assert.equal($('scan-banner-progress').value,50);assert($('scan-progress-label').textContent.includes('50%'));assert.equal($('scan-progress-current').textContent,'sample.jpg');assert(!$('scan-banner').hidden);
data.progress={stage:'discover',label:'Discovery',completed:3,total:null,percent:null,current:''};scope.renderStatus(data);assert.equal($('scan-progress').value,undefined);assert(!$('scan-progress-label').textContent.includes('%'));data.running=false;scope.renderStatus(data);assert($('scan-banner').hidden);assert($('bar').hidden);
'''
        result=subprocess.run([node,'-e',code,str(Path(__file__).parent)],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
