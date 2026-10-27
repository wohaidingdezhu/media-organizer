"""Filters follow the visible media and start changed results at the top."""
import unittest
import test_media_center as center_tests


class ContextualFilterTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_similar_photos_drop_video_only_filters_and_show_candidates(self):
        self.run_ui(r'''
const second={...photo.files[0],id:'f'.repeat(64),path:'/generated/second.jpg'};
review.similar=[{id:'pair',files:[photo.files[0],second].map(file=>({...file,kind:'photo',media_kind:'photo',owner:photo.id,metadata:{}}))}];
await $('center-refresh').onclick();location.hash='#review';events.hashchange();
$('media-kind').value='video';$('media-kind').onchange();$('filter-duration').value='short';$('filter-duration').onchange();$('filter-resolution').value='fhd';$('filter-resolution').onchange();$('filter-genre').value='Drama';$('filter-genre').onchange();scope.window.scrollY=900;
$('center-content').children[0].children[1].onclick();
assert($('center-subtitle').textContent.includes('1 组'));assert($('center-content').children.some(n=>n.className==='review-group'));
for(const id of ['media-kind','filter-duration','filter-resolution','filter-genre']){assert($(id).hidden,id);assert.equal($(id).value,'',id);}
assert.equal(scope.window.scrollY,0);assert.equal(operations.length,0);
$('center-content').children[0].children[0].onclick();assert(!$('media-kind').hidden);assert(!$('filter-duration').hidden);assert(!$('filter-genre').hidden);
''')

    def test_single_media_pages_do_not_offer_conflicting_types(self):
        self.run_ui(r'''
for(const view of ['movies','videos','photos']){
location.hash='#'+view;events.hashchange();assert($('media-kind').hidden,view);
$('media-kind').value=view==='photos'?'video':'photo';$('media-kind').onchange();assert.equal($('media-kind').value,'');
if(view==='photos')assert($('center-subtitle').textContent.includes('1 项媒体'));
}
assert($('filter-genre').hidden);location.hash='#favorites';events.hashchange();assert(!$('media-kind').hidden);assert(!$('filter-genre').hidden);assert.equal(operations.length,0);
''')

    def test_global_search_from_photos_allows_video_filters_then_clears_on_return(self):
        self.run_ui(r'''
movie.files[0].video={duration_seconds:60,width:1920,height:1080};await $('center-refresh').onclick();location.hash='#photos';events.hashchange();
$('query').value='Film';$('search-scope').value='all';$('center-search').onsubmit({preventDefault(){}});
assert(!$('media-kind').hidden);assert(!$('filter-duration').hidden);assert(!$('filter-genre').hidden);
$('media-kind').value='movie';$('media-kind').onchange();$('filter-duration').value='short';$('filter-duration').onchange();assert($('center-subtitle').textContent.includes('1 项媒体'));
$('clear-search').onclick();assert($('media-kind').hidden);assert($('filter-duration').hidden);assert.equal($('media-kind').value,'');assert.equal($('filter-duration').value,'');assert($('center-subtitle').textContent.includes('1 项媒体'));assert.equal(operations.length,0);
''')

    def test_photo_filter_in_mixed_view_hides_movie_genre_without_hiding_type(self):
        self.run_ui(r'''
location.hash='#resources';events.hashchange();$('filter-genre').value='Drama';$('filter-genre').onchange();$('media-kind').value='photo';$('media-kind').onchange();
assert(!$('media-kind').hidden);assert.equal($('media-kind').value,'photo');assert($('filter-genre').hidden);assert.equal($('filter-genre').value,'');
$('media-kind').value='';$('media-kind').onchange();assert(!$('filter-genre').hidden);assert.equal(operations.length,0);
''')

    def test_changed_results_start_at_top_and_selection_rules_still_apply(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();
for(const id of ['media-order','photo-layout']){scope.window.scrollY=1400;$(id).value=id==='media-order'?'name':'timeline';$(id).onchange();assert.equal(scope.window.scrollY,0);assert($('selected-count').textContent.includes('1'));}
scope.window.scrollY=1400;$('filter-year').value='2020';$('filter-year').onchange();assert.equal(scope.window.scrollY,0);assert($('selected-count').textContent.includes('0'));
scope.window.scrollY=1400;$('reset-filters').onclick();assert.equal(scope.window.scrollY,0);
scope.window.scrollY=1400;$('query').value='missing';$('center-search').onsubmit({preventDefault(){}});assert.equal(scope.window.scrollY,0);
scope.window.scrollY=1400;$('clear-search').onclick();assert.equal(scope.window.scrollY,0);assert.equal(operations.length,0);
''')
