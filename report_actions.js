// Reports reuse the same preview and confirmation as photo/file browsing.
let reportFocusReady=false,reportScrollLock=null;
function reportOverlay(open){
  if(typeof window==='undefined')return;
  open=open||!!document.querySelector?.('dialog[open]');
  const root=document.documentElement||document.body;
  if(open&&!reportScrollLock){reportScrollLock={body:document.body.style.overflow,root:root.style.overflow};document.body.style.overflow=root.style.overflow='hidden';}
  if(!open&&reportScrollLock){document.body.style.overflow=reportScrollLock.body;root.style.overflow=reportScrollLock.root;reportScrollLock=null;}
  if(window.parent===window)return;
  const link=document.querySelector?.('nav[aria-label="报告导航"] a');
  if(link)window.parent.postMessage({type:'media-overlay',open},new URL(link.href).origin);
}
async function reportResumeTrash(identifier,onClose,onStatus=()=>{},beforeOpen=()=>{},isCurrent=()=>true){
  try{
    const response=await fetch('api/operations/'+identifier+'/remaining'),result=await response.json();
    if(!isCurrent())return;
    if(!response.ok)throw Error(result.error||'无法读取未处理项');
    if(result.mode!=='trash'||!Array.isArray(result.ids))throw Error('操作记录不匹配');
    if(!result.ids.length){onStatus('没有可继续的未处理文件。已完成或结果需核对的项目不会重新提交。');return;}
    beforeOpen();reportTrashOpen(result.ids,onClose,'继续核对未开始或安全取消的文件；跳过 '+(result.skipped||[]).length+' 项。仍需重新确认。');
  }catch(error){if(isCurrent())onStatus(error.message);}
}
function reportTrashProgress(identifier,onClose){
  if(typeof identifier==='string'&&/^[0-9a-f]{24}$/.test(identifier))reportTrashOpen([],onClose,'',identifier);
}
async function reportRequest(path, body) {
  const options = body === undefined ? {} :
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '请求未完成');
  return result;
}

function openReportDialog(kind, state, actions, onCancel, onClose) {
  const root = mountReportDialog(document.body);
  const nodes = {};
  const refs = {dialog: node => { if (node) nodes.dialog = node; }, content: node => { nodes.content = node; }};
  const render = () => root.render({kind, state, actions, refs});
  render();
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    root.dispose();
    reportOverlay(false);
    onClose();
  };
  nodes.dialog.addEventListener('cancel', onCancel);
  nodes.dialog.addEventListener('close', finish);
  nodes.dialog.showModal();
  reportOverlay(true);
  return {
    render,
    close() { nodes.dialog.close(); finish(); },
    scrollToStart() { if (nodes.content) nodes.content.scrollTop = 0; },
  };
}

