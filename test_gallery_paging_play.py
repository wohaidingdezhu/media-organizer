"""Gallery pages and multi-file playback use the same interface on both OSes."""
import unittest
import test_media_center as center_tests


class GalleryPagingPlayTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_multi_file_card_offers_choice_then_plays_only_chosen_file(self):
        self.run_ui(r'''
const second={...movie.files[0],id:'9'.repeat(64),name:'Part 2.mp4',path:'C:\\Part 2.mp4'};movie.files.push(second);access.ids_by_path[second.path]=second.id;await $('center-refresh').onclick();
const actions=$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions');const choose=actions.children.find(n=>n.textContent==='选择播放');assert(choose);choose.onclick();assert($('media-detail').open);assert.equal(calls.length,0);
const files=$('detail-content').children[0].children[1].children.filter(n=>n.className==='file');assert.equal(files.length,2);await files[1].children.find(n=>n.className==='actions').children.find(n=>n.textContent==='播放').onclick();const plays=calls.filter(c=>c.path==='api/media/action');assert.equal(plays.length,1);assert.equal(plays[0].body.id,second.id);assert.equal(operations.length,0);
''')

    def test_unavailable_version_does_not_hide_available_version_or_allow_open(self):
        self.run_ui(r'''
movie.kind='video';const second={...movie.files[0],id:'9'.repeat(64),name:'Unavailable.mp4',path:'/unavailable.mp4'};movie.files.push(second);location.hash='#videos';events.hashchange();await $('center-refresh').onclick();
$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='选择播放').onclick();const plays=$('detail-content').children[0].children[1].children.filter(n=>n.className==='file').map(file=>file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='播放'));assert(!plays[0].disabled);assert(plays[1].disabled);await plays[1].onclick();assert.equal(calls.length,0);assert.equal(operations.length,0);
''')

    def test_pages_fill_common_column_counts_without_losing_or_repeating_media(self):
        self.run_ui(r'''
items=Array.from({length:101},(_,n)=>({...movie,id:String(n).padStart(64,'0'),kind:'video',title:'Video '+n,mtime:101-n,files:[{...movie.files[0],id:String(n).padStart(64,'0'),path:'/generated/'+n+'.mp4'}]}));location.hash='#videos';events.hashchange();await $('center-refresh').onclick();const names=()=> $('center-content').children[0].children.map(card=>card.children.find(n=>n.tagName==='H3').textContent),seen=[];
assert.equal(names().length,48);seen.push(...names());$('center-next').onclick();assert.equal(names().length,48);seen.push(...names());$('center-next').onclick();assert.equal(names().length,5);seen.push(...names());assert.equal(new Set(seen).size,101);assert.equal(seen.length,101);assert($('center-next').disabled);$('center-prev').onclick();assert.equal(names()[0],'Video 48');assert.equal(operations.length,0);
''')

    def test_mixed_photo_dimensions_keep_row_order_and_full_detail_navigation(self):
        self.run_ui(r'''
items=[[800,200],[200,800],[400,400],[600,400]].map(([width,height],n)=>({...photo,id:String(n).padStart(64,'0'),title:'Shape '+n,mtime:4-n,metadata:{width,height,timeline_month:'2026-10',timeline_source:'EXIF 拍摄日期'}}));location.hash='#photos';events.hashchange();await $('center-refresh').onclick();
const wall=()=> $('center-content').children.find(n=>n.className==='wall photo-wall');assert.equal(wall().children.length,4);wall().children[1].children[0].onclick();assert.equal($('detail-title').textContent,'Shape 1');$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();assert.equal($('detail-title').textContent,'Shape 2');$('detail-close').onclick();$('photo-layout').value='timeline';$('photo-layout').onchange();assert.equal(wall().children.length,4);wall().children[3].children[0].onclick();assert.equal($('detail-title').textContent,'Shape 3');assert.equal(operations.length,0);
''')
