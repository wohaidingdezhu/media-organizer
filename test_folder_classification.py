"""Directory classification is private metadata shared by Mac and Windows."""
from pathlib import Path
import tempfile
import unittest

from media_actions import media_id
from media_center import CenterState, center_catalog, validate_state
import test_media_center as center_tests


class FolderClassificationTests(unittest.TestCase):
    def state(self):
        return {'version': 1, 'favorites': {}, 'collections': {}, 'recent': []}

    def catalog(self, paths, state):
        return center_catalog({'files': [{'path': path, 'kind': '照片' if path.endswith('.jpg') else '视频', 'bytes': 1, 'mtime': 1} for path in paths]}, state)['items']

    def test_nearest_folder_and_file_override_work_for_both_path_styles(self):
        for root, slash in [('/generated/电影', '/'), (r'C:\Generated\电影', '\\')]:
            with self.subTest(root=root):
                child = root+slash+'短片'
                paths = [root+slash+'a.mp4', child+slash+'b.mp4', root+'2'+slash+'c.mp4', root+slash+'photo.jpg']
                state = self.state();state['folder_kinds'] = {root: 'movie', child: 'video'}
                self.assertEqual([item['kind'] for item in self.catalog(paths, state)], ['movie', 'video', 'video', 'photo'])
                state['media_kinds'] = {media_id(paths[0]): 'video', media_id(paths[1]): 'movie'}
                self.assertEqual([item['kind'] for item in self.catalog(paths, state)], ['video', 'movie', 'video', 'photo'])

    def test_saved_rule_applies_to_future_scans_without_same_named_disk_leak(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = CenterState(Path(temporary));folder = r'C:\Media\旅行'
            state.update({'action': 'folder_kind', 'path': folder, 'kind': 'movie'}, set(), [folder])
            paths = [folder+r'\new.mp4', r'D:\Media\旅行\new.mp4']
            self.assertEqual([item['kind'] for item in self.catalog(paths, CenterState(Path(temporary)).snapshot())], ['movie', 'video'])
            state.update({'action': 'folder_kind', 'path': folder, 'kind': 'auto'}, set(), [folder])
            self.assertEqual([item['kind'] for item in self.catalog(paths, state.snapshot())], ['video', 'video'])

    def test_grouped_movie_keeps_eligible_parts_and_directory_video_override_wins(self):
        paths = ['/generated/Movie/a.mp4', '/generated/Movie/clips/b.mp4']
        document = {'files': [{'path': path, 'kind': '视频'} for path in paths], 'video_library': {'groups': [{'group_key': 'a'*64, 'metadata': {'title': 'Film'}, 'files': [{'path': path} for path in paths]}]}}
        state = self.state();state['folder_kinds'] = {'/generated/Movie/clips': 'video'}
        items = center_catalog(document, state)['items']
        self.assertEqual([(item['kind'], len(item['files'])) for item in items], [('movie', 1), ('video', 1)])
        self.assertEqual({file['path'] for item in items for file in item['files']}, set(paths))

    def test_invalid_rule_is_rejected_without_changing_saved_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = CenterState(Path(temporary));before = state.snapshot()
            for payload in [{'path': '/outside', 'kind': 'movie'}, {'path': '/generated', 'kind': 'bad'}, {'path': '/generated', 'kind': []}]:
                with self.assertRaises(ValueError):state.update({'action': 'folder_kind', **payload}, set(), ['/generated'])
                self.assertEqual(state.snapshot(), before)
            for mapping in [{'relative': 'movie'}, {'/a/../b': 'movie'}, {'/generated': True}]:
                with self.assertRaises(ValueError):validate_state({**self.state(), 'folder_kinds': mapping})

    def test_single_file_can_return_to_inherited_classification(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = CenterState(Path(temporary));path = '/generated/a.mp4';identifier = media_id(path)
            state.update({'action': 'folder_kind', 'path': '/generated', 'kind': 'movie'}, set(), ['/generated'])
            state.update({'action': 'media_kind', 'ids': [identifier], 'kind': 'video'}, set(), videos=[identifier])
            self.assertEqual(self.catalog([path], state.snapshot())[0]['kind'], 'video')
            state.update({'action': 'media_kind', 'ids': [identifier], 'kind': 'auto'}, set(), videos=[identifier])
            self.assertEqual(self.catalog([path], state.snapshot())[0]['kind'], 'movie')


class FolderClassificationUITests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_folder_classification_saves_whole_directory_without_file_actions(self):
        self.run_ui(r'''
directories={'/generated':{name:'generated',parent:'',children:[],ids:[movie.id,photo.id]}};location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();
const control=$('center-breadcrumb').children.find(n=>n.tagName==='LABEL').children.find(n=>n.attrs['aria-label']==='本目录的视频分类');assert(control);control.value='movie';await control.onchange();assert.equal(calls.length,1);assert.deepEqual(calls[0].body,{action:'folder_kind',path:'/generated',kind:'movie'});assert.equal(operations.length,0);assert($('center-error').textContent.includes('分类已保存'));assert.equal($('center-error').attrs.role,'status');
''')

    def test_home_distinguishes_work_and_file_counts_and_identifies_mixed_photos(self):
        self.run_ui(r'''
movie.files.push({...movie.files[0],id:'9'.repeat(64),name:'Part 2.mp4',path:'/part2.mp4'});location.hash='#home';events.hashchange();await $('center-refresh').onclick();const counts=$('center-content').children.find(n=>n.className==='counts');assert(counts.children[0].children.some(n=>n.textContent==='部作品 · 2 个视频文件'));assert(counts.children[1].children.some(n=>n.textContent==='个普通视频文件'));
const wall=$('center-content').children.find(n=>n.className==='wall');const photoCard=wall.children.find(n=>n.className==='card photo-card');assert(photoCard);photoCard.children[0].onclick();assert.equal($('detail-title').textContent,'Photo');assert.equal(operations.length,0);
''')

    def test_restore_inherited_classification_updates_detail_controls_and_keeps_draft(self):
        self.run_ui(r'''
movie.manual_kind=true;movie.folder_kind=true;await $('center-refresh').onclick();$('center-content').children[0].children[0].children[0].onclick();const info=()=> $('detail-content').children[0].children[1],tags=()=>info().children.find(n=>n.className==='actions detail-controls').children[0];tags().value='Unsaved';
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='media_kind')movie.manual_kind=false;return originalFetch(path,options);};await info().children.find(n=>n.textContent==='恢复目录 / 自动分类').onclick();assert.equal(calls[0].body.kind,'auto');assert(!info().children.some(n=>n.textContent==='恢复目录 / 自动分类'));assert.equal(tags().value,'Unsaved');assert.equal(operations.length,0);
''')

    def test_failed_folder_save_remains_an_error_and_restores_control(self):
        self.run_ui(r'''directories={'/generated':{name:'generated',parent:'',children:[],ids:[movie.id]}};location.hash='#resources';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children[0].onclick();const control=$('center-breadcrumb').children.find(n=>n.tagName==='LABEL').children[1];scope.fetch=async()=>({ok:false,json:async()=>({error:'Could not save'})});control.value='movie';await control.onchange();assert.equal($('center-error').attrs.role,'alert');assert.equal($('center-error').textContent,'Could not save');assert(!control.disabled);assert.equal(control.value,'auto');assert.equal(operations.length,0);''')
