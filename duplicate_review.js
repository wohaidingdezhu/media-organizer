// Report-only grouping; every cleanup still uses the existing confirmation flow.
let duplicateData=null,duplicateLoading=false,duplicatePage=0,duplicateRenderKey='',duplicateKeepers=new Map(),duplicateExpanded=new Map(),duplicateErrors=new Map();
async function loadDuplicateGroups(){
  if(duplicateLoading||duplicateData)return;
  duplicateLoading=true;$('duplicate-summary').textContent='正在读取完整 SHA-256 精确重复分组…';$('duplicate-retry').hidden=true;
  try{
    duplicateData=await api('api/duplicates');
    $('duplicate-heading').textContent=`精确重复分组核对 · ${duplicateData.counts.groups} 组`;
    $('duplicate-warnings').replaceChildren();
    for(const message of duplicateData.warnings)$('duplicate-warnings').append(make('p','warning',message));
    duplicateRenderKey='';renderDuplicateGroups();
  }catch(err){$('duplicate-summary').textContent='分组读取失败：'+err.message;$('duplicate-retry').hidden=false;}
  finally{duplicateLoading=false;}
}
function renderDuplicateGroups(){
  if(!duplicateData||!$('duplicate-panel').open)return;
  const query=$('duplicate-search').value.trim().toLocaleLowerCase(),kind=$('duplicate-kind').value;
  const key=JSON.stringify([query,kind,duplicatePage,saving,operating,handledTrash.size,photos.size,mediaAvailable,[...duplicateKeepers],[...duplicateExpanded],[...duplicateErrors]]);
  if(key===duplicateRenderKey)return;duplicateRenderKey=key;
  const groups=duplicateData.groups.filter(group=>(!kind||group.items.some(item=>item.kind===kind))&&(!query||group.sha256.includes(query)||group.items.some(item=>item.path.toLocaleLowerCase().includes(query))));
  const pages=Math.max(1,Math.ceil(groups.length/10));duplicatePage=Math.min(duplicatePage,pages-1);
  $('duplicate-summary').textContent=`共 ${duplicateData.counts.groups} 组 · ${duplicateData.counts.files} 个文件 · 重复副本逻辑大小 ${fileSize(duplicateData.counts.redundant_logical_bytes)}（至少保留每组一份）`;
  $('duplicate-page').textContent=`匹配 ${groups.length} 组 · 第 ${duplicatePage+1}/${pages} 页 · 按重复副本逻辑大小排序`;
  $('duplicate-previous').disabled=duplicatePage===0;$('duplicate-next').disabled=duplicatePage>=pages-1;
  $('duplicate-groups').replaceChildren();
  for(const group of groups.slice(duplicatePage*10,duplicatePage*10+10)){
    const box=make('article','duplicate-group'),keeper=duplicateKeepers.get(group.number),limit=duplicateExpanded.get(group.number)||50;
    box.append(make('h3','',`精确重复第 ${group.number} 组 · ${group.items.length} 个文件 · 每个 ${fileSize(group.bytes_each)}`),make('p','muted',`重复副本逻辑大小 ${fileSize(group.redundant_logical_bytes)} · 扫描时内容一致，操作前会重新检查文件状态。`));
    const hash=make('details');hash.append(make('summary','','查看完整 SHA-256'),make('pre','',group.sha256));box.append(hash);
    for(const item of group.items.slice(0,limit)){
      const row=make('div','duplicate-member'+(keeper===item.id?' kept':'')),body=make('div'),handled=handledTrash.has(item.path);
      if(item.kind==='照片'){const thumb=make('div','duplicate-thumb');picture(item,thumb);row.append(thumb);}
      body.append(make('strong','',item.name),make('div','path',item.path),make('p','muted',`${item.kind} · ${fileSize(item.bytes)} · 修改时间：${item.mtime===null?'报告未提供':new Date(item.mtime*1000).toLocaleString('zh-CN')}`));
      if(item.sidecars.length){
        const details=make('details');details.append(make('summary','',`关联附属文件 ${item.sidecars.length} 项（保留原位置）`));
        for(const sidecar of item.sidecars)details.append(make('div','path',sidecar.status+'：'+sidecar.path));body.append(details);
      }
      const actions=make('div','target-actions'),keep=make('button',keeper===item.id?'primary':'',keeper===item.id?'已选本组保留项':'选作本组保留项');
      keep.setAttribute('aria-label','选作本组保留项 '+item.name);keep.setAttribute('aria-pressed',String(keeper===item.id));keep.disabled=saving||operating||handled;
      keep.onclick=()=>{duplicateKeepers.set(group.number,item.id);duplicateErrors.delete(group.number);renderDuplicateGroups();};actions.append(keep);
      for(const [action,label] of [['open',item.kind==='视频'?'播放影片':'打开原图'],['reveal','在 文件管理器 定位']]){
        const button=make('button','',label);button.setAttribute('aria-label',label+' '+item.name);button.disabled=saving||operating||handled||!mediaIds[item.path];
        button.onclick=()=>openMedia(item,action,button);actions.append(button);
      }
      if(handled)body.append(make('p','warning','已移到废纸篓 / 回收站，请重新扫描更新分组。'));
      body.append(actions);row.append(body);box.append(row);
    }
    if(group.items.length>limit){const more=make('button','',`再显示 50 个成员（尚有 ${group.items.length-limit} 项）`);more.onclick=()=>{duplicateExpanded.set(group.number,limit+50);renderDuplicateGroups();};box.append(more);}
    const active=group.items.filter(item=>!handledTrash.has(item.path)),chosen=active.find(item=>item.id===keeper),preview=make('button','danger','预览清理其余副本');
    preview.disabled=saving||operating||!chosen||active.length<2||active.length>201||group.items.length>limit||!mediaAvailable||active.some(item=>!mediaIds[item.path]);
    preview.onclick=()=>previewDuplicateGroup(group);const toolbar=make('div','duplicate-toolbar');toolbar.append(preview);box.append(toolbar);
    if(duplicateErrors.has(group.number))box.append(make('p','error',duplicateErrors.get(group.number)));
    box.append(make('p','muted',group.items.length>limit?'请先展开完整分组核对。':active.length<2?'本组已处理后仅剩一份可选，请重新扫描更新分组。':active.length>201?'本组副本超过单批 200 项，请在主清单分批勾选，并至少保留一份。':chosen?'预览只选本组其余副本，会清空其他勾选；保留选择仅用于本页核对，刷新后需要重新选择。':'请明确选择一份保留项，再预览其余副本；没有默认保留或默认勾选。'));
    $('duplicate-groups').append(box);
  }
  if(!groups.length)$('duplicate-groups').append(make('p','empty','没有匹配的完整 SHA-256 精确重复组。可检查筛选条件、总报告的跳过项和读取问题。'));
}
async function previewDuplicateGroup(group){
  if(saving||operating)return;
  const keeper=duplicateKeepers.get(group.number),active=group.items.filter(item=>!handledTrash.has(item.path));
  if(!active.some(item=>item.id===keeper)||active.length<2||active.length>201||group.items.length>(duplicateExpanded.get(group.number)||50))return;
  duplicateErrors.delete(group.number);
  selected=new Set(active.filter(item=>item.id!==keeper).map(item=>item.id));render();
  await previewOperation('trash');
  if($('execution').open&&executionPreview&&executionPreview.mode==='trash'){
    const kept=active.find(item=>item.id===keeper);
    const note=make('li');note.append(make('strong','','本组保留项（不执行清理）'),make('div','path',kept.path));$('execution-items').prepend(note);
  }else if(!$('error').hidden){
    duplicateErrors.set(group.number,$('error').textContent);renderDuplicateGroups();
  }
}
$('duplicate-panel').addEventListener('toggle',()=>{if($('duplicate-panel').open){if(duplicateData)renderDuplicateGroups();else loadDuplicateGroups();}});
$('duplicate-open').onclick=()=>{$('duplicate-panel').open=true;$('duplicate-panel').scrollIntoView({behavior:'smooth',block:'start'});};
$('duplicate-retry').onclick=loadDuplicateGroups;
for(const id of ['duplicate-search','duplicate-kind'])$(id).addEventListener(id==='duplicate-search'?'input':'change',()=>{duplicatePage=0;renderDuplicateGroups();});
$('duplicate-previous').onclick=()=>{duplicatePage--;renderDuplicateGroups();};
$('duplicate-next').onclick=()=>{duplicatePage++;renderDuplicateGroups();};
