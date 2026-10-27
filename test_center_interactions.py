"""Deferred user actions stay attached to their original media or dialog."""
import unittest
import test_media_center as center_tests


class CenterInteractionTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_card_play_ignores_repeated_clicks_and_recovers_after_failure(self):
        self.run_ui(r'''
const play=()=> $('center-content').children[0].children[0].children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='播放');
let finish,count=0;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/action'){count++;return new Promise(resolve=>finish=()=>resolve({ok:false,json:async()=>({error:'Player unavailable'})}));}return originalFetch(path,options);};
const opening=play().onclick();assert(play().disabled);await play().onclick();assert.equal(count,1);finish();await opening;assert(!play().disabled);assert($('center-error').textContent.includes('Player unavailable'));assert.equal(operations.length,0);
''')

    def test_play_record_failure_explains_that_player_already_opened(self):
        self.run_ui(r'''
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='recent')return {ok:false,json:async()=>({error:'Read only records'})};return originalFetch(path,options);};
await $('center-content').children[0].children[0].children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='播放').onclick();assert($('center-error').textContent.includes('已打开'));assert($('center-error').textContent.includes('最近记录'));assert.equal(calls.filter(c=>c.path==='api/media/action').length,1);assert.equal(operations.length,0);
''')

    def test_note_save_preserves_and_identifies_newer_unsaved_input(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],note=info.children.find(n=>n.tagName==='TEXTAREA'),rating=info.children.find(n=>n.tagName==='SELECT');note.value='Submitted';rating.value='3';
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/notes'){const submitted=JSON.parse(options.body);return new Promise(resolve=>finish=()=>{movie.personal={rating:submitted.rating,note:submitted.note};resolve({ok:true,json:async()=>({})});});}return originalFetch(path,options);};
const saving=info.children.find(n=>n.textContent==='保存评分与备注').onclick();note.value='New draft';rating.value='5';finish();await saving;assert.equal(note.value,'New draft');assert.equal(String(rating.value),'5');assert.equal(movie.personal.note,'Submitted');assert($('detail-feedback').textContent.includes('尚未保存'));assert.equal(operations.length,0);
''')

    def test_collection_create_keeps_new_draft_and_next_save_renames_same_collection(self):
        self.run_ui(r'''
location.hash='#collections';events.hashchange();$('create-collection').onclick();$('collection-name').value='Submitted';let finish;const payloads=[],id='1'.repeat(32),originalFetch=scope.fetch;
scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body&&['create','rename'].includes(body.action)){payloads.push(body);state.collections[id]={name:body.name,ids:[]};if(body.action==='create')return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>({...state,created_collection_id:id})}));return {ok:true,json:async()=>state};}return originalFetch(path,options);};
const saving=$('collection-save').onsubmit({preventDefault(){}});assert($('collection-submit').disabled);$('collection-name').value='New draft';finish();await saving;assert($('collection-form').open);assert(!$('collection-submit').disabled);assert.equal($('collection-name').value,'New draft');assert($('collection-error').textContent.includes('尚未保存'));await $('collection-save').onsubmit({preventDefault(){}});assert(!$('collection-form').open);assert.equal(payloads[1].action,'rename');assert.equal(payloads[1].id,id);assert.equal(Object.keys(state.collections).length,1);assert.equal(operations.length,0);
''')

    def test_collection_create_then_refresh_failure_does_not_create_again(self):
        self.run_ui(r'''
location.hash='#collections';events.hashchange();$('create-collection').onclick();$('collection-name').value='Submitted';const payloads=[],id='2'.repeat(32),originalFetch=scope.fetch;
scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body&&['create','rename'].includes(body.action)){payloads.push(body);state.collections[id]={name:body.name,ids:[]};if(body.action==='create')failCenter=true;return {ok:true,json:async()=>({...state,created_collection_id:body.action==='create'?id:undefined})};}return originalFetch(path,options);};
await $('collection-save').onsubmit({preventDefault(){}});assert($('collection-form').open);assert($('collection-error').textContent.includes('已保存'));failCenter=false;$('collection-name').value='Changed name';await $('collection-save').onsubmit({preventDefault(){}});assert.equal(payloads[1].action,'rename');assert.equal(payloads[1].id,id);assert.equal(Object.keys(state.collections).length,1);assert(!$('collection-form').open);assert.equal(operations.length,0);
''')

    def test_card_and_detail_share_pending_play_and_completion_releases_both(self):
        self.run_ui(r'''
