"""Large library browsing stays correct without reading original media."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import media_center
from media_actions import media_id
import test_media_center as center_tests


class BrowsePerformanceTests(unittest.TestCase):
    def state(self, **values):
        return {'favorites': {}, 'collections': {}, 'recent': [], **values}

    def document(self, paths):
        return {'files': [{'path': path, 'kind': '照片' if path.endswith('.jpg') else '视频',
                           'bytes': 1, 'mtime': 1} for path in paths]}

    def test_many_folder_labels_do_not_reparse_each_rule_for_each_media(self):
        document = self.document([f'C:/Generated/Album{i % 100}/photo{i}.jpg' for i in range(500)])
        state = self.state(folder_labels={f'C:/Generated/Album{i}': f'人物{i}' for i in range(100)})
        with mock.patch.object(media_center, 'source_path', wraps=media_center.source_path) as parse:
            items = media_center.center_catalog(document, state)['items']
        self.assertLess(parse.call_count, 10000)
        self.assertEqual(len(items), 500)
        for index, item in enumerate(items):
            self.assertEqual(item['tags'], [f'人物{index % 100}'])

    def test_windows_case_insensitive_folder_rules_use_nearest_parent(self):
        for root in ['C:/Media', '//NAS/Share/Media']:
            with self.subTest(root=root):
                paths = [root.lower() + '/People/Clips/a.mp4', root.lower() + '/people/b.mp4',
                         root + '/people2/c.mp4', root + '/people/photo.jpg']
                state = self.state(folder_kinds={root.upper() + '/PEOPLE': 'movie',
                                                 root.upper() + '/PEOPLE/CLIPS': 'video'},
                                   folder_labels={root.upper() + '/PEOPLE': '人物'})
                items = media_center.center_catalog(self.document(paths), state)['items']
                self.assertEqual([item['kind'] for item in items], ['video', 'movie', 'video', 'photo'])
                self.assertEqual([item['tags'] for item in items], [['人物'], ['人物'], [], ['人物']])
                state['media_kinds'] = {media_id(paths[0]): 'movie'}
                self.assertEqual(media_center.center_catalog(self.document(paths), state)['items'][0]['kind'], 'movie')

    def test_posix_case_and_unicode_folders_remain_distinct(self):
        paths = ['/generated/People/a.jpg', '/generated/people/a.jpg',
                 '/generated/café/a.jpg', '/generated/cafe\u0301/a.jpg', '/generated/People2/a.jpg']
        state = self.state(folder_labels={'/generated/People': '大写', '/generated/café': '重音'})
        items = media_center.center_catalog(self.document(paths), state)['items']
        self.assertEqual([item['tags'] for item in items], [['大写'], [], ['重音'], [], []])

    def test_multi_folder_movie_inherits_all_labels_once_in_saved_order(self):
        paths = ['C:/Media/A/part1.mp4', 'c:/media/B/part2.mp4', 'C:/Media/A/part3.mp4']
        document = self.document(paths)
        document['video_library'] = {'groups': [{'group_key': 'a' * 64, 'metadata': {'title': 'Film'},
                                                 'tags': ['原有'], 'files': [{'path': path} for path in paths]}]}
        state = self.state(folder_labels={'C:/Media/B': 'B', 'C:/Media': '全部', 'C:/Media/A': 'A'})
        items = media_center.center_catalog(document, state)['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['tags'], ['原有', 'B', '全部', 'A'])
        self.assertEqual(len(items[0]['files']), 3)

    def test_editing_rules_takes_effect_on_next_snapshot_without_rescan(self):
        document = self.document(['C:/Media/People/a.mp4'])
        state = self.state(folder_labels={'C:/Media/People': '张三'}, folder_kinds={'C:/Media': 'movie'})
        first = media_center.center_catalog(document, state)['items'][0]
        state['folder_labels']['C:/Media/People'] = '李四'
        state['folder_kinds']['C:/Media/People'] = 'video'
        second = media_center.center_catalog(document, state)['items'][0]
        self.assertEqual((first['kind'], first['tags']), ('movie', ['张三']))
        self.assertEqual((second['kind'], second['tags']), ('video', ['李四']))
        state['folder_labels'].clear()
        state['folder_kinds'].clear()
        third = media_center.center_catalog(document, state)['items'][0]
        self.assertEqual((third['kind'], third['tags']), ('video', []))

    def test_saved_windows_rules_can_be_replaced_and_removed_using_current_spelling(self):
        with tempfile.TemporaryDirectory() as temporary:
            saved = media_center.CenterState(Path(temporary))
            original, current = 'C:/Media/People', 'c:/media/PEOPLE'
            for action, field, first, second, empty in [
                ('folder_label', 'label', '张三', '李四', ''),
                ('folder_kind', 'kind', 'movie', 'video', 'auto'),
            ]:
                key = 'folder_labels' if field == 'label' else 'folder_kinds'
                saved.update({'action': action, 'path': original, field: first}, set(), [original])
                saved.update({'action': action, 'path': current, field: second}, set(), [current])
                self.assertEqual(saved.snapshot()[key], {current: second})
                saved.update({'action': action, 'path': original, field: empty}, set(), [original])
                self.assertEqual(saved.snapshot()[key], {})

    def test_directory_controls_receive_direct_rule_separately_from_inheritance(self):
        state = self.state(folder_labels={'C:/Media/People': '张三'}, folder_kinds={'C:/Media/People': 'movie'})
        result = media_center.center_catalog(self.document(['c:/media/PEOPLE/child/a.mp4']), state)
        nodes = {path.casefold(): node for path, node in result['directories'].items()}
        self.assertEqual(nodes[r'c:\media\people']['folder_label'], '张三')
        self.assertEqual(nodes[r'c:\media\people']['folder_kind'], 'movie')
        self.assertEqual(nodes[r'c:\media\people\child']['folder_kind'], 'auto')
        self.assertEqual(nodes[r'c:\media\people\child']['folder_label'], '')
        self.assertEqual(result['items'][0]['kind'], 'movie')
        self.assertEqual(state['folder_labels'], {'C:/Media/People': '张三'})

    def test_large_natural_sorts_reuse_locale_setup_and_keep_stable_results(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Both platform CI jobs install Node for UI behaviour tests')
        code = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let setups=0;
function Collator(...args){setups++;return new Intl.Collator(...args);}
const scope={module:{exports:{}},Intl:{Collator}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const b=scope.module.exports,rows=Array.from({length:4000},(_,i)=>({id:String(i).padStart(6,'0'),title:'照片 '+(i%31),kind:'photo',files:[],tags:[],metadata:{},personal:{note:''},mtime:1,bytes:1}));
const before=JSON.stringify(rows),expected=[...rows].sort((a,b)=>a.title.localeCompare(b.title,'zh-CN',{numeric:true})||a.id.localeCompare(b.id)).map(row=>row.id);
for(const order of ['name','recent','oldest','size'])assert.equal(JSON.stringify(b.centerFilter(rows,{order}).map(row=>row.id)),JSON.stringify(expected));
const groups=rows.slice(0,200).map(row=>({files:[{id:row.id,name:row.title,mtime:1,bytes:1}]}));
assert.equal(JSON.stringify(b.centerReviewGroups({exact:groups},{order:'name'}).map(g=>g.files[0].id)),JSON.stringify([...groups].sort((a,b)=>a.files[0].name.localeCompare(b.files[0].name,'zh-CN',{numeric:true})||a.files[0].id.localeCompare(b.files[0].id)).map(g=>g.files[0].id)));
assert.equal(setups,1);assert.equal(JSON.stringify(rows),before);
'''
        result = subprocess.run([node, '-e', code, str(Path(__file__).with_name('media_center.js'))],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class DirectoryRuleDisplayTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_controls_use_resolved_windows_rule_and_failed_save_restores_it(self):
        self.run_ui(r'''
const path='c:\\media\\PEOPLE';
directories={[path]:{name:'PEOPLE',parent:'',children:[],ids:[movie.id],folder_label:'张三',folder_kind:'movie'}};
state.folder_labels={'C:\\Media\\People':'张三'};state.folder_kinds={'C:\\Media\\People':'movie'};
location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();
assert($('center-breadcrumb').children.some(node=>node.textContent==='查看标签：张三'));
assert($('center-breadcrumb').children.some(node=>node.textContent==='取消目录标签'));
const control=$('center-breadcrumb').children.find(n=>n.tagName==='LABEL').children[1];assert.equal(control.value,'movie');
scope.fetch=async()=>({ok:false,json:async()=>({error:'Cannot save'})});control.value='auto';await control.onchange();
assert.equal(control.value,'movie');assert.equal(operations.length,0);
''')
