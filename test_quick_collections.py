"""Create a collection from a selection without leaving the media list."""
import json
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server
from media_center import CenterState
from test_library_server import MemoryServer, request
import test_media_center as center_tests


class QuickCollectionStorageTests(unittest.TestCase):
    setUp = center_tests.MediaCenterTests.setUp
    catalog = center_tests.MediaCenterTests.catalog
    unchanged = center_tests.MediaCenterTests.unchanged

    def test_create_with_mixed_members_is_persisted_in_one_write(self):
        ids = [item['id'] for item in self.catalog()['items']]
        saved = self.state.update({'action': 'create', 'name': '旅行精选', 'ids': ids + ids[:1]}, set(ids))
        identifier = saved['created_collection_id']
        self.assertEqual(CenterState(self.output).snapshot()['collections'][identifier], {'name': '旅行精选', 'ids': ids})
        self.unchanged()

    def test_invalid_members_or_duplicate_name_never_leave_an_empty_collection(self):
        ids = [item['id'] for item in self.catalog()['items']]
        self.state.update({'action': 'create', 'name': 'Existing'}, set(ids))
        before = self.state.snapshot()
        for members in (None, 'bad', [{}], ['f'*64], ids[:1]*201):
            with self.subTest(members=members), self.assertRaises(ValueError):
                self.state.update({'action': 'create', 'name': 'New', 'ids': members}, set(ids))
            self.assertEqual(self.state.snapshot(), before)
        with self.assertRaises(ValueError):
            self.state.update({'action': 'create', 'name': 'existing', 'ids': ids}, set(ids))
        self.assertEqual(self.state.snapshot(), before)
        self.unchanged()

    def test_http_create_with_selection_preserves_media(self):
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(self.report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        _, raw = request(server, prefix+'api/center')
        ids = [item['id'] for item in json.loads(raw)['items']]
        headers, raw = request(server, prefix+'api/center', 'POST',
                               {'action': 'create', 'name': 'Mixed', 'ids': ids}, origin='http://127.0.0.1:43210')
        self.assertIn('200 OK', headers)
        saved = json.loads(raw)
        self.assertEqual(saved['collections'][saved['created_collection_id']]['ids'], ids)
        self.unchanged()


class QuickCollectionInteractionTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_create_from_selection_stays_in_list_and_targets_new_collection(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();assert($('create-selection-collection').disabled);assert($('target-collection').hidden);
$('select-page').onclick();assert(!$('create-selection-collection').disabled);scope.window.scrollY=700;$('create-selection-collection').onclick();assert($('collection-help').textContent.includes('1 项'));$('collection-name').value='Trip';
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body?.action==='create'){calls.push({path,body});state.collections.new={name:body.name,ids:body.ids};return {ok:true,json:async()=>({...state,created_collection_id:'new'})};}return originalFetch(path,options);};
await $('collection-save').onsubmit({preventDefault(){}});assert(!$('collection-form').open);assert.equal($('center-title').textContent,'照片');assert.equal(scope.window.scrollY,700);assert($('selected-count').textContent.includes('1'));assert.deepEqual(Array.from(state.collections.new.ids),[photo.id]);assert.equal($('target-collection').value,'new');assert(!$('target-collection').hidden);assert.equal($('center-error').attrs.role,'status');assert($('center-error').textContent.includes('已加入 1 项'));assert.equal(calls.filter(c=>c.body.action==='members').length,0);assert.equal(operations.length,0);
''')

    def test_cancel_keeps_selection_and_plain_create_never_reuses_old_members(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();$('create-selection-collection').onclick();$('collection-cancel').onclick();assert($('selected-count').textContent.includes('1'));assert.equal(calls.length,0);
location.hash='#collections';events.hashchange();$('create-collection').onclick();$('collection-name').value='Empty';await $('collection-save').onsubmit({preventDefault(){}});assert.deepEqual(Array.from(calls.at(-1).body.ids),[]);assert.equal(operations.length,0);
''')

    def test_failed_create_keeps_members_and_name_for_retry(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();$('create-selection-collection').onclick();$('collection-name').value='Trip';
const originalFetch=scope.fetch;let fail=true;scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body?.action==='create'){calls.push({path,body});return {ok:!fail,json:async()=>fail?{error:'Name already exists'}:state};}return originalFetch(path,options);};
await $('collection-save').onsubmit({preventDefault(){}});assert($('collection-form').open);assert.equal($('collection-name').value,'Trip');assert($('collection-error').textContent.includes('Name already exists'));assert($('selected-count').textContent.includes('1'));fail=false;$('collection-name').value='Trip 2';await $('collection-save').onsubmit({preventDefault(){}});assert.deepEqual(Array.from(calls.at(-1).body.ids),[photo.id]);assert.equal(operations.length,0);
''')

    def test_saved_create_with_failed_refresh_retries_same_collection_without_members(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();$('create-selection-collection').onclick();$('collection-name').value='Trip';
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body?.action==='create'){calls.push({path,body});state.collections.new={name:body.name,ids:body.ids};failCenter=true;return {ok:true,json:async()=>({...state,created_collection_id:'new'})};}return originalFetch(path,options);};
await $('collection-save').onsubmit({preventDefault(){}});assert($('collection-form').open);assert($('collection-error').textContent.includes('已保存'));failCenter=false;await $('collection-save').onsubmit({preventDefault(){}});assert.equal(calls.at(-1).body.action,'rename');assert.equal(calls.at(-1).body.id,'new');assert.equal(calls.at(-1).body.ids,undefined);assert.equal(Object.keys(state.collections).length,1);assert.deepEqual(Array.from(state.collections.new.ids),[photo.id]);assert.equal(operations.length,0);
''')

    def test_empty_collection_explains_how_to_add_media(self):
        self.run_ui(r'''
state.collections.empty={name:'Empty',ids:[]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='Empty')).onclick();assert($('center-content').children.some(n=>n.textContent.includes('勾选后选择此合集')));assert.equal(operations.length,0);
''')

    def test_collection_from_another_scan_does_not_claim_to_be_empty(self):
        self.run_ui(r'''
state.collections.other={name:'Other scan',ids:['f'.repeat(64)]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='Other scan')).onclick();assert($('center-content').children.some(n=>n.textContent.includes('不在当前扫描结果')));assert.deepEqual(state.collections.other.ids,['f'.repeat(64)]);assert.equal(operations.length,0);
''')