const card=$('center-content').children[0].children[0],cardPlay=card.children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='播放');let finish,count=0;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/action'){count++;return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>({})}));}return originalFetch(path,options);};
const opening=cardPlay.onclick();card.children[0].onclick();const detailPlay=()=> $('detail-content').children[0].children[1].children.find(n=>n.className==='file').children.find(n=>n.className==='actions').children.find(n=>n.textContent==='播放');assert(detailPlay().disabled);await detailPlay().onclick();assert.equal(count,1);finish();await opening;assert(!detailPlay().disabled);assert(!cardPlay.disabled);assert.equal(operations.length,0);
''')

    def test_photo_detail_uses_full_width_controls_while_movie_keeps_poster_layout(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();assert.equal($('detail-content').children[0].className,'detail-grid');$('detail-close').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();assert.equal($('detail-content').children[0].className,'detail-grid photo-detail');assert($('detail-content').children[0].children[1].children.some(n=>n.className==='actions detail-controls'));assert.equal(operations.length,0);
''')

    def test_tag_save_keeps_text_entered_after_submission(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();
const info=$('detail-content').children[0].children[1],controls=info.children.find(n=>n.className==='actions detail-controls'),tags=controls.children[0];
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const body=options?.body&&JSON.parse(options.body);if(body?.action==='labels')return new Promise(resolve=>finish=()=>{movie.local_tags=movie.tags=body.tags;resolve({ok:true,json:async()=>state});});return originalFetch(path,options);};
tags.value='Saved tag';const saving=controls.children[1].onclick();assert(controls.children[1].disabled);tags.value='Saved tag, New unsaved tag';finish();await saving;
assert(!controls.children[1].disabled);assert.equal(tags.value,'Saved tag, New unsaved tag');assert.deepEqual(movie.local_tags,['Saved tag']);assert.equal($('detail-content').children[0].children[1],info);assert($('detail-feedback').textContent.includes('尚未保存'));assert.equal(operations.length,0);
''')

    def test_newer_manual_refresh_supersedes_save_refresh_without_false_error(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const controls=$('detail-content').children[0].children[1].children.find(n=>n.className==='actions detail-controls');let saved=false,finish,delayed=false;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='labels'){saved=true;movie.local_tags=movie.tags=JSON.parse(options.body).tags;}const response=await originalFetch(path,options);if(path==='api/center'&&!options?.body&&saved&&!delayed){delayed=true;return new Promise(resolve=>finish=()=>resolve(response));}return response;};controls.children[0].value='Saved';const saving=controls.children[1].onclick();await new Promise(r=>setImmediate(r));assert(finish);await $('center-refresh').onclick();finish();await saving;assert.equal($('center-error').textContent,'');assert.equal($('detail-status').textContent,'');assert.equal(controls.children[0].value,'Saved');assert.equal(operations.length,0);
''')

    def test_successful_tag_write_with_failed_refresh_keeps_input_and_explains_result(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],controls=info.children.find(n=>n.className==='actions detail-controls'),tags=controls.children[0];tags.value='Submitted tag';const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const response=await originalFetch(path,options);if(options?.body&&JSON.parse(options.body).action==='labels')failCenter=true;return response;};await controls.children[1].onclick();assert.equal(tags.value,'Submitted tag');assert.equal($('detail-feedback').textContent,'');assert($('detail-status').textContent.includes('已保存'));assert($('detail-status').textContent.includes('刷新失败'));assert.equal(operations.length,0);
