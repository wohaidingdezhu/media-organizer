function appendOperationStatus(item,parent){if(handledTrash.has(item.path))parent.append(make('p','warning','已移到废纸篓 / 回收站，请重新扫描更新清单'));else if(handledCopy.has(item.path))parent.append(make('span','badge status-include','已有复制成功记录 · 原件保留'));}
function picture(item,parent){const photo=photos.get(item.id);if(photo&&/^previews\/[A-Za-z0-9_-]+\.png$/.test(photo.preview||'')){const img=make('img');img.src=photo.preview;img.alt=item.name;img.loading='lazy';img.onclick=()=>viewPictures([item]);parent.append(img);}else parent.append(make('div','photo-placeholder','预览未生成 · 可打开原图'));}
function renderPhotos(){if(!data)return;$('photo-wall').replaceChildren();if($('view-mode').value!=='wall')return;for(const item of pageItems){const card=make('article','photo-card');picture(item,card);const label=make('label'),check=make('input');check.type='checkbox';check.checked=selected.has(item.id);check.disabled=saving||operating||handledTrash.has(item.path);check.setAttribute('aria-label','选择照片 '+item.name);check.onchange=()=>{if(check.checked)selected.add(item.id);else selected.delete(item.id);updateSelection();};label.append(check,make('strong','',item.name));card.append(label,make('div','path',item.path),make('div','muted',`${fileSize(item.bytes)} · ${(photos.get(item.id)||{}).month||'日期未提供'} · ${names[item.state]}`),make('div','reason',item.reason));appendOperationStatus(item,card);appendBasketStatus(item,card);if(item.duplicate_group)card.append(make('span','badge warning','精确重复第 '+item.duplicate_group+' 组'));if(mediaIds[item.path])for(const[action,text]of[['open','打开原图'],['reveal','在 文件管理器 定位']]){const button=make('button','',text);button.disabled=saving||operating||handledTrash.has(item.path);button.setAttribute('aria-label',text+' '+item.name);button.onclick=()=>openMedia(item,action,button);card.append(button);}const edit=make('button','','调整分类位置');edit.disabled=saving||operating;edit.onclick=()=>openEditor(item);card.append(edit);$('photo-wall').append(card);}if(!pageItems.length)$('photo-wall').append(make('p','empty','没有符合条件的照片。'));}
function viewPictures(items){$('picture-title').textContent=items.length===2?'并排比较照片':'照片预览';$('picture-body').replaceChildren();const grid=make('div',items.length===2?'compare-pictures':'');for(const item of items){const cell=make('div');picture(item,cell);cell.append(make('div','path',item.path));if(mediaIds[item.path]){const button=make('button','','打开原图');button.onclick=()=>openMedia(item,'open',button);cell.append(button);}grid.append(cell);}$('picture-body').append(grid);if(!$('picture-viewer').open)$('picture-viewer').showModal();}
$('select-all-current').onclick=()=>{const items=pageItems.filter(item=>!handledTrash.has(item.path)),all=items.every(item=>selected.has(item.id));for(const item of items){if(all)selected.delete(item.id);else selected.add(item.id);}render();};
$('picture-close').onclick=()=>$('picture-viewer').close();$('compare').onclick=()=>viewPictures(data.items.filter(item=>selected.has(item.id)));
$('view-mode').onchange=()=>{page=0;selected.clear();render();};
function optionsFor(id,values,label){$(id).replaceChildren();const all=make('option','',label);all.value='';$(id).append(all);for(const value of [...new Set(values)].filter(Boolean).sort().slice(0,1000)){const option=make('option','',value);option.value=value;$(id).append(option);}}
async function api(route,body){const response=await fetch(route,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const result=await response.json();if(!response.ok)throw new Error(result.error||'请求失败');return result;}
async function checkDisks(){try{const result=await api('api/roots');$('disk-status').replaceChildren();for(const root of result.roots)$('disk-status').append(make('p','',`${root.status==='available'?'来源可访问':'来源不可访问（磁盘未连接、文件夹移走或权限问题）'}：${root.path}`));}catch(err){$('disk-status').textContent=err.message;}}
$('check-disks').onclick=checkDisks;
async function loadExtras(){const results=await Promise.allSettled([api('api/photos'),api('api/changes')]);if(results[0].status==='fulfilled'){const items=results[0].value.items;photos=new Map(items.map(item=>[item.id,item]));optionsFor('photo-month',items.map(item=>item.month),'全部照片年月');render();}else error('照片清单读取失败：'+results[0].reason.message);if(results[1].status==='fulfilled'){const result=results[1].value;const labels={added:'新增',changed:'发生变化',absent:'本次未扫描到'};$('changes-list').replaceChildren();$('changes-list').append(make('p','',result.message||`对比 ${result.previous_created_at}：新增 ${result.counts.added} · 变化 ${result.counts.changed} · 本次未扫描到 ${result.counts.absent}`));for(const item of result.items.slice(0,200))$('changes-list').append(make('p','path',labels[item.state]+'：'+item.path));if(result.items.length>200)$('changes-list').append(make('p','','仅展示前 200 项。'));}else $('changes-list').textContent='比较失败：'+results[1].reason.message;checkDisks();refreshOperations();refreshBasket();}
const oldBuildFolders=buildFolders;buildFolders=function(){oldBuildFolders();optionsFor('source-folder',data.items.map(item=>item.source_folder),'全部原文件夹');};
$('choose-destination').onclick=async()=>{if(operating||saving)return;const button=$('choose-destination');button.disabled=true;try{const result=await api('api/operations/destination',{});if(result.destination)$('destination').value=result.destination;}catch(err){error(err.message);}finally{render();}};
async function previewOperation(mode,bundle=false){if(operating||saving||!selected.size)return;saving=true;error();$('notice').textContent='正在检查文件和目标位置…';render();try{executionPreview=await api('api/operations/preview',{mode,bundle,ids:[...selected],destination:$('destination').value.trim()});$('execution-title').textContent=bundle?'确认影片和附件整组复制':mode==='copy'?'确认复制到分类目录':'确认移到废纸篓 / 回收站';$('execution-summary').textContent=`${executionPreview.items.length} 个文件 · ${fileSize(executionPreview.bytes)} · ${mode==='copy'?'复制后保留原件，目标副本使用 SHA-256 校验':'从原位置移到系统废纸篓 / 回收站，可在文件管理器恢复；占用空间不一定立即释放'}`;$('execution-items').replaceChildren();for(const group of executionPreview.bundles||[]){const row=make('li');row.append(make('strong','',group.title+(group.edition?' · '+group.edition:'')),make('p','',`${group.videos} 视频 · 字幕 ${group.counts.subtitle} · NFO ${group.counts.nfo} · 封面 ${group.counts.cover} · 剧照 ${group.counts.still}`));if(group.missing.length)row.append(make('p','muted','未发现可选资料：'+group.missing.join('、')));$('execution-items').append(row);}for(const item of executionPreview.items){const row=make('li');row.append(make('strong','',item.role||'媒体'),make('div','path',item.path),make('div','path','→ '+item.target));$('execution-items').append(row);}for(const item of executionPreview.skipped||[]){const row=make('li','warning');row.append(make('strong','','未加入复制：'+item.reason),make('div','path',item.path));$('execution-items').append(row);}$('execution-confirm').checked=false;$('execution-start').disabled=true;$('execution-error').hidden=true;$('execution').showModal();$('notice').textContent='检查完成，请在弹窗中核对。';}catch(err){error(err.message);$('notice').textContent='';}finally{saving=false;render();}}
$('copy-files').onclick=()=>previewOperation('copy');$('trash-files').onclick=()=>previewOperation('trash');
$('copy-bundles').onclick=()=>previewOperation('copy',true);
$('execution-confirm').onchange=()=>$('execution-start').disabled=!$('execution-confirm').checked||operating;
function cancelPreview(){executionPreview=null;$('notice').textContent='已取消预览，未执行文件操作。';}
$('execution-cancel').onclick=()=>{cancelPreview();$('execution').close();};
$('execution').addEventListener('cancel',event=>{if(operating)event.preventDefault();else cancelPreview();});
$('execution-start').onclick=async()=>{
  if(!executionPreview||!$('execution-confirm').checked||operating)return;
  operating=true;monitorRevision++;clearTimeout(operationTimer);render();
  $('execution-start').disabled=true;$('execution-cancel').disabled=true;
  try{
    await api('api/operations/start',{token:executionPreview.token});
    selected.clear();$('notice').textContent='操作已开始，请保持本机服务运行，完成后重新扫描。';
  }catch(err){
    error('启动请求未确认：'+err.message+'。正在读取操作记录，请勿重复执行。');
  }finally{
    executionPreview=null;$('execution').close();$('execution-cancel').disabled=false;
    refreshOperations();
  }
};
function activeOperation(job){return ['running','external_running'].includes(job.status);}
function operationCard(job){
  const box=make('div','operation'),state={running:'执行中',external_running:'另一服务执行中',complete:'已完成',stopped:'遇到问题已停止',cancelled:'已安全停止',interrupted:'服务曾中断'};
  box.append(make('strong','',`${job.bundle?'影片和附件整组复制':job.mode==='copy'?'复制分类':'废纸篓 / 回收站清理'} · ${state[job.status]||job.status} · ${job.items.filter(item=>item.status==='success').length}/${job.total}`),make('p','muted',job.created_at||''));
  if(activeOperation(job)){
    const stop=make('button','',job.stop_requested?'正在安全停止…':'安全停止此批次');
    stop.disabled=!!job.stop_requested;stop.onclick=()=>stopOperation(job.id,stop);box.append(stop);
    box.append(make('p','muted',job.mode==='copy'?'停止会清理当前未完成临时副本，已完成副本保留；磁盘正在响应时可能需要等待。':'当前文件的系统废纸篓 / 回收站操作会完成，再停止后续项；不会自动恢复已成功项。'));
  }
  if(job.error)box.append(make('p','warning',job.error));
  for(const item of job.items){
    const text=make('pre','',`${item.status==='success'?'成功':item.status==='cancelled'?'已取消，原件保留':item.status==='processing'?(activeOperation(job)?`正在${({copying:'复制',verifying:'校验副本',verified:'完成校验'})[item.phase]||'处理'}${job.mode==='copy'?'（'+fileSize(item.processed_bytes||0)+' / '+fileSize(item.bytes||0)+'）':''}`:'处理结果待确认'):item.status==='unknown'?'结果未确认':'失败'}：${item.role?'['+item.role+'] ':''}${item.path}\n→ ${item.trashed_path||item.target}${item.error?'\n'+item.error:''}`);
    box.append(text);
  }
  const started=new Set(job.items.map(item=>item.path)),pending=(job.planned_items||[]).filter(item=>!started.has(item.path));
  if(pending.length){
    const details=make('details'),summary=make('summary','',`${activeOperation(job)?'尚未开始':'未处理'} ${pending.length} 个文件（原位置保留）`);details.append(summary);
    for(const item of pending)details.append(make('pre','',(item.role?'['+item.role+'] ':'')+item.path+'\n→ '+item.target));box.append(details);
  }else if(!job.planned_items&&job.total>job.items.length){box.append(make('p','warning',`还有 ${job.total-job.items.length} 项未记录处理结果。旧记录没有完整批次清单，请对照报告核对。`));}
  if(!job.bundle&&['cancelled','stopped'].includes(job.status)&&!job.error&&job.planned_items&&(pending.length||job.items.some(item=>item.status==='cancelled'))){
    const review=make('button','','核对此批次未处理项');review.disabled=operating||saving;
    const result=make('div');result.setAttribute('role','status');
    review.onclick=()=>reviewRemaining(job.id,review,result);box.append(review,result);
  }
  if(job.bundle&&!activeOperation(job)&&job.status!=='complete')box.append(make('p','warning','整组未全部完成，请按影片、字幕、封面等逐项核对。已成功副本保留；重新整组预览遇到已有目标会拒绝覆盖，不会自动重试。'));
  box.append(make('p','muted','记录保存在本次报告的 operations 文件夹。已成功项不会自动撤销；结果未确认时先在 文件管理器 核对。'));return box;
}
async function reviewRemaining(id,button,result){
  if(operating||saving||!data)return;
  let showSelection=false;
  saving=true;button.disabled=true;error();render();
  try{
    const advice=await api('api/operations/'+id+'/remaining');
    selected=new Set(advice.ids);
    showSelection=advice.ids.length>0;
    for(const filter of filters)$(filter).value='';
    $('risk').value='selected';$('view-mode').value='list';page=0;
    result.replaceChildren(make('p','',`已勾选 ${advice.ids.length} 项，跳过 ${advice.skipped.length} 项。${advice.message}`));
    if(advice.skipped.length){const details=make('details');details.append(make('summary','','查看跳过原因'));for(const item of advice.skipped)details.append(make('p','path',item.path+'：'+item.reason));result.append(details);}
    $('notice').textContent=advice.ids.length?`已勾选 ${advice.ids.length} 个未处理文件。请核对分类位置与目标文件夹，再重新预览确认。`:'没有可直接继续核对的文件，请查看此批次的跳过原因。';
  }catch(err){result.replaceChildren(make('p','warning',err.message));error(err.message);}
  finally{saving=false;render();button.disabled=operating;if(showSelection)$('selection').scrollIntoView({block:'start'});}
}
async function stopOperation(id,button){
  button.disabled=true;
  try{
    await api('api/operations/stop',{id});
    $('notice').textContent='已请求安全停止，请等待逐项结果；已完成项保留。';
  }catch(err){error('停止请求未确认，请查看进度后重试：'+err.message);}
  refreshOperations();
}
async function refreshOperations(){
  clearTimeout(operationTimer);
  const revision=++monitorRevision;
  try{
    const result=await api('api/operations');if(revision!==monitorRevision)return;
    const wasBusy=operating,wasRetrying=monitorFailures>0;monitorFailures=0;
    operating=result.jobs.some(activeOperation)||(result.warnings||[]).length>0;
    handledTrash=new Set(result.handled.trash);handledCopy=new Set(result.handled.copy);
    $('operations-summary').textContent=`展示最近 ${result.jobs.length}/${result.record_count} 个批次；文件标记核对本报告的全部有效操作记录。成功记录不代表文件现在仍在目标位置，操作后请重新扫描。`;
    $('operations').replaceChildren();
    for(const warning of result.warnings||[])$('operations').append(make('p','warning',warning));
    for(const job of result.jobs){
      $('operations').append(operationCard(job));
    }
    if(wasRetrying)error();
    if(result.jobs.some(activeOperation)){
      $('notice').textContent=result.jobs.some(job=>activeOperation(job)&&job.stop_requested)?'正在安全停止，请保持本机服务运行，等待当前文件结果。':'文件操作执行中，进度会自动更新。请保持本机服务运行；完成前暂停修改计划。';
      operationTimer=setTimeout(refreshOperations,1000);
    }else if((result.warnings||[]).length){
      $('notice').textContent='部分操作记录无法读取，暂时锁定文件操作。请先核对记录与 文件管理器，再点击“读取操作记录”。';
    }else if(wasBusy&&result.jobs.length){
      $('notice').textContent=result.jobs[0].status==='complete'?'操作完成，请重新扫描更新文件清单。':result.jobs[0].status==='cancelled'?'已安全停止，已成功项保留。请查看未处理文件清单，重新选择后预览。':'操作已停止，请查看逐项结果；成功项保留，未确认项先在 文件管理器 核对。';
    }else if(wasRetrying){$('notice').textContent='已恢复连接，没有正在执行的文件操作。';}
    if(!result.jobs.length&&!(result.warnings||[]).length)$('operations').append(make('p','','本次扫描还没有文件操作记录。'));
    render();
  }catch(err){
    if(revision!==monitorRevision)return;
    operating=true;monitorFailures++;
    error('进度连接失败，操作状态暂未确认。已锁定执行和计划修改，将自动重连：'+err.message);
    $('notice').textContent='请保持服务运行；也可点击“读取操作记录”重试。';render();
    operationTimer=setTimeout(refreshOperations,Math.min(10000,1000*2**Math.min(monitorFailures,4)));
  }
}
$('refresh-operations').onclick=refreshOperations;
// Keep idle windows in sync with batches started elsewhere.
window.addEventListener('focus',()=>{if(!executionPreview&&!saving){refreshOperations();refreshBasket();}});
window.addEventListener('pagehide',()=>{monitorRevision++;clearTimeout(operationTimer);});