function reportTrashOpen(ids, onClose, summary='', existingJob='') {
  if (existingJob ? !/^[0-9a-f]{24}$/.test(existingJob) : !Array.isArray(ids) || ids.length < 1 || ids.length > 200
    || new Set(ids).size !== ids.length || ids.some(id => !/^[0-9a-f]{64}$/.test(id))) return;

  const state = {monitoring:!!existingJob, status:'', rows:[], summary,
    progress:{total:ids.length, completed:0, visible:false}, running:false, stopAllowed:false, stopLocked:false,
    canConfirm:false, canResume:false, retry:false, returning:false, resuming:false};
  let preview = null, closed = false, jobId = existingJob, timer = null, previewTimer = null, checking = false, stopRequested = false;
  const bytes = value => Number.isFinite(value) && value >= 0
    ? (value >= 1073741824 ? (value/1073741824).toFixed(1) + ' GB'
      : value >= 1048576 ? (value/1048576).toFixed(1) + ' MB' : (value/1024).toFixed(0) + ' KB')
    : '';
  function finish() {
    state.running = false;
    state.stopAllowed = false;
    state.returning = true;
  }
  async function poll() {
    if (closed) return;
    try {
      const job = await reportRequest('api/operations/' + jobId);
      if (closed) return;
      if (job.id !== jobId || job.mode !== 'trash') throw Error('操作记录不匹配');
      const items = job.items || [];
      const completed = items.filter(item => ['success','complete'].includes(item.status)).length;
      const total = Number.isInteger(job.total) && job.total > 0 ? job.total : ids.length;
      state.progress = {completed, total, visible:true};
      if (job.status === 'complete') {
        state.running = false;
        dialog.close();
        return;
      }
      if (['running','external_running'].includes(job.status)) {
        state.running = true;
        state.stopAllowed = true;
        const current = items.find(item => item.status === 'processing');
        const prefix = stopRequested || job.stop_requested
          ? '正在安全停止；当前文件核对或系统回收结束后停止。' : '正在核对并移到回收站。';
        state.status = prefix + ' 已完成 ' + completed + '/' + total + ' 个文件（'
          + Math.floor(completed * 100 / (total || 1)) + '%）' + (current ? '；当前：' + current.path : '');
        timer = setTimeout(poll, 1000);
      } else {
        finish();
        state.canResume = ['stopped','cancelled'].includes(job.status) && !job.error && Boolean(job.planned_items);
        state.status = '处理已停止，完成 ' + completed + '/' + total
          + ' 个文件。已完成的文件在回收站中，其余文件未自动重试。' + (job.error ? ' ' + job.error : '');
        state.rows = items.map(item => (['complete','success'].includes(item.status) ? '已完成：' : '待核对：')
          + item.path + (item.error ? ' · ' + item.error : ''));
        const started = new Set(items.map(item => item.path));
        for (const item of job.planned_items || []) if (!started.has(item.path)) state.rows.push('未开始：'+item.path);
      }
      dialog.render();
    } catch (error) {
      if (closed) return;
      state.status = '连接暂时中断，正在核对处理结果，请勿重复删除：' + error.message;
      dialog.render();
      timer = setTimeout(poll, 3000);
    }
  }
  async function checkPreview() {
    if (checking || state.running || closed) return;
    checking = true;
    preview = null;
    Object.assign(state, {retry:false, canConfirm:false, returning:false, rows:[]});
    const began = Date.now();
    const waiting = () => {
      if (!checking || closed) return;
      const seconds = Math.floor((Date.now()-began)/1000);
      state.status = '正在核对 ' + ids.length + ' 个文件' + (seconds ? ' · 已等待 '+seconds+' 秒' : '')
        + '。大文件及保留副本的完整校验可能较慢；尚未删除，可取消返回，所选文件会保留。';
      dialog.render();
      if (typeof setTimeout === 'function') previewTimer = setTimeout(waiting,1000);
    };
    waiting();
    try {
      const result = await reportRequest('api/operations/preview', {mode:'trash', ids});
      if (closed) return;
      preview = result;
      const amount = bytes(result.bytes);
      state.status = result.items.length + ' 个文件' + (amount ? ' · '+amount : '') + '将移到系统回收站，可以恢复。';
      state.rows = result.items.map(item => item.path);
      state.canConfirm = Boolean(result.token);
    } catch (error) {
      if (!closed) {
        state.status = error.message + '；所选文件已保留，处理原因后可重新检查。';
        state.returning = state.retry = true;
      }
    } finally {
      checking = false;
      clearTimeout(previewTimer);
      if (!closed) dialog.render();
    }
  }

  const actions = {
    close() { if (!state.running || existingJob) dialog.close(); },
    retry: checkPreview,
    async confirm() {
      if (closed || state.running || !preview?.token) return;
      const token = preview.token;
      preview = null;
      Object.assign(state, {running:true, canConfirm:false, rows:[], status:'正在提交…'});
      dialog.render();
      dialog.scrollToStart();
      try {
        const started = await reportRequest('api/operations/start', {token});
        if (closed) return;
        jobId = started.id;
        state.stopAllowed = true;
        await poll();
      } catch (error) {
        if (closed) return;
        finish();
        state.status = '启动结果未确认，请在整理工具中核对操作记录后再处理：' + error.message;
        dialog.render();
      }
    },
    async stop() {
      if (closed || !jobId || !state.stopAllowed || state.stopLocked) return;
      state.stopLocked = true;
      dialog.render();
      try {
        await reportRequest('api/operations/stop', {id:jobId});
        stopRequested = true;
        state.status = '正在安全停止；当前文件核对或系统回收结束后停止。';
      } catch (error) {
        state.status = '停止请求未确认：' + error.message;
        state.stopLocked = false;
      }
      if (!closed) dialog.render();
    },
    async resume() {
      if (!state.canResume || state.resuming || closed) return;
      state.resuming = true;
      dialog.render();
      await reportResumeTrash(jobId,onClose,text => {
        state.status = text;
      },() => dialog.close(),() => !closed);
      if (!closed) { state.resuming = false; dialog.render(); }
    },
  };
  const dialog = openReportDialog('trash', state, actions,
    event => { if (state.running && !existingJob) event.preventDefault(); },
    () => {
      closed = true;
      clearTimeout(timer);
      clearTimeout(previewTimer);
      if (typeof onClose === 'function') onClose();
    });
  if (existingJob) {
    state.status = '正在读取处理记录，关闭窗口不会停止后台操作。';
    dialog.render();
    poll();
  } else checkPreview();
}
function pageReportPairs(section,page=0){
  const pairs=[...section.querySelectorAll('.pair')],pages=Math.max(1,Math.ceil(pairs.length/10));page=Math.max(0,Math.min(page,pages-1));
  pairs.forEach((pair,index)=>{pair.hidden=index<page*10||index>=(page+1)*10;});
  const pager=section.querySelector('.report-pair-pager');
  if(pager){pager.hidden=!pairs.length;pager.children[0].disabled=page===0;pager.children[1].textContent=`${pairs.length} 对相似照片 · 第 ${page+1}/${pages} 页`;pager.children[2].disabled=page>=pages-1;pager.children[0].onclick=()=>pageReportPairs(section,page-1);pager.children[2].onclick=()=>pageReportPairs(section,page+1);}
}
function focusReportSection(){
  const main=document.querySelector('main');if(!main||main.tagName!=='MAIN')return;
  const section=['#similar','#duplicates'].includes(location.hash)?document.querySelector(location.hash):null;
  if(!reportFocusReady){const style=document.createElement('style');style.textContent='.report-section-hidden{display:none!important}.compact-report main{margin:12px auto}.compact-report section{margin:0}.compact-report section:not(.report-show-notes)>.report-section-note{display:none}body:not(.compact-report) .report-focus-tools{display:none}.report-focus-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0}.report-focus-tools p{flex-basis:100%;margin:0;color:#51675e}';document.body.append(style);reportFocusReady=true;}
  document.body.classList.toggle('compact-report',!!section);
  for(const child of main.children)child.classList.toggle('report-section-hidden',!!section&&child!==section);
  if(!section){for(const pair of main.querySelectorAll('.pair'))pair.hidden=false;return;}
  if(!section.querySelector('.report-focus-tools')){
    for(const child of section.children)if(child.tagName==='P'&&!child.classList.contains('empty')&&!child.classList.contains('notice'))child.classList.add('report-section-note');
    const tools=document.createElement('div'),hint=document.createElement('p'),notes=document.createElement('button'),all=document.createElement('a');tools.className='report-focus-tools';
    hint.textContent=section.id==='similar'?'相似照片可能内容不同。直接对比，确认后删除不要的照片。':'这些文件内容完全相同。可对比或逐项删除，至少保留一份。';
    notes.textContent='查看说明';notes.onclick=()=>{const open=!section.classList.contains('report-show-notes');section.classList.toggle('report-show-notes',open);notes.textContent=open?'收起说明':'查看说明';};
    all.textContent='查看完整报告';all.href='report.html';tools.append(hint,notes,all);section.prepend(tools);
    if(section.id==='similar'){
      const pager=document.createElement('div');pager.className='report-focus-tools report-pair-pager';
      for(const [tag,label]of [['button','上一页'],['span',''],['button','下一页']]){const node=document.createElement(tag);node.textContent=label;pager.append(node);}section.append(pager);
    }
  }
  if(section.id==='similar')pageReportPairs(section);
}
function reportActionOpen(action, ids, onClose, summary='', onReview) {
  if (action === 'delete') return reportTrashOpen(ids,onClose,summary);
  if (action !== 'compare' || !Array.isArray(ids) || ids.length !== 2 || ids[0] === ids[1]
    || ids.some(id => !/^[0-9a-f]{64}$/.test(id))) return;

  let closed = false, handoff = false;
  const state = {photos:[], review:typeof onReview === 'function', status:'正在读取照片…', saving:false};
  const actions = {
    close() { if (!state.saving) dialog.close(); },
    trash(file) {
      if (closed || state.saving || !file.canTrash) return;
      handoff = true;
      dialog.close();
      reportTrashOpen([file.id],onClose);
    },
    async decide(decision) {
      if (closed || state.saving || !state.review) return;
      state.saving = true;
      state.status = '正在保存核对结论…';
      dialog.render();
      try {
        await onReview(decision, () => { handoff = true; dialog.close(); });
        if (!handoff) state.status = '未能保存核对结论，请重试或返回列表刷新。';
      } catch (error) {
        state.status = error.message;
      } finally {
        state.saving = false;
        if (!closed && !handoff) dialog.render();
      }
    },
  };
  const dialog = openReportDialog('compare', state, actions,
    event => { if (state.saving) event.preventDefault(); },
    () => { closed = true; if (!handoff && typeof onClose === 'function') onClose(); });
  Promise.all([reportRequest('api/center'), reportRequest('api/media')]).then(([data, access]) => {
    if (closed) return;
    const photos = new Map();
    for (const item of data.items) if (item.kind === 'photo') for (const file of item.files) {
      if (ids.includes(file.id)) photos.set(file.id, {...file, preview:item.preview});
    }
    const chosen = ids.map(id => photos.get(id));
    if (chosen.some(file => !file)) throw Error('照片已不在当前清单，请刷新列表。');
    state.status = (state.review ? '并排核对后，可保存结论或选择要删除的照片。' : '并排核对后选择要删除的照片。')
      + '相似不代表内容完全相同。';
    state.photos = chosen.map(file => ({
      ...file,
      preview:/^(previews|covers)\/[A-Za-z0-9_-]+\.png$/.test(file.preview || '') ? file.preview : '',
      canTrash:Boolean(access.available && !data.restored && !data.operation_state?.running && !data.operation_state?.warnings?.length
        && access.ids_by_path[file.path] === file.id),
    }));
    dialog.render();
  }).catch(error => {
    if (!closed) { state.status = error.message; dialog.render(); }
  });
}
async function addReportFileActions(){
  if(!document.querySelector('#duplicates')&&!document.querySelector('#similar'))return;
  try{
    const response=await fetch('api/media');if(!response.ok)throw Error('读取文件操作入口失败');
    const catalog=await response.json(),ids=catalog.ids_by_path||{};
    const button=(parent,label,action,paths)=>{
      if(paths.some(path=>!ids[path]))return;
      const control=document.createElement('button');control.textContent=label;control.style.cssText='margin:6px;padding:7px 12px;cursor:pointer';
      control.setAttribute('aria-label',label+' '+paths.map(path=>path.split(/[\\/]/).pop()).join('、'));
      control.disabled=action==='delete'&&!catalog.available;
      control.onclick=()=>reportActionOpen(action,paths.map(path=>ids[path]));parent.append(control);
    };
    for(const group of document.querySelectorAll('#duplicates details')){
      const paths=[];
      for(const row of group.querySelectorAll('li')){const path=row.textContent.trim();paths.push(path);button(row,'删除…','delete',[path]);}
      if(paths.length===2&&paths.every(path=>/\.(jpe?g|png|gif|webp|heic|heif|bmp|tiff?)$/i.test(path)))button(group,'对比这两张','compare',paths);
    }
    for(const pair of document.querySelectorAll('#similar .pair')){
      const paths=[];
      for(const tile of pair.querySelectorAll('.image-tile')){const path=tile.lastElementChild?.textContent.trim();if(path){paths.push(path);button(tile,'删除…','delete',[path]);}}
      if(paths.length===2)button(pair,'对比这两张','compare',paths);
    }
  }catch(error){const note=document.createElement('p');note.textContent=error.message+'；刷新页面后重试。';note.setAttribute('role','alert');document.body.prepend(note);}
}
focusReportSection();
if(typeof window!=='undefined')window.addEventListener('hashchange',focusReportSection);
addReportFileActions();
