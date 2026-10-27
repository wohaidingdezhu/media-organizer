"""Refreshing a live detail preserves editing and truthful navigation."""
import unittest
import test_media_center as center_tests


class DetailRefreshTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_metadata_refresh_preserves_dirty_fields_but_updates_clean_fields(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();
const info=()=> $('detail-content').children[0].children[1], tags=()=>info().children.find(n=>n.className==='actions detail-controls').children[0], note=()=>info().children.find(n=>n.tagName==='TEXTAREA'), rating=()=>info().children.find(n=>n.tagName==='SELECT');
tags().value='Unsaved tag';note().value='Unsaved note';rating().value='4';
movie.metadata={...movie.metadata,year:'2026'};movie.local_tags=['Server tag'];movie.personal={rating:2,note:'Server note'};await $('center-refresh').onclick();
assert.equal(tags().value,'Unsaved tag');assert.equal(note().value,'Unsaved note');assert.equal(String(rating().value),'4');
tags().value='Server tag';note().value='Server note';rating().value='2';movie.metadata={...movie.metadata,year:'2027'};movie.local_tags=['New server tag'];movie.personal={rating:3,note:'New server note'};await $('center-refresh').onclick();
assert.equal(tags().value,'New server tag');assert.equal(note().value,'New server note');assert.equal(String(rating().value),'3');assert.equal(operations.length,0);
''')

    def test_operation_status_refresh_keeps_player_and_updates_delete_availability(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const grid=$('detail-content').children[0],file=grid.children[1].children.find(n=>n.className==='file'),actions=file.children.find(n=>n.className==='actions');
actions.children.find(n=>n.textContent==='页内预览').onclick();const player=file.children.find(n=>n.tagName==='VIDEO');const trash=actions.children.find(n=>n.textContent==='删除此文件…');
operationState={running:true,warnings:[]};await $('center-refresh').onclick();assert.equal($('detail-content').children[0],grid);assert(file.children.includes(player));assert(trash.disabled);
operationState={running:false,warnings:[]};await $('center-refresh').onclick();assert.equal($('detail-content').children[0],grid);assert(!trash.disabled);assert.equal(operations.length,0);
''')

    def test_removed_neighbor_updates_photo_counter_and_buttons(self):
        self.run_ui(r'''
const second={...photo,id:'f'.repeat(64),title:'Second',mtime:1};items=[photo,second];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();
const next=()=> $('detail-actions').children.find(n=>n.textContent==='下一张');assert(!next().disabled);items=[photo];await $('center-refresh').onclick();assert(next().disabled);assert($('detail-actions').children.some(n=>n.textContent==='1 / 1'));assert.equal($('detail-title').textContent,'Photo');assert.equal(operations.length,0);
''')

    def test_rebuild_during_tag_save_uses_visible_newer_draft_for_feedback(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const controls=()=> $('detail-content').children[0].children[1].children.find(n=>n.className==='actions detail-controls');
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body?.action==='labels')return new Promise(resolve=>finish=()=>{movie.local_tags=movie.tags=body.tags;resolve({ok:true,json:async()=>state});});return originalFetch(path,options);};
controls().children[0].value='Submitted';const saving=controls().children[1].onclick();movie.metadata={...movie.metadata,year:'2026'};await $('center-refresh').onclick();controls().children[0].value='New draft';finish();await saving;
assert.equal(controls().children[0].value,'New draft');assert($('detail-feedback').textContent.includes('尚未保存'));assert.deepEqual(movie.local_tags,['Submitted']);assert.equal(operations.length,0);
''')

    def test_rebuild_during_note_save_preserves_newer_note_rating_and_focus(self):
        self.run_ui(r'''
E.prototype.focus=function(){scope.document.activeElement=this;};E.prototype.setSelectionRange=function(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;};
$('center-content').children[0].children[0].children[0].onclick();const info=()=> $('detail-content').children[0].children[1], note=()=>info().children.find(n=>n.tagName==='TEXTAREA'), rating=()=>info().children.find(n=>n.tagName==='SELECT');
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/notes'){const body=JSON.parse(options.body);return new Promise(resolve=>finish=()=>{movie.personal={rating:body.rating,note:body.note};resolve({ok:true,json:async()=>({})});});}return originalFetch(path,options);};
note().value='Submitted';rating().value='3';const saving=info().children.find(n=>n.textContent==='保存评分与备注').onclick();note().focus();note().setSelectionRange(2,5,'forward');movie.metadata={...movie.metadata,year:'2026'};await $('center-refresh').onclick();
assert.equal(scope.document.activeElement,note());assert.equal(note().selectionStart,2);assert.equal(note().selectionEnd,5);assert.equal(note().value,'Submitted');note().value='New note';rating().value='5';finish();await saving;
assert.equal(note().value,'New note');assert.equal(String(rating().value),'5');assert($('detail-feedback').textContent.includes('尚未保存'));assert.equal(movie.personal.note,'Submitted');assert.equal(operations.length,0);
''')

    def test_draft_does_not_follow_user_to_another_photo(self):
        self.run_ui(r'''
const second={...photo,id:'f'.repeat(64),title:'Second',mtime:1,local_tags:['Second saved']};items=[photo,second];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();
const tags=()=> $('detail-content').children[0].children[1].children.find(n=>n.className==='actions detail-controls').children[0];tags().value='First unsaved';photo.metadata={...photo.metadata,width:100};await $('center-refresh').onclick();assert.equal(tags().value,'First unsaved');$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();assert.equal(tags().value,'Second saved');assert.equal($('detail-title').textContent,'Second');assert.equal(operations.length,0);
''')
