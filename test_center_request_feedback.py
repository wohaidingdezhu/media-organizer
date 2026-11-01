"""Slow refresh and collection saves stay understandable and retryable."""
import unittest
import test_media_center


class CenterRequestFeedbackTests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_refresh_is_single_flight_and_preserves_cross_page_selection(self):
        self.run_ui(r'''
items=Array.from({length:104},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(i=>[i.files[0].path,i.files[0].id]));location.hash='#photos';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();for(let n=0;n<3;n++){$('select-page').onclick();if(n<2)$('center-next').onclick();}assert($('selected-count').textContent.includes('104 项'));
let finish,reads=0;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/center'&&!options?.body){reads++;return new Promise(resolve=>finish=async()=>resolve(await original(path,options)));}return original(path,options);};
const control=$('center-refresh'),first=control.onclick(),second=control.onclick();assert.equal(first,second);assert(control.disabled);assert.equal(control.textContent,'正在刷新…');assert.equal(control.getAttribute('aria-busy'),'true');assert.equal(reads,1);finish();assert.equal(await first,true);assert.equal(await second,true);assert(!control.disabled);assert.equal(control.textContent,'刷新页面');assert.equal(control.getAttribute('aria-busy'),'false');assert($('selected-count').textContent.includes('104 项'));assert($('center-page').textContent.includes('3/3'));assert.equal(operations.length,0);
''')

    def test_failed_refresh_releases_button_and_can_retry_with_selection(self):
        self.run_ui(r'''
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();failCenter=true;assert.equal(await $('center-refresh').onclick(),false);assert(!$('center-refresh').disabled);assert($('center-error').textContent.includes('Disconnected'));assert($('selected-count').textContent.includes('1 项'));assert($('delete-selected').disabled);
failCenter=false;assert.equal(await $('center-refresh').onclick(),true);assert(!$('center-refresh').disabled);assert(!$('center-error').textContent);assert($('selected-count').textContent.includes('1 项'));assert(!$('delete-selected').disabled);assert.equal(operations.length,0);
''')

    def test_missing_file_recheck_is_shared_and_failure_does_not_block_retry(self):
        self.run_ui(r'''
let finish,rechecks=0,missing=true;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/availability/recheck'){rechecks++;return new Promise(resolve=>finish=()=>resolve({ok:false,json:async()=>({error:'Generated offline disk'})}));}const response=await original(path,options);if(path==='api/center'&&!options?.body){const data=await response.json();return {ok:true,json:async()=>({...data,missing_files:missing?1:0})};}return response;};await $('center-refresh').onclick();const first=$('center-refresh').onclick();const second=$('center-refresh').onclick();assert.equal(rechecks,1);assert.equal(first,second);finish();await first;assert($('center-error').textContent.includes('Generated offline disk'));assert(!$('center-refresh').disabled);missing=false;scope.fetch=original;const retry=$('center-refresh').onclick();await retry;assert.equal(calls.filter(call=>call.path==='api/media/availability/recheck').length,1);assert(!$('center-refresh').disabled);assert.equal(operations.length,0);
''')

    def test_collection_save_wait_feedback_blocks_duplicates_and_failure_keeps_selection(self):
        self.run_ui(r'''
state.collections.c1={name:'旅行',ids:[]};await $('center-refresh').onclick();$('manage-toggle').onclick();$('select-page').onclick();$('target-collection').value='c1';let finish,posts=0;const original=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='members'){posts++;return new Promise(resolve=>finish=()=>resolve({ok:false,json:async()=>({error:'Generated save error'})}));}return original(path,options);};
const saving=$('add-collection').onclick();assert.equal($('add-collection').textContent,'正在加入…');assert($('add-collection').disabled);assert($('target-collection').disabled);await $('add-collection').onclick();assert.equal(posts,1);finish();await saving;assert.equal($('add-collection').textContent,'加入合集');assert(!$('add-collection').disabled);assert(!$('target-collection').disabled);assert($('selected-count').textContent.includes('1 项'));assert($('center-error').textContent.includes('Generated save error'));
scope.fetch=original;await $('add-collection').onclick();assert($('center-error').textContent.includes('已将 1 项媒体加入合集“旅行”'));assert.equal($('center-error').getAttribute('role'),'status');assert($('selected-count').textContent.includes('1 项'));assert.equal(operations.length,0);
''')

    def test_remove_from_collection_waits_and_keeps_original_media(self):
        self.run_ui(r'''
state.collections.c1={name:'旅行',ids:[photo.id]};location.hash='#collections';events.hashchange();await $('center-refresh').onclick();$('center-content').children[0].children.find(card=>card.children.some(n=>n.textContent==='旅行')).onclick();$('manage-toggle').onclick();$('select-page').onclick();let finish,posts=0;const original=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='members'){posts++;const body=JSON.parse(options.body);assert.equal(body.remove,true);assert.equal(body.id,'c1');return new Promise(resolve=>finish=()=>{state.collections.c1.ids=[];resolve({ok:true,json:async()=>state});});}return original(path,options);};
const saving=$('remove-collection').onclick();assert.equal($('remove-collection').textContent,'正在移出…');assert($('remove-collection').disabled);await $('remove-collection').onclick();assert.equal(posts,1);finish();await saving;assert.equal($('remove-collection').textContent,'移出合集');assert($('remove-collection').disabled);assert($('selected-count').textContent.includes('0 项'));assert($('center-error').textContent.includes('移出合集“旅行”'));assert.equal(items.length,2);assert.equal(operations.length,0);
''')


if __name__ == '__main__':
    unittest.main()
