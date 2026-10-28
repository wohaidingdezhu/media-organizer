"""Refresh restored entries without disturbing in-progress user selection."""
import unittest
import test_media_center


class MissingRefreshUITests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_manual_refresh_queues_hidden_files_and_defers_update_during_selection(self):
        self.run_ui(r'''
let revision=0,missing=1,rechecks=0;
const base=scope.fetch;
scope.fetch=async(path,options)=>{
  if(path==='api/media/availability/recheck'){rechecks++;return {ok:true,json:async()=>({queued:1})};}
  if(path==='api/media/availability')return {ok:true,json:async()=>({revision,missing_count:missing})};
  const response=await base(path,options);
  if(path==='api/center'&&!options?.body){const data=await response.json();return {ok:true,json:async()=>({...data,missing_files:missing,availability_revision:revision})};}
  return response;
};
await $('center-refresh').onclick();assert.equal(rechecks,0);
location.hash='#photos';events.hashchange();$('manage-toggle').onclick();$('select-page').onclick();
assert($('selected-count').textContent.includes('1'));
await $('center-refresh').onclick();assert.equal(rechecks,1);assert($('selected-count').textContent.includes('1'));
revision=1;missing=0;await scope.poll();assert($('center-status').textContent.includes('保留'));
assert($('selected-count').textContent.includes('1'));
$('manage-toggle').onclick();await scope.poll();
assert(!$('center-status').textContent.includes('暂时隐藏'));assert.equal(operations.length,0);
''', setup='scope.setInterval=fn=>{scope.poll=fn;};')


if __name__ == '__main__':
    unittest.main()