''')

    def test_successful_note_write_with_failed_refresh_does_not_claim_fully_refreshed(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],note=info.children.find(n=>n.tagName==='TEXTAREA');note.value='Submitted note';const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{const response=await originalFetch(path,options);if(path==='api/notes')failCenter=true;return response;};await info.children.find(n=>n.textContent==='保存评分与备注').onclick();assert.equal(note.value,'Submitted note');assert.equal($('detail-feedback').textContent,'');assert($('detail-status').textContent.includes('刷新失败'));assert.equal(operations.length,0);
''')

    def test_save_then_next_photo_never_jumps_back_or_shows_previous_feedback(self):
        self.run_ui(r'''
const second={...photo,id:'f'.repeat(64),title:'Second',mtime:1,files:[{...photo.files[0],id:'9'.repeat(64),path:'/second.jpg'}]};items=[photo,second];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='labels')return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>state}));return originalFetch(path,options);};
const controls=$('detail-content').children[0].children[1].children.find(n=>n.className==='actions detail-controls');controls.children[0].value='First tag';const saving=controls.children[1].onclick();$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();const nextGrid=$('detail-content').children[0];finish();await saving;
assert.equal($('detail-title').textContent,'Second');assert.equal($('detail-content').children[0],nextGrid);assert.equal($('detail-feedback').textContent,'');assert.equal($('detail-status').textContent,'');assert.equal(operations.length,0);
''')

    def test_favorite_response_after_close_and_reopen_same_media_does_not_claim_new_action(self):
        self.run_ui(r'''
const cover=$('center-content').children[0].children[0].children[0];cover.onclick();let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='favorite')return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>state}));return originalFetch(path,options);};
const saving=$('detail-actions').children[0].onclick();$('detail-close').onclick();cover.onclick();const newGrid=$('detail-content').children[0];finish();await saving;assert($('media-detail').open);assert.equal($('detail-content').children[0],newGrid);assert.equal($('detail-feedback').textContent,'');assert.equal(operations.length,0);
''')

    def test_late_save_and_player_errors_do_not_pollute_another_detail(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],file=info.children.find(n=>n.className==='file');file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='页内预览').onclick();const player=file.children.find(n=>n.tagName==='VIDEO');
let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='favorite')return new Promise(resolve=>finish=()=>resolve({ok:false,json:async()=>({error:'Old movie save failed'})}));return originalFetch(path,options);};
const saving=$('detail-actions').children[0].onclick();$('detail-close').onclick();location.hash='#photos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();player.onerror();finish();await saving;assert.equal($('detail-title').textContent,'Photo');assert.equal($('detail-status').textContent,'');assert.equal($('detail-feedback').textContent,'');assert.equal(operations.length,0);
''')

    def test_manage_delete_controls_recover_after_favorite_without_an_extra_refresh(self):
        self.run_ui(r'''
$('manage-toggle').onclick();const currentCard=()=>$('center-content').children[0].children[0],actions=()=>currentCard().children.find(n=>n.className==='card-actions');assert(!actions().children.find(n=>n.textContent==='删除…').disabled);
await actions().children.find(n=>n.textContent==='☆ 收藏').onclick();const trash=actions().children.find(n=>n.textContent==='删除…');assert(!trash.disabled);trash.onclick();assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');
''')

    def test_detail_delete_control_recovers_after_rating_save_and_keeps_player(self):
        self.run_ui(r'''
$('manage-toggle').onclick();$('center-content').children[0].children[0].children[0].onclick();const grid=$('detail-content').children[0],info=grid.children[1],file=info.children.find(n=>n.className==='file'),trash=file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='删除此文件…');
await info.children.find(n=>n.textContent==='保存评分与备注').onclick();assert.equal($('detail-content').children[0],grid);assert(!trash.disabled);assert.equal(operations.length,0);
''')

    def test_old_collection_save_does_not_close_reopened_form(self):
        self.run_ui(r'''
location.hash='#collections';events.hashchange();$('create-collection').onclick();$('collection-name').value='First collection';let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='create')return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>state}));return originalFetch(path,options);};
const saving=$('collection-save').onsubmit({preventDefault(){}});$('collection-cancel').onclick();$('create-collection').onclick();$('collection-name').value='New form draft';finish();await saving;assert($('collection-form').open);assert.equal($('collection-name').value,'New form draft');assert.equal($('collection-error').textContent,'');assert.equal(operations.length,0);
''')

    def test_select_page_fills_remaining_capacity_from_unselected_items(self):
        self.run_ui(r'''
