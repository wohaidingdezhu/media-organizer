"""Multi-keyword searches use snapshot fields without touching original media."""
import unittest

import test_media_center as center_tests


class SearchKeywordTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_clearing_search_input_restores_directory_and_resets_selection(self):
        self.run_ui(r'''
directories={'/generated':{name:'generated',parent:'',children:[],ids:[photo.id]}};
location.hash='#resources';events.hashchange();await $('center-refresh').onclick();
$('center-content').children[0].children[0].onclick();
$('include-subfolders').checked=true;$('include-subfolders').onchange();
$('media-order').value='name';$('media-order').onchange();
$('query').value='Film';$('search-scope').value='all';$('center-search').onsubmit({preventDefault(){}});
$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('1'));
scope.window.scrollY=900;
$('query').value='';$('query').oninput({isComposing:false});
assert.equal($('center-title').textContent,'generated');assert($('clear-search').hidden);
assert($('center-subtitle').textContent.includes('1 项媒体'));
assert($('center-breadcrumb').children.some(n=>n.textContent==='/generated'));
assert($('include-subfolders').checked);assert.equal($('media-order').value,'name');
assert($('selected-count').textContent.startsWith('已选 0'));assert.equal(scope.window.scrollY,0);
assert.equal(calls.length,0);assert.equal(operations.length,0);
''')

    def test_editing_query_waits_for_submit_and_composition_does_not_clear_results(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();
$('query').value='Photo';$('center-search').onsubmit({preventDefault(){}});
$('manage-toggle').onclick();$('select-page').onclick();
const wall=$('center-content').children[0];
for(const [value,isComposing]of [['Camera',false],['',true]]){
  $('query').value=value;$('query').oninput({isComposing});
  assert.equal($('center-content').children[0],wall);
  assert($('selected-count').textContent.includes('1'));assert(!$('clear-search').hidden);
}
$('query').value='';$('query').oninput({isComposing:false});
assert.equal($('center-title').textContent,'照片');assert($('clear-search').hidden);
const restored=$('center-content').children[0];
$('select-page').onclick();$('query').oninput({isComposing:false});
assert.equal($('center-content').children[0],restored);assert($('selected-count').textContent.includes('1'));
assert.equal(calls.length,0);assert.equal(operations.length,0);
''')

    def test_keywords_match_across_fields_and_keep_current_scope(self):
        self.run_ui(r'''
photo.title='Café 旅行';photo.tags=['周末'];photo.personal.note='海边';
photo.files[0].path='C:\\Generated\\2024\\photo.jpg';
movie.title='Café 旅行';movie.tags=['周末'];movie.personal.note='海边';
movie.files[0].path='\\\\NAS\\Share\\2024\\movie.mp4';
await $('center-refresh').onclick();location.hash='#photos';events.hashchange();
const search=value=>{$('query').value=value;$('center-search').onsubmit({preventDefault(){}});};
search('  2024\tCAFE\u0301　周末\n海边  ');
assert($('center-subtitle').textContent.includes('1 项媒体'));
$('search-scope').value='all';$('search-scope').onchange();
assert($('center-subtitle').textContent.includes('2 项媒体'));
search('海边 2024 不存在');assert($('center-subtitle').textContent.includes('0 项媒体'));
search('海边 2024 CAFE\u0301');assert($('center-subtitle').textContent.includes('2 项媒体'));
$('clear-search').onclick();assert.equal($('query').value,'');
assert($('center-subtitle').textContent.includes('1 项媒体'));assert.equal(operations.length,0);
''')

    def test_review_keywords_match_one_member_and_keep_entire_group(self):
        self.run_ui(r'''
photo.tags=['旅行'];photo.personal.note='海边';
const first={...photo.files[0],path:'/generated/2024/Café.jpg',name:'Café.jpg',kind:'photo',media_kind:'photo',owner:photo.id,metadata:{}};
const second={...first,id:'f'.repeat(64),path:'C:\\Copies\\2023\\copy.jpg',name:'copy.jpg'};
review={exact:[{sha256:'1'.repeat(64),files:[first,second]}],similar:[{id:'2'.repeat(64),distance:3,files:[first,second]}],warnings:[]};
await $('center-refresh').onclick();location.hash='#review';events.hashchange();
const search=value=>{$('query').value=value;$('center-search').onsubmit({preventDefault(){}});};
const group=()=>$('center-content').children.find(n=>n.className==='review-group');
for(const mode of [0,1]){
  $('center-content').children[0].children[mode].onclick();
  search('海边 2024 CAFE\u0301 旅行');assert(group());
  assert.equal(group().children.find(n=>n.className==='review-files').children.length,2);
  search('2024 2023');assert(!group());
  search(' \t　');assert(group());
}
assert.equal(operations.length,0);
''')
