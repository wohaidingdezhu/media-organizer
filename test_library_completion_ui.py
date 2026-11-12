"""Batch polling preserves the gallery, selected items and in-progress editing."""
import unittest
import test_media_center


class LibraryCompletionUITests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_resume_request_expires_when_user_leaves_recovery_controls(self):
        self.run_ui(r'''
operationState.unfinished=[{id:'b'.repeat(24),status:'cancelled',completed:1,total:2,created_at:'generated',can_resume:true}];
await $('center-refresh').onclick();let finish,wasCurrent;
scope.reportResumeTrash=async(id,load,status,beforeOpen,isCurrent)=>{await new Promise(resolve=>finish=resolve);wasCurrent=isCurrent();};
const row=$('center-content').children.find(n=>n.className==='actions batch-recovery'),resume=row.children[2];resume.isConnected=true;
const pending=resume.onclick({currentTarget:resume});assert(resume.disabled);resume.isConnected=false;location.hash='#photos';events.hashchange();finish();await pending;
assert.equal(wasCurrent,false);assert.equal(operations.length,0);
''')

    def test_interrupted_history_is_accessible_and_only_safe_batches_offer_resume(self):
        self.run_ui(r'''
operationState.unfinished=[{id:'a'.repeat(24),status:'interrupted',completed:1,total:200,created_at:'generated',can_resume:false},{id:'b'.repeat(24),status:'cancelled',completed:96,total:200,created_at:'generated',can_resume:true}];
await $('center-refresh').onclick();let watched=[],resumes=0,finish;
scope.reportTrashProgress=id=>watched.push(id);scope.reportResumeTrash=async(id,load,status)=>{resumes++;await new Promise(resolve=>finish=resolve);status('Generated offline disk');};
const rows=$('center-content').children.filter(n=>n.className==='actions batch-recovery');assert.equal(rows.length,2);assert(rows[0].children[0].textContent.includes('1/200'));assert.equal(rows[0].children.length,2);rows[0].children[1].onclick();assert.deepEqual(watched,['a'.repeat(24)]);
const resume=rows[1].children[2],pending=resume.onclick({currentTarget:resume});assert(resume.disabled);await resume.onclick({currentTarget:resume});assert.equal(resumes,1);finish();await pending;assert(!resume.disabled);assert($('center-error').textContent.includes('Generated offline disk'));assert.equal(operations.length,0);
''')

    def test_running_batch_polls_small_status_without_rebuilding_large_gallery(self):
        self.run_ui(r'''
items=Array.from({length:10000},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),files:[{...photo.files[0],id:(n+20000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));operationState.running=true;await $('center-refresh').onclick();
const rows=$('center-content').children.slice(),fetch=scope.fetch;let catalogs=0,polls=0;scope.fetch=async(path,options)=>{if(path==='api/center')catalogs++;if(path==='api/operations')polls++;return fetch(path,options);};
for(let n=0;n<5;n++)await pendingRefresh();assert.equal(polls,5);assert.equal(catalogs,0);assert.deepEqual($('center-content').children,rows);assert.equal(scope.window.scrollY,350);
operationState.running=false;await pendingRefresh();assert.equal(catalogs,1);assert.equal(polls,6);assert(!truthyStatus());function truthyStatus(){return $('center-status').textContent.includes('文件操作正在进行');}
''')

    def test_finish_during_detail_edit_waits_and_failed_refresh_can_recover(self):
        self.run_ui(r'''
operationState.running=true;await $('center-refresh').onclick();$('center-content').children[0].children[0].children[0].onclick();
const content=$('detail-content').children[0],tags=content.children[1].children.find(n=>n.className==='actions detail-controls').children[0];tags.value='Unsaved draft';
operationState.running=false;const fetch=scope.fetch;let catalogs=0;scope.fetch=async(path,options)=>{if(path==='api/center')catalogs++;return fetch(path,options);};
await pendingRefresh();assert.equal(catalogs,0);assert.equal($('detail-content').children[0],content);assert.equal(tags.value,'Unsaved draft');assert($('center-status').textContent.includes('当前选择与编辑已保留'));assert(!$('center-status').textContent.includes('文件操作正在进行'));
$('media-detail').close();failCenter=true;await pendingRefresh();assert.equal(catalogs,1);failCenter=false;await pendingRefresh();assert.equal(catalogs,2);assert(!$('center-status').textContent.includes('批次状态有待刷新'));
''')


if __name__ == '__main__':
    unittest.main()