items=Array.from({length:201},(_,index)=>({...photo,id:(index+1).toString(16).padStart(64,'0'),title:'Photo '+index,mtime:201-index,files:[{...photo.files[0],id:(index+1).toString(16).padStart(64,'0'),path:'/generated/'+index+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.files[0].id]));await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('manage-toggle').onclick();
for(let page=0;page<5;page++){if(page)$('center-next').onclick();$('select-page').onclick();}assert($('selected-count').textContent.includes('200'));for(let page=0;page<4;page++)$('center-prev').onclick();const check=$('center-content').children[0].children[1].children.find(n=>n.className==='pick').children[0];check.checked=false;check.onchange();assert($('selected-count').textContent.includes('199'));$('select-page').onclick();assert($('selected-count').textContent.includes('200'));assert($('center-content').children[0].children[1].children.find(n=>n.className==='pick').children[0].checked);assert.equal(operations.length,0);
''')

    def test_sort_and_photo_layout_preserve_selection_but_filter_changes_clear_it(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('1'));
$('media-order').value='name';$('media-order').onchange();assert($('selected-count').textContent.includes('1'));
$('photo-layout').value='timeline';$('photo-layout').onchange();assert($('selected-count').textContent.includes('1'));
$('delete-selected').onclick();assert.deepEqual(Array.from(operations[0].ids),[photo.files[0].id]);
$('filter-year').value='2020';$('filter-year').onchange();assert($('selected-count').textContent.includes('0'));assert($('delete-selected').disabled);
''')

    def test_unfavorited_item_is_removed_from_batch_selection(self):
        self.run_ui(r'''
state.favorites[movie.id]=state.favorites[photo.id]=true;await $('center-refresh').onclick();location.hash='#favorites';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('2'));
const card=$('center-content').children[0].children.find(n=>n.children.some(c=>c.tagName==='H3'&&c.textContent==='Film'));
await card.children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='★ 已收藏').onclick();assert($('selected-count').textContent.includes('1'));$('delete-selected').onclick();assert.deepEqual(Array.from(operations[0].ids),[photo.files[0].id]);
''')

    def test_refresh_prunes_members_removed_from_collection_selection(self):
        self.run_ui(r'''
state.collections.sample={name:'Sample',ids:[movie.id,photo.id]};location.hash='#collections';events.hashchange();$('center-content').children[0].children.find(n=>n.children.some(c=>c.tagName==='STRONG'&&c.textContent==='Sample')).onclick();$('manage-toggle').onclick();$('select-page').onclick();assert($('selected-count').textContent.includes('2'));
state.collections.sample.ids=[movie.id];await $('center-refresh').onclick();assert($('selected-count').textContent.includes('1'));$('delete-selected').onclick();assert.deepEqual(Array.from(operations[0].ids),[movie.files[0].id]);
''')

    def test_review_refresh_removes_selection_from_disappeared_groups(self):
        self.run_ui(r'''
const f={...photo.files[0],kind:'photo',media_kind:'photo',owner:photo.id,metadata:photo.metadata};review.similar=[{id:'g1',files:[f,{...f,id:movie.files[0].id,path:movie.files[0].path}]}];await $('center-refresh').onclick();location.hash='#review';events.hashchange();$('center-content').children[0].children[1].onclick();
const section=$('center-content').children.find(n=>n.className==='review-group'),check=section.children.find(n=>n.className==='review-files').children[0].children[0].children[0];check.checked=true;check.onchange();
review.similar=[];await $('center-refresh').onclick();const batch=$('center-content').children.find(n=>n.className==='actions review-batch');assert(batch.children[0].textContent.includes('0'));assert(batch.children.find(n=>n.textContent==='删除所选…').disabled);assert.equal(operations.length,0);
''')

    def test_similarity_confirmation_keeps_selection_in_other_groups_and_restores_batch_button(self):
        self.run_ui(r'''
const second={...photo,id:'6'.repeat(64),files:[{...photo.files[0],id:'5'.repeat(64),name:'Second.jpg',path:'/generated/second.jpg'}]};items.push(second);access.ids_by_path[second.files[0].path]=second.files[0].id;const f={...photo.files[0],kind:'photo',media_kind:'photo',owner:photo.id,metadata:photo.metadata},m={...second.files[0],kind:'photo',media_kind:'photo',owner:second.id,metadata:second.metadata};const extra={...photo,id:'8'.repeat(64),files:[{...photo.files[0],id:'7'.repeat(64),path:'/generated/extra.jpg'}]};items.push(extra);access.ids_by_path[extra.files[0].path]=extra.files[0].id;const e={...extra.files[0],kind:'photo',media_kind:'photo',owner:extra.id,metadata:extra.metadata};review.similar=[{id:'g1',files:[f,e]},{id:'g2',files:[m,e]}];await $('center-refresh').onclick();location.hash='#review';events.hashchange();$('center-content').children[0].children[1].onclick();
let sections=()=>$('center-content').children.filter(n=>n.className==='review-group');const containsSecond=section=>section.children.find(n=>n.className==='review-files').children.some(card=>card.children.some(n=>n.tagName==='H3'&&n.textContent==='Second.jpg'));const check=sections().find(containsSecond).children.find(n=>n.className==='review-files').children[0].children[0].children[0];check.checked=true;check.onchange();
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='review_similarity')review.similar=review.similar.filter(g=>g.id!=='g1');return originalFetch(path,options);};
await sections().find(section=>!containsSecond(section)).children.find(n=>n.className==='actions review-group-actions').children.find(n=>n.textContent==='确认不相似').onclick();const batch=$('center-content').children.find(n=>n.className==='actions review-batch');assert(batch.children[0].textContent.includes('1'));const trash=batch.children.find(n=>n.textContent==='删除所选…');assert(!trash.disabled);trash.onclick();assert.deepEqual(Array.from(operations[0].ids),[second.files[0].id]);
''')

    def test_review_batch_bar_only_occupies_space_while_files_are_selected(self):
        self.run_ui(r'''
const f={...photo.files[0],kind:'photo',media_kind:'photo',owner:photo.id,metadata:photo.metadata};review.exact=[{files:[f,{...f,id:movie.files[0].id,path:movie.files[0].path}]}];await $('center-refresh').onclick();location.hash='#review';events.hashchange();
const batch=()=>$('center-content').children.find(n=>n.className==='actions review-batch');assert(!batch().hidden);assert(batch().children[0].textContent.includes('1'));batch().children.find(n=>n.textContent==='清空选择').onclick();assert(batch().hidden);
const check=$('center-content').children.find(n=>n.className==='review-group').children.find(n=>n.className==='review-files').children[0].children[0].children[0];check.checked=true;check.onchange();assert(!batch().hidden);assert(batch().children[0].textContent.includes('1'));batch().children.find(n=>n.textContent==='清空选择').onclick();assert(batch().hidden);assert.equal(operations.length,0);
''')

    def test_photo_page_and_photo_only_search_hide_and_clear_video_filters(self):
        self.run_ui(r'''
$('filter-duration').value='short';$('filter-resolution').value='hd';location.hash='#photos';events.hashchange();assert($('filter-duration').hidden);assert($('filter-resolution').hidden);assert.equal($('filter-duration').value,'');assert($('center-content').children.length);
$('search-scope').value='all';$('query').value='Photo';$('media-kind').value='photo';$('center-search').onsubmit({preventDefault(){}});assert($('filter-duration').hidden);
location.hash='#videos';events.hashchange();assert(!$('filter-duration').hidden);assert(!$('filter-resolution').hidden);assert.equal(operations.length,0);
''')

    def test_video_card_delete_is_available_without_entering_manage_mode(self):
        self.run_ui(r'''
movie.kind='video';await $('center-refresh').onclick();location.hash='#videos';events.hashchange();assert($('manage-bar').hidden);const actions=$('center-content').children[0].children[0].children.find(n=>n.className==='card-actions');const trash=actions.children.find(n=>n.textContent==='删除…');assert(trash&&!trash.disabled);trash.onclick();assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');assert.deepEqual(Array.from(operations[0].ids),[movie.files[0].id]);assert.equal(calls.length,0);
''')

    def test_month_view_honors_name_and_size_sort_inside_months_with_unknown_dates_last(self):
        self.run_ui(r'''
const b=require(process.argv[1]),sample=(id,month,title,bytes,mtime)=>({...photo,id,title,bytes,mtime,metadata:{timeline_month:month}}),rows=[sample('a','2025-02','Photo10',10,3),sample('b','2025-02','Photo2',20,1),sample('c','2025-01','Before',30,2),sample('d','','Unknown',40,0)];const before=JSON.stringify(rows);
assert.deepEqual(b.centerTimelineOrder(b.centerFilter(rows,{order:'name'}),'name').map(n=>n.id),['b','a','c','d']);assert.deepEqual(b.centerTimelineOrder(b.centerFilter(rows,{order:'size'}),'size').map(n=>n.id),['b','a','c','d']);assert.deepEqual(b.centerTimelineOrder(b.centerFilter(rows,{order:'oldest'}),'oldest').map(n=>n.id),['c','b','a','d']);assert.equal(JSON.stringify(rows),before);assert.equal(operations.length,0);
''')

    def test_oldest_month_view_and_continuous_photo_navigation_use_the_same_order(self):
        self.run_ui(r'''
items=[{...photo,title:'Newer',metadata:{timeline_month:'2025-02'},mtime:1},{...photo,id:'8'.repeat(64),title:'Earlier',metadata:{timeline_month:'2025-01'},mtime:10,files:[{...photo.files[0],id:'7'.repeat(64),path:'/earlier.jpg'}]},{...photo,id:'6'.repeat(64),title:'Unknown',metadata:{},mtime:0}];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('photo-layout').value='timeline';$('photo-layout').onchange();$('media-order').value='oldest';$('media-order').onchange();assert.deepEqual($('center-content').children.filter(n=>n.tagName==='H2').map(n=>n.textContent),['2025-01','2025-02','日期未知']);assert($('center-subtitle').textContent.includes('月份从旧到新'));
$('center-content').children.find(n=>n.className==='wall photo-wall').children[0].children[0].onclick();assert.equal($('detail-title').textContent,'Earlier');$('detail-actions').children.find(n=>n.textContent==='下一张').onclick();assert.equal($('detail-title').textContent,'Newer');assert.equal(operations.length,0);
''')

    def test_month_view_name_sort_is_visible_in_cards_and_caption(self):
        self.run_ui(r'''
items=[{...photo,title:'Photo10',mtime:10,metadata:{timeline_month:'2025-02'}},{...photo,id:'8'.repeat(64),title:'Photo2',mtime:1,metadata:{timeline_month:'2025-02'},files:[{...photo.files[0],id:'7'.repeat(64),path:'/photo2.jpg'}]}];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();$('photo-layout').value='timeline';$('photo-layout').onchange();$('media-order').value='name';$('media-order').onchange();const cards=$('center-content').children.find(n=>n.className==='wall photo-wall').children;assert.deepEqual(cards.map(card=>card.children.find(n=>n.tagName==='H3').textContent),['Photo2','Photo10']);assert($('center-subtitle').textContent.includes('月内按名称'));assert.equal(operations.length,0);
''')

    def test_video_detail_delete_opens_one_confirmation_without_manage_mode(self):
        self.run_ui(r'''
movie.kind='video';await $('center-refresh').onclick();location.hash='#videos';events.hashchange();$('center-content').children[0].children[0].children[0].onclick();const info=$('detail-content').children[0].children[1],row=info.children.find(n=>n.className==='file'),trash=row.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='删除此文件…');assert(trash&&!trash.disabled);trash.onclick();assert(!$('media-detail').open);assert.equal(operations.length,1);assert.equal(operations[0].action,'delete');assert.deepEqual(Array.from(operations[0].ids),[movie.files[0].id]);assert.equal(calls.length,0);
''')

    def test_late_collection_delete_does_not_exit_a_different_album(self):
        self.run_ui(r'''
state.collections.sample={name:'Sample',ids:[movie.id]};folders['/Other Album']=[photo.id];await $('center-refresh').onclick();location.hash='#collections';events.hashchange();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='Sample')).onclick();let finish;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='delete_collection')return new Promise(resolve=>finish=()=>{delete state.collections.sample;resolve({ok:true,json:async()=>state});});return originalFetch(path,options);};
const deleting=$('center-breadcrumb').children.find(n=>n.textContent==='删除合集记录').onclick();$('center-breadcrumb').children.find(n=>n.textContent==='← 返回相册与合集').onclick();$('center-content').children[0].children.find(card=>card.children.some(n=>n.className==='muted album-path'&&n.textContent==='/Other Album')).onclick();assert.equal($('center-title').textContent,'Other Album');finish();await deleting;assert.equal($('center-title').textContent,'Other Album');assert($('center-content').children[0].children.some(card=>card.children.some(n=>n.textContent==='Photo')));assert.equal(operations.length,0);
''')

    def test_collection_delete_returns_to_collection_list_when_still_in_deleted_collection(self):
        self.run_ui(r'''
state.collections.sample={name:'Sample',ids:[movie.id]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='Sample')).onclick();const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='delete_collection')delete state.collections.sample;return originalFetch(path,options);};await $('center-breadcrumb').children.find(n=>n.textContent==='删除合集记录').onclick();assert.equal($('center-title').textContent,'相册与合集');assert.equal(operations.length,0);
''')
