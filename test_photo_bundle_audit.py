"""Photo association reviews operate on generated reports, including offline data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server
import test_photo_bundle as fixture
from media_actions import MediaActions, media_id
from organization_plan import STATE_FILE
from photo_bundle import photo_bundle_audit
from test_library_server import MemoryServer, request


class PhotoBundleAuditTests(unittest.TestCase):
    setUp = fixture.PhotoBundleTests.setUp
    file = fixture.PhotoBundleTests.file
    scan = fixture.PhotoBundleTests.scan
    prepare = fixture.PhotoBundleTests.prepare

    def test_review_matches_copy_manifest_and_exposes_current_plans_without_modifying_them(self):
        paths, key, report, document, plan, ops = self.prepare()
        before = {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}
        state = (report / STATE_FILE).read_bytes()
        result = photo_bundle_audit(document, key, plan.snapshot()['items'])
        preview = ops.preview('copy', [key], str(self.destination), photo_bundle=True)
        self.assertEqual({item['path'] for item in result['items']}, {item['path'] for item in preview['items']})
        self.assertEqual(result['counts'], preview['bundles'][0]['counts'])
        self.assertEqual(result['photo_ids'], sorted([media_id(str(paths[1])), key], key=lambda identifier: str(ops.media.records[identifier]['path'])))
        self.assertEqual(result['identity_missing'], 0)
        self.assertEqual(next(item['plan'] for item in result['items'] if item['id'] == key)['state'], 'include')
        self.assertIsNone(next(item['plan'] for item in result['items'] if item['label'] == 'AAE'))
        self.assertEqual((report / STATE_FILE).read_bytes(), state)
        self.assertEqual(before, {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths})
        self.assertFalse(list(self.destination.iterdir()))

    def test_missing_identities_and_restored_offline_snapshot_remain_reviewable(self):
        paths, key, _, document, plan, _ = self.prepare()
        for item in document['sidecars']:
            item.pop('source_signature')
        document['restored_snapshot'] = True
        for path in paths:
            path.unlink()
        with mock.patch.object(MediaActions, 'validate', side_effect=AssertionError('audit must not validate original files')):
            result = photo_bundle_audit(document, key, plan.snapshot()['items'])
        self.assertTrue(result['restored_snapshot'])
        self.assertEqual(result['identity_missing'], 3)
        self.assertEqual(len(result['items']), 6)
        self.assertIn('不检查原文件', result['message'])

    def test_ambiguous_candidates_are_listed_and_never_part_of_photo_selection(self):
        paths, key, _, document, plan, _ = self.prepare(('IMG.jpg', 'IMG.jpeg', 'IMG.mov', 'IMG.mp4', 'IMG.xmp', 'IMG.jpg.xmp'))
        result = photo_bundle_audit(document, key, plan.snapshot()['items'])
        self.assertEqual(result['photo_ids'], [key])
        self.assertEqual({item['path'] for item in result['skipped']}, {str(path) for path in paths[1:5]})
        self.assertEqual(result['counts']['XMP'], 1)

    def test_single_photo_has_no_claim_of_damage_and_wrong_identifiers_are_rejected(self):
        _, key, _, document, plan, _ = self.prepare(('IMG.jpg',))
        result = photo_bundle_audit(document, key, plan.snapshot()['items'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['skipped'], [])
        for invalid in (None, 1, [], '../report.json', 'F' * 64, 'f' * 64):
            with self.subTest(identifier=invalid), self.assertRaises(ValueError):
                photo_bundle_audit(document, invalid)

    def test_server_offline_audit_requires_scanned_photo_and_uses_latest_plan(self):
        paths, key, report, _, plan, _ = self.prepare()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        before = (report / STATE_FILE).read_bytes()
        for path in paths:
            path.unlink()
        with mock.patch.object(MediaActions, 'validate', side_effect=AssertionError('HTTP audit must not read media')):
            headers, body = request(server, prefix + 'api/photo-bundles/' + key)
        self.assertIn('200 OK', headers)
        self.assertEqual(len(json.loads(body)['items']), 6)
        self.assertEqual((report / STATE_FILE).read_bytes(), before)
        plan.set_states([key], 'hold')
        _, body = request(server, prefix + 'api/photo-bundles/' + key)
        self.assertEqual(next(item['plan']['state'] for item in json.loads(body)['items'] if item['id'] == key), 'hold')
        for invalid in ('unknown', 'f' * 64, media_id(str(paths[2]))):
            headers, _ = request(server, prefix + 'api/photo-bundles/' + invalid)
            self.assertIn('400', headers)
        for page in ('photos.html', 'organize.html'):
            headers, body = request(server, prefix + page)
            self.assertIn('200 OK', headers)
            self.assertIn('配套文件核对'.encode(), body)
            self.assertIn(b'function openPhotoBundle', body)

    def test_report_only_windows_posix_and_unc_paths_keep_original_names_and_sizes(self):
        for folder, separator in (('C:\\samples', '\\'), ('/generated/samples', '/'), ('\\\\nas\\samples', '\\')):
            document = {'files': [{'path': folder + separator + 'IMG.JPG', 'kind': '照片', 'extension': 'jpg', 'bytes': 21},
                                  {'path': folder + separator + 'IMG.DNG', 'kind': '照片', 'extension': 'dng', 'bytes': 99}],
                        'sidecars': [{'path': folder + separator + 'IMG.JPG.xmp', 'extension': 'xmp', 'bytes': 3}]}
            result = photo_bundle_audit(document, media_id(document['files'][0]['path']))
            self.assertEqual([item['bytes'] for item in result['items']], [21, 99, 3])
            self.assertEqual([item['relative'] for item in result['items']], ['IMG.JPG', 'IMG.DNG', 'IMG.JPG.xmp'])
            self.assertEqual(result['identity_missing'], 3)

    def node(self, code):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Mac and Windows CI install Node for shared interface tests')
        result = subprocess.run([node, '-e', code, str(Path(__file__).parent / 'management.js')], text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def ui_fixture(self):
        return r"""
