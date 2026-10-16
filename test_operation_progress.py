"""Exercise the shipped progress UI with generated 10,000-item job records."""
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r'''
const fs=require('fs'),vm=require('vm');
let created=0,moves=0,stopped=null,reviewed=null;
class E{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.attrs={};created++;}
  append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
  replaceChildren(...children){for(const child of this.children)child.parent=null;this.children=[];this.append(...children);}
  setAttribute(key,value){this.attrs[key]=value;}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;moves++;}}
  insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,child);child.parent=this;moves++;}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new E('div'));return nodes.get(id);};
function all(root){return [root,...root.children.flatMap(all)];}
function text(root){return root.hidden?'':[root.textContent,...root.children.map(text)].join(' ');}
function control(root,label){return all(root).find(node=>node.textContent===label);}
function check(value,message){if(!value)throw Error(message);}
const scope={$,make:(tag,cls,content)=>new E(tag,content||''),fileSize:String,operating:false,saving:false,stopOperation:async(id)=>{stopped=id;},reviewRemaining:async(id)=>{reviewed=id;}};
vm.createContext(scope);const source=fs.readFileSync(process.argv[1],'utf8');vm.runInContext(source.slice(source.indexOf('function activeOperation'),source.indexOf('async function reviewRemaining')),scope);
const planned=Array.from({length:10000},(_,i)=>({path:'C:\\样例\\source-'+String(i).padStart(5,'0')+'.jpg',target:'C:\\副本\\photo-'+i+'.jpg',bytes:100,role:'照片'}));
function job(overrides={}){return {id:'a'.repeat(24),mode:'copy',quick:true,queue:true,batch_count:50,completed_batches:0,status:'running',total:planned.length,planned_items:planned,items:[],...overrides};}
'''


class OperationProgressTests(unittest.TestCase):
    def run_ui(self, script):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'UI behavior tests require the declared Node test runtime')
        result = subprocess.run([node, '-e', HARNESS + script, str(Path(__file__).parent/'management.js')],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_ten_thousand_items_are_reachable_without_creating_ten_thousand_rows(self):
        self.run_ui(r'''
const record=job(),card=scope.operationCard(record);
check(created<150,'large pending queue eagerly rendered');
check(all(card).filter(e=>e.tag==='pre'&&!e.hidden).length===50,'page must contain 50 file rows');
check(text(card).includes('未开始 10000'),'pending count');
const next=control(card,'下一页明细');for(let page=1;page<200;page++)next.onclick();
check(text(card).includes('source-09999.jpg'),'last pending item unreachable');check(next.disabled,'last page unbounded');
const search=all(card).find(e=>e.tag==='input');search.value='source-07500';search.oninput();
check(text(card).includes('明细 1/10000 项'),'search must include off-page records');
check(text(card).includes('source-07500.jpg'),'search result missing');
const completed=job({status:'complete',items:planned.map(item=>({...item,status:'success'})),completed_batches:50});
const same=scope.operationCard(completed);check(same===card,'card identity lost');check(search.value==='source-07500','search lost during refresh');
check(text(card).includes('成功 10000'),'completed count');check(!text(card).includes('安全停止整个整理任务'),'completed task has stop action');
search.value='';search.oninput();for(let page=1;page<200;page++)next.onclick();
check(text(card).includes('photo-9999.jpg'),'last successful result unreachable');
''')

    def test_current_transfer_and_errors_remain_visible_with_filtered_pages_and_interruption(self):
        self.run_ui(r'''
const record=job({items:[...planned.slice(0,125).map(item=>({...item,status:'success'})),{...planned[125],status:'processing',phase:'verifying',processed_bytes:40}]});
const card=scope.operationCard(record),filter=all(card).find(e=>e.tag==='select');filter.value='success';filter.onchange();control(card,'下一页明细').onclick();
check(text(card).includes('正在校验副本（40 / 100）'),'current transfer hidden behind success filter');check(text(card).includes('第 2/3 页'),'success pagination');
scope.operationCard({...record,items:[...record.items.slice(0,125),{...record.items[125],processed_bytes:80}]});
check(text(card).includes('正在校验副本（80 / 100）')&&text(card).includes('第 2/3 页'),'refresh lost page or current byte progress');
const interrupted={...record,status:'interrupted',error:'服务中断，请核对'};scope.operationCard(interrupted);
check(text(card).includes('需核对 1'),'interrupted processing was treated as success');
control(card,'查看需核对的 1 项').onclick();check(text(card).includes('source-00125.jpg')&&text(card).includes('处理结果待确认'),'interrupted item inaccessible');
check(!text(card).includes('正在校验'),'interrupted job claims active transfer');
scope.operationCard({...record,status:'stopped',items:[...record.items.slice(0,125),{...planned[125],status:'failed',error:'磁盘断开'}]});
check(text(card).includes('磁盘断开'),'failure hidden after polling');check(text(card).includes('整组未全部完成'),'bundle warning lost');
check(!text(card).includes('核对此批次未处理项'),'quick queue must not offer unsafe retry');
''')

    def test_polling_keeps_controls_and_page_attached_and_prunes_old_views(self):
        self.run_ui(r'''
const record=job();scope.renderOperationCards({jobs:[record],warnings:[]});const parent=$('operations'),card=parent.children[0],search=all(card).find(e=>e.tag==='input');
control(card,'下一页明细').onclick();const before=moves;created=0;
scope.renderOperationCards({jobs:[record],warnings:[]});
check(moves===before,'polling detached a focused control');check(parent.children[0]===card&&all(card).includes(search),'polling replaced controls');
check(created===50&&text(card).includes('第 2/200 页'),'polling should only replace visible detail rows');
scope.renderOperationCards({jobs:[record],warnings:['损坏记录请核对']});check(text(parent).includes('损坏记录请核对'),'history warning missing');
scope.renderOperationCards({jobs:[],warnings:[]});check(text(parent).includes('还没有文件操作记录'),'empty history');
check(vm.runInContext('operationViews.size',scope)===0,'detached histories retained');
''')

    def test_stop_targets_whole_queue_and_legacy_history_keeps_unknown_outcomes(self):
        self.run_ui(r'''
(async()=>{
const record=job(),card=scope.operationCard(record);await control(card,'安全停止整个整理任务').onclick();check(stopped===record.id,'wrong stop target');
scope.operationCard({...record,stop_requested:true});check(control(card,'正在安全停止…').disabled,'stop request not reflected');
const old=job({id:'b'.repeat(24),quick:false,queue:false,status:'interrupted',total:3,planned_items:undefined,items:[{...planned[0],status:'success'}]});
const legacy=scope.operationCard(old);check(text(legacy).includes('还有 2 项未记录处理结果'),'legacy missing list hidden');check(!text(legacy).includes('未开始 2'),'invented safe pending files');
const normal=job({id:'c'.repeat(24),quick:false,queue:false,status:'cancelled',total:2,planned_items:planned.slice(0,2),items:[{...planned[0],status:'cancelled'}]});
const ordinary=scope.operationCard(normal);await control(ordinary,'核对此批次未处理项').onclick();check(reviewed===normal.id,'existing ordinary review action broken');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
