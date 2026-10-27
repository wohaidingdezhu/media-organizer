"""Batch selection reflects the media actually displayed on the home page."""
import unittest
import test_media_center as center_tests


class HomeSelectionTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_same_media_selection_stays_in_sync_across_home_sections(self):
        self.run_ui(r'''
state.recent=[photo.id];location.hash='#home';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();
const walls=()=>$('center-content').children.filter(n=>n.className==='wall');const check=card=>card.children.find(n=>n.className==='pick').children[0];
const recent=check(walls()[0].children[0]),updated=check(walls()[1].children.find(card=>card.children.some(n=>n.textContent==='Photo')));
recent.checked=true;recent.onchange();assert(updated.checked);assert($('selected-count').textContent.includes('1'));updated.checked=false;updated.onchange();assert(!recent.checked);assert($('selected-count').textContent.includes('0'));assert.equal(operations.length,0);
''')

    def test_select_page_includes_recent_items_outside_latest_twelve_once(self):
        self.run_ui(r'''
items=Array.from({length:13},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,mtime:n,files:[{...photo.files[0],id:(n+1).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));state.recent=[items[0].id,items[12].id];access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.id]));location.hash='#home';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('13'));const walls=$('center-content').children.filter(n=>n.className==='wall');for(const wall of walls.slice(0,2))for(const card of wall.children)assert(card.children.find(n=>n.className==='pick').children[0].checked);$('delete-selected').onclick();assert.equal(operations[0].ids.length,13);assert.equal(new Set(operations[0].ids).size,13);
''')

    def test_select_page_disabled_on_folder_tiles_and_empty_results(self):
        self.run_ui(r'''
location.hash='#collections';events.hashchange();$('manage-toggle').onclick();assert($('select-page').disabled);location.hash='#photos';events.hashchange();$('manage-toggle').onclick();assert(!$('select-page').disabled);$('query').value='not found';$('center-search').onsubmit({preventDefault(){}});assert($('select-page').disabled);assert.equal(operations.length,0);
''')