const fs=require('fs'),vm=require('vm');
class Element{constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.events={};this.value='';this.open=false;}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(){}addEventListener(name,handler){this.events[name]=handler;}showModal(){this.open=true;}close(){this.open=false;if(this.events.close)this.events.close();}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const items=[{id:'jpg',name:'IMG.JPG',kind:'照片',path:'/generated/IMG.JPG',state:'include'},{id:'raw',name:'IMG.DNG',kind:'照片',path:'/generated/IMG.DNG',state:'pending'},{id:'mov',name:'IMG.MOV',kind:'视频',path:'/generated/IMG.MOV'}];
const audit={photo_ids:['jpg','raw'],items:[{id:'jpg',path:items[0].path,label:'照片',bytes:12,plan:{state:'include',target:'用户/样例/照片/IMG.JPG'}},{id:'raw',path:items[1].path,label:'RAW',bytes:45,plan:{state:'pending',target:'照片/RAW/IMG.dng'}},{id:'mov',path:items[2].path,label:'配对视频候选',bytes:9,plan:{state:'hold',target:'视频/IMG.mov'}},{path:'/generated/IMG.xmp',label:'XMP',bytes:3,plan:null}],counts:{'照片':1,'RAW':1,'配对视频候选':1,'XMP':1,'AAE':0},skipped:[{path:'/generated/IMG.jpeg',reason:'同名照片有多项候选'}],message:'同名视频仅为候选，不能证明是 Live Photo。',identity_missing:1};
let apiCalls=[],planReads=0;
const scope={$,make:(tag,cls,text)=>new Element(tag,text||''),data:{items},selected:new Set(['mov']),mediaIds:Object.fromEntries(items.map(item=>[item.path,item.id])),handledTrash:new Set(),saving:false,operating:false,photoBundleInfo:null,photoBundleAnchor:null,photoBundleRevision:0,names:{include:'已纳入',pending:'待核对',hold:'暂缓'},fileSize:n=>String(n),filters:['state','kind','risk'],wallUsers:{reset(){}},page:4,buildFolders(){},render(){scope.syncPhotoBundleControls();},api:async route=>{apiCalls.push(route);return audit;},request:async()=>{planReads++;return {items};}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(0,source.indexOf('function picture(')),scope);
"""

    def test_actual_review_ui_displays_candidates_plans_warnings_and_only_selects_photos(self):
        self.node(self.ui_fixture() + r"""
