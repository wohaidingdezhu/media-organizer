"""Changing browsing categories must retain the user's library records."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server
from media_actions import media_id
from media_center import CenterState, center_catalog
from test_library_server import MemoryServer, request
import test_media_center as center_tests


class ClassificationContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state = CenterState(Path(self.temp.name))
        self.paths = ['/generated/part1.mp4', '/generated/part2.mp4']
        self.ids = [media_id(path) for path in self.paths]
        self.group = 'a'*64
        self.document = {'roots': ['/generated'], 'files': [{'path': path, 'kind': '视频'} for path in self.paths],
                         'video_library': {'groups': [{'group_key': self.group, 'metadata': {'title': 'Film'}, 'files': [{'path': path} for path in self.paths]}]}}

    def change(self, payload):
        current = center_catalog(self.document, self.state.snapshot())
        return self.state.update(payload, {item['id'] for item in current['items']}, current['directories'],
                                 videos=self.ids, document=self.document)

    def test_split_and_regroup_preserve_favorites_labels_collection_and_recent(self):
        self.state.update({'action': 'favorite', 'id': self.group, 'value': True}, {self.group})
        self.state.update({'action': 'labels', 'id': self.group, 'tags': ['旅行']}, {self.group})
        self.state.update({'action': 'recent', 'id': self.group}, {self.group})
        saved = self.state.update({'action': 'create', 'name': '精选'}, {self.group});collection = saved['created_collection_id']
        self.state.update({'action': 'members', 'id': collection, 'ids': [self.group]}, {self.group})
        self.change({'action': 'folder_kind', 'path': '/generated', 'kind': 'video'})
        catalog = center_catalog(self.document, self.state.snapshot())
        self.assertEqual([item['kind'] for item in catalog['items']], ['video', 'video'])
        self.assertTrue(all(item['favorite'] and item['local_tags'] == ['旅行'] for item in catalog['items']))
        self.assertEqual(set(catalog['state']['collections'][collection]['ids']), set(self.ids))
        self.assertEqual(set(catalog['recent']), set(self.ids))
        for identifier in self.ids:
            self.state.update({'action': 'favorite', 'id': identifier, 'value': False}, self.ids)
            self.state.update({'action': 'labels', 'id': identifier, 'tags': []}, self.ids)
        self.change({'action': 'folder_kind', 'path': '/generated', 'kind': 'auto'})
        catalog = center_catalog(self.document, self.state.snapshot());item = catalog['items'][0]
        self.assertFalse(item['favorite']);self.assertEqual(item['local_tags'], [])
        self.assertEqual(catalog['state']['collections'][collection]['ids'], [self.group])
        self.assertEqual(catalog['recent'], [self.group])

    def test_partial_reclassification_preserves_both_remaining_work_and_split_file(self):
        self.state.update({'action': 'favorite', 'id': self.group, 'value': True}, {self.group})
        self.change({'action': 'media_kind', 'ids': [self.ids[0]], 'kind': 'video'})
        items = center_catalog(self.document, self.state.snapshot())['items']
        self.assertEqual({item['id'] for item in items}, {self.group, self.ids[0]})
        self.assertTrue(all(item['favorite'] for item in items))
        self.assertEqual(sum(len(item['files']) for item in items), 2)

    def test_merging_excess_tags_refuses_without_partial_state_write(self):
        self.change({'action': 'folder_kind', 'path': '/generated', 'kind': 'video'})
        for index, identifier in enumerate(self.ids):
            self.state.update({'action': 'labels', 'id': identifier, 'tags': [str(index)+'-'+str(n) for n in range(11)]}, self.ids)
        before = self.state.snapshot()
        with self.assertRaisesRegex(ValueError, '20'):
            self.change({'action': 'folder_kind', 'path': '/generated', 'kind': 'auto'})
        self.assertEqual(self.state.snapshot(), before)

    def test_windows_paths_keep_records_separate_from_another_disk(self):
        self.paths = [r'C:\Generated\part1.mp4', r'C:\Generated\part2.mp4']
        self.ids = [media_id(path) for path in self.paths]
        other = r'D:\Generated\part1.mp4'
        self.document['roots'] = [r'C:\Generated', r'D:\Generated']
        self.document['files'] = [{'path': path, 'kind': '视频'} for path in self.paths+[other]]
        self.document['video_library']['groups'][0]['files'] = [{'path': path} for path in self.paths]
        self.state.update({'action': 'favorite', 'id': self.group, 'value': True}, {self.group})
        self.change({'action': 'folder_kind', 'path': r'C:\Generated', 'kind': 'video'})
        by_path = {item['files'][0]['path']: item for item in center_catalog(self.document, self.state.snapshot())['items']}
        self.assertTrue(all(by_path[path]['favorite'] for path in self.paths));self.assertFalse(by_path[other]['favorite'])

    def test_classification_keeps_collection_members_from_another_scan(self):
        unseen = 'f'*64
        saved = self.state.update({'action': 'create', 'name': '跨目录合集'}, {self.group, unseen});collection = saved['created_collection_id']
        self.state.update({'action': 'members', 'id': collection, 'ids': [self.group, unseen]}, {self.group, unseen})
        self.change({'action': 'media_kind', 'ids': self.ids, 'kind': 'video'})
        self.assertEqual(set(self.state.snapshot()['collections'][collection]['ids']), set(self.ids+[unseen]))


class ClassificationRouteTests(unittest.TestCase):
    setUp = center_tests.MediaCenterTests.setUp
    unchanged = center_tests.MediaCenterTests.unchanged

    def test_actual_api_reclassification_keeps_favorite_and_tags(self):
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(self.report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        _, raw = request(server, prefix+'api/center');movie = next(item for item in json.loads(raw)['items'] if item['kind'] == 'movie')
        for payload in [{'action': 'favorite', 'id': movie['id'], 'value': True},
                        {'action': 'labels', 'id': movie['id'], 'tags': ['保留标签']},
                        {'action': 'media_kind', 'ids': [file['id'] for file in movie['files']], 'kind': 'video'}]:
            headers, _ = request(server, prefix+'api/center', 'POST', payload, origin='http://127.0.0.1:43210');self.assertIn('200 OK', headers)
        _, raw = request(server, prefix+'api/center')
        item = next(item for item in json.loads(raw)['items'] if item['id'] == movie['files'][0]['id'])
        self.assertEqual(item['kind'], 'video');self.assertTrue(item['favorite']);self.assertEqual(item['local_tags'], ['保留标签']);self.unchanged()


class ClassificationInteractionTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_auto_detected_movie_can_be_corrected_and_disappearing_detail_has_feedback(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1];const change=info.children.find(n=>n.textContent==='改为普通视频');assert(change);
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='media_kind')items=[photo,{...movie,id:movie.files[0].id,kind:'video'}];return originalFetch(path,options);};await change.onclick();assert(!$('media-detail').open);assert($('center-error').textContent.includes('普通视频'));assert.equal($('center-error').attrs.role,'status');assert.equal(operations.length,0);
''')
