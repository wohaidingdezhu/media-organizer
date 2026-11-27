"""Pending native folder dialogs must preserve large cross-page media selections."""
import unittest

import test_direct_cleanup


SETUP = r'''
const html=fs.readFileSync(root+'/organization.html','utf8');
const start=html.indexOf('function render(){'),end=html.indexOf('function buildFolders()',start);
scope.filters=[];scope.syncBrowseControls=()=>{};scope.data=null;
vm.runInContext(html.slice(start,end),scope);
vm.runInContext(management.slice(management.indexOf("$('choose-destination').onclick"),management.indexOf('let executionPage=')),scope);
scope.selected=new Set(Array.from({length:200},(_,number)=>number.toString(16).padStart(64,'0')));
const selected=[...scope.selected];
'''


class DestinationPickerTests(unittest.TestCase):
    node = test_direct_cleanup.DirectCleanupTests.node

    def test_pending_dialog_survives_rerender_and_blocks_duplicate_or_file_actions(self):
        self.node(SETUP + r'''
(async()=>{
let finish;scope.api=async(route,body)=>{requests.push({route,body});return new Promise(resolve=>{finish=resolve;});};
$('destination').value='C:\\generated\\old';
const pending=$('choose-destination').onclick();
assert(scope.saving);assert($('choose-destination').disabled);assert($('destination').disabled);
assert($('choose-destination').textContent.includes('正在选择'));
scope.render();assert($('choose-destination').disabled);
await $('choose-destination').onclick();await scope.previewOperation('trash');await scope.previewOperation('copy');
assert.equal(requests.length,1);assert.equal(requests[0].route,'api/operations/destination');
finish({destination:'\\\\generated-server\\媒体\\整理'});await pending;
assert.equal($('destination').value,'\\\\generated-server\\媒体\\整理');
assert(!scope.saving&&!$('choose-destination').disabled&&!$('destination').disabled);
assert.deepEqual([...scope.selected],selected);assert(!$('execution').open);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_cancel_and_failure_preserve_target_and_picks_then_allow_retry(self):
        self.node(SETUP + r'''
(async()=>{
let attempt=0;scope.api=async()=>{attempt++;if(attempt===2)throw Error('目录选择暂不可用');return {destination:attempt===1?'':'/generated/new'};};
$('choose-destination').textContent='选择分类目标…';$('destination').value='/generated/original';
await $('choose-destination').onclick();
assert.equal($('destination').value,'/generated/original');assert(!scope.saving);
await $('choose-destination').onclick();
assert($('error').textContent.includes('目录选择暂不可用'));assert.equal($('destination').value,'/generated/original');
assert(!scope.saving&&!$('choose-destination').disabled);assert.equal($('choose-destination').textContent,'选择分类目标…');
await $('choose-destination').onclick();
assert.equal($('destination').value,'/generated/new');assert($('error').hidden);
assert.deepEqual([...scope.selected],selected);assert.equal(requests.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_late_picker_result_does_not_unlock_an_external_operation(self):
        self.node(SETUP + r'''
(async()=>{
let finish;scope.api=()=>new Promise(resolve=>{finish=resolve;});
const pending=$('choose-destination').onclick();scope.operating=true;scope.render();
finish({destination:'/generated/copies'});await pending;
assert(!scope.saving&&scope.operating);assert($('destination').disabled&&$('choose-destination').disabled);
assert.deepEqual([...scope.selected],selected);assert.equal(requests.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