(async()=>{const card=new Element();scope.appendPhotoBundleButton(items[0],card);await card.children[0].onclick();if(!apiCalls[0].endsWith('/jpg'))throw Error('wrong photo request');const text=JSON.stringify($('photo-bundle-items').children);if(!text.includes('XMP')||!text.includes('同名照片有多项候选')||!text.includes('重新扫描')||!text.includes('用户/样例'))throw Error('lost audit detail');if(!$('photo-bundle-message').textContent.includes('Live Photo'))throw Error('missing warning');if($('photo-bundle-select').disabled)throw Error('cannot select known photos');await $('photo-bundle-select').onclick();if([...scope.selected].join(',')!=='jpg,raw'||scope.data.items[0].state!=='include'||scope.data.items[1].state!=='pending'||planReads!==1)throw Error('selection changed plan or included video');if($('photo-bundle-dialog').open||$('risk').value!=='selected'||$('kind').value!=='照片'||scope.page!==0)throw Error('selected pair not visible');if(!$('notice').textContent.includes('替换'))throw Error('missing selection notice');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_review_selection_is_disabled_for_snapshot_cleaned_member_and_active_copy(self):
        self.node(self.ui_fixture() + r"""
(async()=>{await scope.openPhotoBundle(items[0]);for(const mode of ['snapshot','trashed','busy','unknown']){audit.restored_snapshot=mode==='snapshot';scope.handledTrash=new Set(mode==='trashed'?[items[1].path]:[]);scope.operating=mode==='busy';if(mode==='unknown')delete scope.mediaIds[items[1].path];scope.syncPhotoBundleControls();if(!$('photo-bundle-select').disabled)throw Error('selection allowed '+mode);}if(planReads)throw Error('selection auto ran');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_closed_or_replaced_review_ignores_late_result_and_failure_offers_retry(self):
        self.node(self.ui_fixture() + r"""
(async()=>{let pending=[];scope.api=route=>new Promise((resolve,reject)=>pending.push({resolve,reject}));const first=scope.openPhotoBundle(items[0]);$('photo-bundle-close').onclick();pending[0].resolve(audit);await first;if(scope.photoBundleInfo||$('photo-bundle-dialog').open)throw Error('late result restored closed review');const old=scope.openPhotoBundle(items[0]),next=scope.openPhotoBundle(items[1]);pending[2].resolve({...audit,message:'latest'});await next;pending[1].resolve({...audit,message:'old'});await old;if($('photo-bundle-message').textContent!=='latest')throw Error('old response overwrote current');const failed=scope.openPhotoBundle(items[0]);pending[3].reject(Error('offline'));await failed;if($('photo-bundle-retry').hidden||$('photo-bundle-error').hidden||!$('photo-bundle-select').disabled)throw Error('failed request enabled selection');scope.api=async()=>audit;await $('photo-bundle-retry').onclick();if(!scope.photoBundleInfo||!$('photo-bundle-error').hidden||$('photo-bundle-select').disabled)throw Error('retry did not recover');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_cancel_during_latest_plan_read_keeps_existing_selection_and_writes_nothing(self):
        self.node(self.ui_fixture() + r"""
(async()=>{await scope.openPhotoBundle(items[0]);let resolve;scope.request=()=>new Promise(done=>resolve=done);const selection=$('photo-bundle-select').onclick();$('photo-bundle-close').onclick();resolve({items});await selection;if([...scope.selected].join(',')!=='mov'||scope.saving)throw Error('cancel changed selection or left busy');if(items[0].state!=='include'||items[1].state!=='pending')throw Error('plan changed');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_plan_read_failure_or_new_operation_keeps_selection_and_recovers_controls(self):
        self.node(self.ui_fixture() + r"""
(async()=>{await scope.openPhotoBundle(items[0]);scope.request=async()=>{throw Error('plan unavailable');};await $('photo-bundle-select').onclick();if([...scope.selected].join(',')!=='mov'||scope.saving||$('photo-bundle-error').hidden||!$('photo-bundle-dialog').open)throw Error('failed read changed selection or locked controls');let resolve;scope.request=()=>new Promise(done=>resolve=done);const selection=$('photo-bundle-select').onclick();scope.operating=true;resolve({items});await selection;if([...scope.selected].join(',')!=='mov'||!$('photo-bundle-select').disabled||scope.saving)throw Error('new copy did not block selection');})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_confirmed_start_exits_empty_selection_filter_but_other_filters_and_failed_start_stay(self):
        self.node(self.ui_fixture() + r"""
Object.assign(scope,{executionPreview:{token:'generated'},monitorRevision:0,operationTimer:null,clearTimeout(){},error(){},refreshOperations(){},render(){}});vm.runInContext(source.slice(source.indexOf("$('execution-start').onclick"),source.indexOf('function activeOperation')),scope);
(async()=>{for(const mode of ['selected','copied','failed']){scope.executionPreview={token:'generated'};scope.operating=false;scope.selected=new Set(['jpg','raw']);$('execution-confirm').checked=true;$('risk').value=mode==='failed'?'selected':mode;scope.api=async()=>{if(mode==='failed')throw Error('not confirmed');return {};};await $('execution-start').onclick();if(mode==='failed'){if(scope.selected.size!==2||$('risk').value!=='selected')throw Error('unconfirmed start changed selection');}else if(scope.selected.size||$('risk').value!==(mode==='selected'?'':mode))throw Error('wrong post-start filter '+mode);}})().catch(e=>{console.error(e);process.exitCode=1;});
""")
