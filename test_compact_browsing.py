# coding: utf-8
"""Compact controls preserve selection, active filters and all similarity candidates."""
from pathlib import Path
import subprocess
import shutil
import unittest

from test_direct_cleanup import HARNESS


class CompactBrowsingTests(unittest.TestCase):
    def node(self, code):
        node=shutil.which('node')
        self.assertIsNotNone(node, 'Both platform CI jobs install Node for UI behavior tests')
        result=subprocess.run([node,'-e',HARNESS+code,str(Path(__file__).parent)],text=True,encoding='utf-8',capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_sidebar_switches_library_scan_and_settings_without_file_operations(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),start=html.indexOf('function showPane('),end=html.indexOf('function formatTime(',start);
E.prototype.removeAttribute=function(key){delete this.attrs[key];};scope.document={querySelectorAll:()=>[$('movie-tab'),$('photo-tab')]};vm.runInContext(html.slice(start,end),scope);
for(const pane of ['scan','settings','catalog','results']){scope.showPane(pane);assert.equal($('platform-main').getAttribute('data-pane'),pane);for(const other of ['scan','settings','catalog','results']){assert.equal($(other==='results'?'result-pane':other+'-pane').hidden,pane!==other);assert.equal($('nav-'+other).getAttribute('aria-current'),pane===other?'page':undefined);}}
assert.equal(requests.length,0);
''')

    def test_batch_controls_only_appear_after_selection_and_video_or_busy_cannot_compare(self):
        self.node(r'''
const html=fs.readFileSync(root+'/organization.html','utf8'),start=html.indexOf('function updateSelection()'),end=html.indexOf('function matchesRisk(',start);
Object.assign(scope,{pageItems:[a,b],wallUsers:{update(){}},updateBasketControls(){}});vm.runInContext(html.slice(start,end),scope);
scope.selected.clear();scope.updateSelection();assert($('trash-files').hidden);assert($('compare').hidden);assert($('clear').hidden);assert(!$('select-all-current').disabled);
scope.selected.add(a.id);scope.updateSelection();assert(!$('trash-files').hidden);assert(!$('trash-files').disabled);assert($('trash-files').textContent.includes('1'));assert($('compare').disabled);
scope.selected.add(b.id);scope.updateSelection();assert(!$('compare').disabled);assert($('selection').textContent.includes('2'));
scope.operating=true;scope.updateSelection();assert($('compare').disabled);assert($('trash-files').disabled);
scope.operating=false;scope.data.items[1].kind='视频';scope.updateSelection();assert($('compare').disabled);assert.equal(scope.selected.size,2);
''')

    def test_collapsed_filter_summary_tracks_active_values_and_reset_without_mutation(self):
        self.node(r'''
vm.runInContext(management.slice(management.indexOf('function syncBrowseFilterSummary()'),management.indexOf('function syncBrowseControls()')),scope);
scope.syncBrowseFilterSummary();assert.equal($('browse-options-summary').textContent,'筛选与排序');
$('sort').value='size';$('photo-month').value='2026/10';scope.syncBrowseFilterSummary();assert($('browse-options-summary').textContent.includes('2'));
assert.equal($('sort').value,'size');assert.equal($('photo-month').value,'2026/10');assert.equal(scope.selected.size,1);
$('source-folder').value='C:\\generated';scope.syncBrowseFilterSummary();assert($('browse-options-summary').textContent.includes('3'));
for(const id of ['sort','photo-month','source-folder'])$(id).value='';scope.syncBrowseFilterSummary();assert.equal($('browse-options-summary').textContent,'筛选与排序');
''')

    def test_similarity_paging_reaches_every_pair_and_clamps_boundaries_without_operations(self):
        self.node(r'''
const source=fs.readFileSync(root+'/report_actions.js','utf8');vm.runInContext(source.slice(source.indexOf('function pageReportPairs('),source.indexOf('function focusReportSection(')),scope);
const pairs=Array.from({length:23},()=>new E('article')),pager=new E();pager.append(new E('button'),new E('span'),new E('button'));
const section={querySelectorAll:()=>pairs,querySelector:()=>pager};scope.pageReportPairs(section);
assert.equal(pairs.filter(p=>!p.hidden).length,10);assert(pager.children[0].disabled);assert(pager.children[1].textContent.includes('23'));
pager.children[2].onclick();assert.equal(pairs.findIndex(p=>!p.hidden),10);pager.children[2].onclick();assert.equal(pairs.filter(p=>!p.hidden).length,3);assert(pager.children[2].disabled);assert(pager.children[1].textContent.includes('3/3'));
scope.pageReportPairs(section,99);assert.equal(pairs.findIndex(p=>!p.hidden),20);pager.children[0].onclick();assert.equal(pairs.findIndex(p=>!p.hidden),10);
scope.pageReportPairs({querySelectorAll:()=>[],querySelector:()=>pager});assert(pager.hidden);assert.equal(requests.length,0);
''')

    def test_focused_report_hides_other_sections_and_overview_restores_all_pairs(self):
        self.node(r'''
const source=fs.readFileSync(root+'/report_actions.js','utf8');vm.runInContext(source.slice(0,source.indexOf('function reportActionOpen(')),scope);
const main=new E('main'),body=new E('body'),header=new E('header'),other=new E('section'),section=new E('section');section.id='similar';
const description=new E('p','算法说明'),warning=new E('p','结果达到上限');warning.classList.add('notice');section.append(description,warning);main.append(header,section,other);
const pairs=Array.from({length:12},()=>new E('article'));let tools=null,pager=null;
section.querySelectorAll=()=>pairs;section.querySelector=selector=>selector==='.report-focus-tools'?tools:selector==='.report-pair-pager'?pager:null;
const append=section.append.bind(section),prepend=section.prepend.bind(section);section.append=(...nodes)=>{for(const n of nodes)if(n.className?.includes('report-pair-pager'))pager=n;append(...nodes);};section.prepend=(...nodes)=>{tools=nodes[0];prepend(...nodes);};
main.querySelectorAll=()=>pairs;Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:s=>s==='main'?main:s==='#similar'?section:null},location:{hash:'#similar'}});
scope.focusReportSection();assert(body.classList.contains('compact-report'));assert(header.classList.contains('report-section-hidden'));assert(other.classList.contains('report-section-hidden'));assert(!section.classList.contains('report-section-hidden'));
assert(description.classList.contains('report-section-note'));assert(!warning.classList.contains('report-section-note'));assert.equal(tools.children[0].textContent.includes('内容不同'),true);assert.equal(pairs.filter(p=>!p.hidden).length,10);
tools.children[1].onclick();assert(section.classList.contains('report-show-notes'));tools.children[1].onclick();assert(!section.classList.contains('report-show-notes'));
scope.location.hash='';scope.focusReportSection();assert(!body.classList.contains('compact-report'));assert(!other.classList.contains('report-section-hidden'));assert(pairs.every(p=>!p.hidden));assert.equal(requests.length,0);
''')

    def test_only_current_trusted_report_can_expand_modal_viewport(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),start=html.indexOf("window.addEventListener('message'"),end=html.indexOf('async function loadReports',start),events={};
Object.assign(scope,{window:{addEventListener:(name,handler)=>events[name]=handler},URL,selectedReport:'scan-generated'});const frame=$('result-frame');frame.src='http://127.0.0.1:12345/token/center.html';frame.contentWindow={};vm.runInContext(html.slice(start,end),scope);const viewer=$('result-viewer');
(async()=>{for(const event of [{source:{},origin:'http://127.0.0.1:12345',data:{type:'media-overlay',open:true}},{source:frame.contentWindow,origin:'https://foreign.invalid',data:{type:'media-overlay',open:true}},{source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-overlay',open:'yes'}}]){await events.message(event);assert(!viewer.classList.contains('overlay-mode'));}
await events.message({source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-overlay',open:true}});assert(viewer.classList.contains('overlay-mode'));
await events.message({source:frame.contentWindow,origin:'http://127.0.0.1:12345',data:{type:'media-overlay',open:false}});assert(!viewer.classList.contains('overlay-mode'));assert.equal(requests.length,0);})().catch(e=>{console.error(e);process.exitCode=1;});
''')
