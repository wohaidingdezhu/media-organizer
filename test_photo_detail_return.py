"""Closing a cross-page photo preview returns to the last viewed photo."""
import unittest
import test_media_center as center_tests


class PhotoDetailReturnTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_close_after_cross_page_navigation_follows_last_photo(self):
        self.run_ui(r'''
items=Array.from({length:52},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,mtime:100-n,files:[{...photo.files[0],id:(n+1).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();const wall=$('center-content').children.find(n=>n.className==='wall photo-wall');wall.children[47].children[0].onclick();assert.equal($('detail-title').textContent,'Photo 47');$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();assert.equal($('detail-title').textContent,'Photo 48');scope.window.scrollY=900;$('detail-close').onclick();$('media-detail').events.close();assert($('center-page').textContent.includes('2/2'));assert.equal($('center-content').children.find(n=>n.className==='wall photo-wall').children[0].children.find(n=>n.tagName==='H3').textContent,'Photo 48');assert($('selected-count').textContent.includes('48'));assert.equal(scope.window.scrollY,0);assert.equal(operations.length,0);
''')

    def test_close_on_same_page_keeps_existing_scroll(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();scope.window.scrollY=650;$('center-content').children[0].children[0].children[0].onclick();$('detail-close').onclick();$('media-detail').events.close();assert.equal(scope.window.scrollY,650);assert.equal(operations.length,0);
''')

    def test_view_change_does_not_return_to_old_photo_page(self):
        self.run_ui(r'''
items=Array.from({length:50},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,mtime:100-n,files:[{...photo.files[0],id:(n+1).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[47].children[0].onclick();$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();location.hash='#videos';events.hashchange();$('media-detail').events.close();assert.equal($('center-title').textContent,'视频');assert.equal(location.hash,'#videos');assert.equal(operations.length,0);
''')

    def test_review_preview_does_not_change_review_page(self):
        self.run_ui(r'''
const second={...photo,id:'f'.repeat(64),title:'Second',files:[{...photo.files[0],id:'9'.repeat(64),path:'/second.jpg'}]};items=[photo,second];review.exact=Array.from({length:12},(_,n)=>({files:[photo,second].map(item=>({...item.files[0],name:item.title,kind:'photo',media_kind:'photo',owner:item.id,metadata:{},mtime:n}))}));await $('center-refresh').onclick();location.hash='#review';events.hashchange();$('center-next').onclick();const group=$('center-content').children.find(n=>n.className==='review-group');group.children.find(n=>n.className==='review-files').children[0].children.find(n=>n.className==='cover').onclick();$('detail-close').onclick();$('media-detail').events.close();assert($('center-page').textContent.includes('2/2'));assert.equal(operations.length,0);
''')
