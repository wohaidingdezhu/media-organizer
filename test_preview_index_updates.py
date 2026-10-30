"""Preview completion touches current matching records, not the entire library."""
import unittest
import test_media_center


class PreviewIndexUpdateTests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_large_library_repair_does_not_search_unrelated_files(self):
        self.run_ui(r'''
items=Array.from({length:14000},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Generated '+n,preview:'',files:[{...photo.files[0],id:(n+20000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.files[0].id]));
let current;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>({preview:'covers/generated.png'})};}const response=await original(path,options);if(path==='api/center'&&!options?.body){current=await response.json();return {ok:true,json:async()=>current};}return response;};
location.hash='#photos';events.hashchange();await $('center-refresh').onclick();let searches=0;for(const item of current.items)item.files.some=()=>{searches++;throw Error('Unexpected full catalog search');};
const card=$('center-content').children[0].children[0],id=card.getAttribute('data-media-id'),repair=card.children.find(n=>n.className==='card-actions').children[0];await repair.onclick();
assert.equal(searches,0);assert.equal(calls.length,1);assert.equal(current.items.find(item=>item.id===id).preview,'covers/generated.png');assert.equal(current.items.filter(item=>item.preview).length,1);assert.equal(card.children[0].children[0].tagName,'IMG');assert(repair.hidden);assert.equal(operations.length,0);
''')

    def test_repair_updates_all_matching_review_records_and_movie_versions(self):
        self.run_ui(r'''
movie.preview='';const extra={...movie.files[0],id:'8'.repeat(64),path:'/second.mp4'};movie.files.push(extra);access.ids_by_path[extra.path]=extra.id;
const file={...movie.files[0],preview:'',preview_status:'Missing'},other={...extra,preview:'covers/other.png'};
review={exact:[{id:'exact',files:[{...file},{...other}]}],similar:[{id:'pair',files:[{...file},{...other}]}],dismissed_similar:[{id:'dismissed',files:[{...file},{...other}]}]};
let current;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover')return {ok:true,json:async()=>({preview:'covers/generated.png'})};const response=await original(path,options);if(path==='api/center'&&!options?.body){current=await response.json();return {ok:true,json:async()=>current};}return response;};
await $('center-refresh').onclick();const card=$('center-content').children[0].children[0];await card.children.find(n=>n.className==='card-actions').children[0].onclick();assert.equal(current.items[0].preview,'covers/generated.png');
for(const key of ['exact','similar','dismissed_similar']){assert.equal(review[key][0].files[0].preview,'covers/generated.png');assert.equal(review[key][0].files[0].preview_status,undefined);assert.equal(review[key][0].files[1].preview,'covers/other.png');}assert.equal(operations.length,0);
''')

    def test_refresh_during_repair_uses_new_catalog_and_keeps_queue_shared(self):
        self.run_ui(r'''
photo.preview='';items=[photo];let current,finish;const original=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>({preview:'covers/generated.png'})}));}const response=await original(path,options);if(path==='api/center'&&!options?.body){current=await response.json();return {ok:true,json:async()=>current};}return response;};
location.hash='#photos';events.hashchange();await $('center-refresh').onclick();const old=current,card=$('center-content').children[0].children[0];const waiting=card.children.find(n=>n.className==='card-actions').children[0].onclick();card.children[0].isConnected=false;
await $('center-refresh').onclick();const fresh=$('center-content').children[0].children[0],again=fresh.children.find(n=>n.className==='card-actions').children[0].onclick();assert.equal(calls.length,1);finish();await waiting;await again;
assert.notEqual(current,old);assert.equal(old.items[0].preview,'');assert.equal(current.items[0].preview,'covers/generated.png');assert.equal(fresh.children[0].children[0].src,'covers/generated.png');assert.equal($('preview-progress').value,100);assert.equal(operations.length,0);
''')

    def test_refresh_keeps_pending_detail_preview_observed_and_draft_intact(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.active=new Set();observers.push(this);}observe(node){this.active.add(node);}unobserve(node){this.active.delete(node);}disconnect(){this.active.clear();}};
movie.preview='';const original=scope.fetch;scope.fetch=async(path,options)=>path==='api/media/cover'?{ok:true,json:async()=>({preview:'covers/detail.png'})}:original(path,options);
await $('center-refresh').onclick();$('center-content').children[0].children[0].children[0].onclick();const grid=$('detail-content').children[0],art=grid.children[0],tags=grid.children[1].children.find(n=>n.className==='actions detail-controls').children[0],observer=observers[0];tags.value='Keep draft';assert(observer.active.has(art));
await $('center-refresh').onclick();assert.equal($('detail-content').children[0],grid);assert(observer.active.has(art));assert.equal(tags.value,'Keep draft');observer.callback([{target:art,isIntersecting:true}]);await new Promise(resolve=>setImmediate(resolve));assert.equal(art.children[0].src,'covers/detail.png');assert(!observer.active.has(art));assert.equal(tags.value,'Keep draft');assert.equal(operations.length,0);
''')


if __name__ == '__main__':
    unittest.main()
