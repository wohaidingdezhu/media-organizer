let pictureView,pictureError='',pictureSession=0;
const pictureFailures=new Set(),pictureOriginalPending=new Set();
function setPictureError(message){
  pictureError=message||'';
  if(pictureView)syncPictureViewer();
}
async function previewTrashItem(item){
  if(saving||operating)return;
  if(!mediaAvailable||mediaIds[item.path]!==item.id||handledTrash.has(item.path)){error('此文件暂不能清理，请重新扫描或检查操作记录。');return;}
  const session=pictureSession;
  await previewOperation('trash',false,false,false,[item.id]);
  if(session===pictureSession&&!$('execution').open&&$('picture-viewer').open&&!$('error').hidden)setPictureError($('error').textContent);
}
function bundleReviewIds(info=bundleReviewInfo){return info?(info.media_ids||info.photo_ids):[];}
function bundleReviewKind(info=bundleReviewInfo){return info?.media_kind||'照片';}
function selectableBundleMedia(){if(!bundleReviewInfo||!data||bundleReviewInfo.restored_snapshot)return [];const ids=new Set(bundleReviewIds());const items=data.items.filter(item=>ids.has(item.id));return items.length===ids.size&&items.length>0&&items.length<=200&&items.every(item=>item.kind===bundleReviewKind()&&mediaIds[item.path]===item.id&&!handledTrash.has(item.path))?items:[];}
function syncBundleReviewControls(){const items=selectableBundleMedia();$('bundle-review-select').disabled=saving||operating||!items.length;$('bundle-review-select').textContent=bundleReviewInfo?`勾选本组${bundleReviewKind()==='视频'?'视频':'照片'}（${bundleReviewIds().length} ${bundleReviewKind()==='视频'?'项':'张'}）`:'勾选本组媒体';}
async function openBundleReview(item){
  const revision=++bundleReviewRevision;bundleReviewInfo=null;bundleReviewAnchor=item;
  $('bundle-review-title').textContent=(item.kind==='视频'?'影片整组核对':'照片配套文件核对')+' · '+item.name;$('bundle-review-summary').textContent='正在读取扫描记录和分类计划…';$('bundle-review-message').textContent='';$('bundle-review-items').replaceChildren();$('bundle-review-error').hidden=true;$('bundle-review-retry').hidden=true;syncBundleReviewControls();
  if(!$('bundle-review-dialog').open)$('bundle-review-dialog').showModal();
  try{
    const result=await api((item.kind==='视频'?'api/movie-bundles/':'api/photo-bundles/')+item.id);if(revision!==bundleReviewRevision)return;bundleReviewInfo=result;
    if(result.media_kind==='视频')$('bundle-review-title').textContent='影片整组核对 · '+result.title+(result.edition?' · '+result.edition:'');
    $('bundle-review-summary').textContent=Object.entries(result.counts).map(([label,count])=>label+' '+count).join(' · ')+` · 未明确关联 ${result.skipped.length} 项`;$('bundle-review-message').textContent=result.message;
    if(result.media_kind==='视频')$('bundle-review-items').append(make('li','muted','整组复制时，字幕、封面和剧照沿用本组视频的分类目录，保留原名与 extrafanart 子目录；下列附件分类计划仅用于独立处理，不会因整组复制而修改。'));
    if(result.restored_snapshot)$('bundle-review-items').append(make('li','warning','这是恢复的历史快照，只能核对记录；重新扫描后才能勾选并操作原文件。'));
    if(result.identity_missing)$('bundle-review-items').append(make('li','warning',`${result.identity_missing} 项缺少扫描身份记录；整组复制前需要重新扫描。`));
    if(result.missing?.length)$('bundle-review-items').append(make('li','muted','未发现：'+result.missing.join('、')+'。这些附件为可选资料，缺少不表示影片损坏。'));
    if(item.kind==='照片'&&result.items.length===1&&!result.skipped.length)$('bundle-review-items').append(make('li','muted','本次扫描未发现配套文件；只表示清单中没有记录，不代表照片损坏。'));
    for(const file of result.items){const row=make('li');row.append(make('strong','',file.label+' · '+fileSize(file.bytes)),make('div','path',file.path));if(file.plan){row.append(make('p','minor','分类计划：'+(names[file.plan.state]||file.plan.state)),make('div','path',file.plan.target||'暂无分类建议'));if(file.plan.blocked_reason)row.append(make('p','warning',file.plan.blocked_reason));}else row.append(make('p','minor','附属文件：整组复制时沿用本组媒体的目录，保留原名。'));$('bundle-review-items').append(row);}
    for(const file of result.skipped){const row=make('li','warning');row.append(make('strong','','未自动关联：'+file.reason),make('div','path',file.path));$('bundle-review-items').append(row);}
    syncBundleReviewControls();
  if(typeof syncExecutionControls==='function')syncExecutionControls();
  for(const id of ['quick-choose','quick-preview','quick-destination','quick-save-settings','quick-all-batches','quick-correct'])$(id).disabled=saving||operating;
  }catch(err){if(revision!==bundleReviewRevision)return;$('bundle-review-summary').textContent='配套清单未读取，请重试。';$('bundle-review-error').textContent=err.message;$('bundle-review-error').hidden=false;$('bundle-review-retry').hidden=false;}
}
$('bundle-review-close').onclick=()=>$('bundle-review-dialog').close();
$('bundle-review-dialog').addEventListener('close',()=>{bundleReviewRevision++;bundleReviewInfo=null;bundleReviewAnchor=null;syncBundleReviewControls();});
$('bundle-review-retry').onclick=()=>bundleReviewAnchor?openBundleReview(bundleReviewAnchor):undefined;
$('bundle-review-select').onclick=async()=>{
  if(saving||operating||!selectableBundleMedia().length)return;
  const info=bundleReviewInfo,revision=bundleReviewRevision;saving=true;syncBundleReviewControls();render();
  try{
    if(info.media_kind==='视频'){const current=await api('api/movie-bundles/'+bundleReviewAnchor.id);if(revision!==bundleReviewRevision)return;if(current.grouping_revision!==info.grouping_revision||current.group_key!==info.group_key||JSON.stringify(bundleReviewIds(current))!==JSON.stringify(bundleReviewIds(info)))throw new Error('影片分组已变化，请关闭后重新核对本组。');}
    const latest=await request();if(revision!==bundleReviewRevision)return;if(operating)throw new Error('有文件操作正在执行，请完成后再勾选。');data=latest;
    const items=selectableBundleMedia();if(!items.length)throw new Error('本组媒体不再可勾选，请重新扫描或读取最新清单。');
    selected=new Set(items.map(item=>item.id));buildFolders();for(const id of filters)$(id).value='';wallUsers.reset();$('kind').value=bundleReviewKind(info);$('risk').value='selected';if(info.media_kind==='视频')$('view-mode').value='list';page=0;$('bundle-review-dialog').close();$('notice').textContent=`已勾选本组 ${bundleReviewIds(info).length} 项${info.media_kind==='视频'?'视频':'照片'}，替换之前的勾选。请核对归属与分类目录，再纳入计划；附属文件须另行整组预览确认。`;
  }catch(err){if(revision===bundleReviewRevision){$('bundle-review-error').textContent=err.message;$('bundle-review-error').hidden=false;}}
  finally{saving=false;render();syncBundleReviewControls();}
};
function photoPreviewSource(id){const preview=photos.get(id)?.preview;return /^previews\/[A-Za-z0-9_-]+\.png$/.test(preview||'')?preview:'';}
const workspaceMediaActions = {
  formatSize: bytes => fileSize(bytes),
  owner: item => wallUsers.label([item.path]),
  open: (item, action) => openMedia(item, action),
  trash: item => previewTrashItem(item),
  bundle: item => openBundleReview(item),
  edit: item => openEditor(item),
  reset: id => changeTarget(id, null),
  preview: item => viewPictures([item]),
  removeCandidate: id => mutateBasket('remove', [id]),
  select(item, checked) {
    if (saving || operating || handledTrash.has(item.path)) return;
    if (checked) selected.add(item.id);
    else selected.delete(item.id);
    renderWorkspaceMedia();
    updateSelection();
  },
};
let updateWorkspaceMedia;
function renderWorkspaceMedia() {
  if (!updateWorkspaceMedia) updateWorkspaceMedia = mountWorkspaceMedia({table: $('rows'), wall: $('photo-wall')}, workspaceMediaActions);
  updateWorkspaceMedia(pageItems, {
    available: mediaAvailable,
    ids: mediaIds,
    names,
    photos,
    selected: new Set(selected),
    handled: handledTrash,
    copied: handledCopy,
    basket: basketIds,
    basketWritable: basketReady && !saving && !operating && !basketLoading,
    busy: saving || operating,
    saving,
  }, $('view-mode').value === 'wall');
}
function viewPictures(items,{copies=false,sequence=visible}={}){
  if(!items.length)return;pictureSession++;pictureFailures.clear();pictureCopies=copies;pictureCompare=!copies&&items.length===2;
  pictureSequence=pictureCompare?[...items]:photoBrowseSequence(sequence,items[0]);
  pictureIndex=pictureCompare?0:Math.max(0,pictureSequence.findIndex(item=>item.id===items[0].id));
  renderPictureViewer();if(!$('picture-viewer').open){$('picture-viewer').showModal();syncBrowseControls();}
}
function syncPictureViewer(){
  if(!pictureView)pictureView=mountPictureViewer($('picture-viewer'),{
    previous:()=>turnPicture(-1),next:()=>turnPicture(1),close:()=>$('picture-viewer').close(),
    previewError:(key,session)=>{if(session===pictureSession){pictureFailures.add(key);syncPictureViewer();}},
    trash:previewTrashItem,
    copy:(item,action,button)=>{
      const session=pictureSession;setPictureError('');
      return openOrganizedCopy(item,action,button,message=>{
        if(session===pictureSession&&$('picture-viewer').open)setPictureError(message);
      });
    },
    original:async(item,button)=>{
      if(saving||operating||pictureOriginalPending.has(item.id)||mediaIds[item.path]!==item.id||handledTrash.has(item.path))return;
      const session=pictureSession;pictureOriginalPending.add(item.id);setPictureError('');
      try{await openMedia(item,'open',button,message=>{
        if(session===pictureSession&&$('picture-viewer').open)setPictureError(message);
      });}
      finally{pictureOriginalPending.delete(item.id);syncPictureViewer();}
    },
  });
  const items=(pictureCompare?pictureSequence:[pictureSequence[pictureIndex]]).filter(Boolean);
  pictureView({copies:pictureCopies,compare:pictureCompare,index:pictureIndex,total:pictureSequence.length,
    session:pictureSession,error:pictureError,busy:saving||operating,
    items:items.map(item=>{
      const preview=photoPreviewSource(pictureCopies?mediaIds[item.path]:item.id),key=item.id+'\0'+preview;
      return {item,key,preview,failed:pictureFailures.has(key),size:fileSize(item.bytes),
        month:photos.get(item.id)?.month||'日期未提供',handled:handledTrash.has(item.path),
        hasOriginal:mediaIds[item.path]===item.id,canTrash:!pictureCopies&&mediaAvailable&&mediaIds[item.path]===item.id&&!handledTrash.has(item.path),
        pending:pictureCopies?(typeof organizedCopyPending==='function'&&organizedPendingActions.get(item.id)):(pictureOriginalPending.has(item.id)?'open':'')};
    }),
  });
}
function renderPictureViewer(preserveError=false){
  if(!preserveError)pictureError='';
  syncPictureViewer();
}
function turnPicture(step){if(pictureCompare)return;const next=pictureIndex+step;if(next<0||next>=pictureSequence.length)return;pictureIndex=next;pictureSession++;renderPictureViewer();}
function syncBrowseFilterSummary(){
  const summary=$('browse-options-summary');if(!summary)return;
  const count=['sort','photo-month','source-folder'].filter(id=>$(id).value).length;
  summary.textContent=count?`筛选与排序（${count} 项生效）`:'筛选与排序';
}
function syncBrowseControls(){
  syncBrowseFilterSummary();
  syncBundleReviewControls();
  if(typeof syncExecutionControls==='function')syncExecutionControls();
  for(const id of ['quick-choose','quick-preview','quick-destination','quick-save-settings','quick-all-batches','quick-correct'])$(id).disabled=saving||operating;
  if(typeof syncOrganizedCards==='function')syncOrganizedCards();
  if(typeof syncQuickIssueControls==='function')syncQuickIssueControls();
  $('reset-filters').disabled=saving;
  $('batch-folder-save').disabled=saving||operating||!folderPreview||!folderPreview.changed_count;
  if(pictureView)syncPictureViewer();
}
$('picture-viewer').addEventListener('keydown',event=>{if(pictureCompare||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName))return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();turnPicture(event.key==='ArrowLeft'?-1:1);}});
$('picture-viewer').addEventListener('close',()=>{pictureSession++;pictureSequence=[];pictureIndex=0;pictureCopies=false;pictureCompare=false;pictureFailures.clear();setPictureError('');});
$('reset-filters').onclick=()=>{if(saving)return;for(const id of filters)$(id).value='';wallUsers.reset();folderBrowser.reset();if($('view-mode').value==='wall')$('kind').value='照片';page=0;selected.clear();$('notice').textContent='已重置筛选，勾选项已清空。';render();};
function clearFolderPreview(){folderPreview=null;$('batch-folder-items').replaceChildren();$('batch-folder-summary').textContent='';$('batch-folder-save').disabled=true;}
function folderError(message){$('batch-folder-error').textContent=message||'';$('batch-folder-error').hidden=!message;}
$('batch-folder').onclick=()=>{if(saving||operating||!selected.size||selected.size>200)return;folderIds=[...selected];clearFolderPreview();folderError();const chosen=data.items.filter(item=>selected.has(item.id)),folders=new Set(chosen.map(folderOf));$('batch-folder-input').value=folders.size===1&&chosen.every(item=>item.selectable)?[...folders][0]:'';$('batch-folder-dialog').showModal();$('batch-folder-input').focus();};
$('batch-folder-input').oninput=()=>{clearFolderPreview();folderError();};
$('batch-folder-preview').onclick=async()=>{if(saving||operating)return;clearFolderPreview();folderError();saving=true;render();try{folderPreview=await request({action:'folder-preview',ids:folderIds,folder:$('batch-folder-input').value});$('batch-folder-summary').textContent=`${folderPreview.items.length} 项 · ${folderPreview.changed_count} 项位置变化 · ${folderPreview.reset_count} 项需重新核对；尚未保存`;
  for(const item of folderPreview.items){const row=make('li');row.append(make('div','path',item.path),make('div','path',item.before+' → '+item.after),make('p','minor',item.changed?'位置变化，保存后为待核对':'位置未变，保留当前状态'));$('batch-folder-items').append(row);}
}catch(err){folderError(err.message);}finally{saving=false;render();}};
$('batch-folder-save').onclick=async()=>{if(saving||operating||!folderPreview||!folderPreview.changed_count)return;const preview=folderPreview;saving=true;folderError();render();try{data=await request({action:'folder-apply',ids:folderIds,folder:preview.folder,revision:preview.revision});selected=new Set(folderIds);buildFolders();for(const id of filters)$(id).value='';$('risk').value='selected';if($('view-mode').value==='wall')$('kind').value='照片';page=0;$('batch-folder-dialog').close();$('notice').textContent=`已保存 ${preview.changed_count} 项分类调整。请核对并纳入计划，再预览复制；原件不变。`;}
catch(err){clearFolderPreview();folderError(err.message+'；请取消后读取最新进度，再重新预览。');}finally{saving=false;render();}};
$('batch-folder-cancel').onclick=()=>{$('batch-folder-dialog').close();clearFolderPreview();};
$('batch-folder-dialog').addEventListener('cancel',event=>{if(saving)event.preventDefault();});
$('select-all-current').onclick=()=>{const items=pageItems.filter(item=>!handledTrash.has(item.path)),all=items.every(item=>selected.has(item.id));for(const item of items){if(all)selected.delete(item.id);else selected.add(item.id);}render();};
$('compare').onclick=()=>viewPictures(data.items.filter(item=>selected.has(item.id)));
$('view-mode').onchange=()=>{if($('view-mode').value==='wall')$('kind').value='照片';page=0;selected.clear();render();};
function optionsFor(id,values,label){$(id).replaceChildren();const all=make('option','',label);all.value='';$(id).append(all);for(const value of [...new Set(values)].filter(Boolean).sort().slice(0,1000)){const option=make('option','',value);option.value=value;$(id).append(option);}}
async function api(route,body){const response=await fetch(route,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const result=await response.json();if(!response.ok)throw new Error(result.error||'请求失败');return result;}
async function checkDisks(){try{const result=await api('api/roots');$('disk-status').replaceChildren();for(const root of result.roots)$('disk-status').append(make('p','',`${root.status==='available'?'来源可访问':'来源不可访问（磁盘未连接、文件夹移走或权限问题）'}：${root.path}`));}catch(err){$('disk-status').textContent=err.message;}}
$('check-disks').onclick=checkDisks;
async function loadExtras(){const results=await Promise.allSettled([api('api/photos'),api('api/changes')]);if(results[0].status==='fulfilled'){const items=results[0].value.items;photos=new Map(items.map(item=>[item.id,item]));optionsFor('photo-month',items.map(item=>item.month),'全部照片年月');render();renderOrganizedResults();if($('picture-viewer').open)renderPictureViewer(true);}else error('照片清单读取失败：'+results[0].reason.message);if(results[1].status==='fulfilled'){const result=results[1].value;const labels={added:'新增',changed:'发生变化',absent:'本次未扫描到'};$('changes-list').replaceChildren();$('changes-list').append(make('p','',result.message||`对比 ${result.previous_created_at}：新增 ${result.counts.added} · 变化 ${result.counts.changed} · 本次未扫描到 ${result.counts.absent}`));for(const item of result.items.slice(0,200))$('changes-list').append(make('p','path',labels[item.state]+'：'+item.path));if(result.items.length>200)$('changes-list').append(make('p','','仅展示前 200 项。'));}else $('changes-list').textContent='比较失败：'+results[1].reason.message;checkDisks();refreshOperations();refreshBasket();}
const oldBuildFolders=buildFolders;buildFolders=function(){oldBuildFolders();optionsFor('source-folder',data.items.map(item=>item.source_folder),'全部原文件夹');};
$('choose-destination').onclick=async()=>{if(operating||saving)return;const button=$('choose-destination');button.disabled=true;try{const result=await api('api/operations/destination',{});if(result.destination)$('destination').value=result.destination;}catch(err){error(err.message);}finally{render();}};
let executionPage=0, executionKeeper='', executionRowsView, executionControlsView;
let executionConfirmed=false,executionSubmitting=false;
function syncExecutionControls(){
  if(!executionControlsView)executionControlsView=mountExecutionControls($('execution-controls'),{
    previous:()=>changeExecutionPage(-1),next:()=>changeExecutionPage(1),
    confirm:checked=>{
      if(executionSubmitting||saving||operating||!executionPreview?.token)return;
      executionConfirmed=checked;syncExecutionControls();
    },
    cancel:()=>cancelExecution(),submit:()=>submitExecution(),
  });
  const result=executionPreview,pages=Math.max(1,Math.ceil(((result?.items?.length||0)+(result?.skipped?.length||0))/100));
  executionControlsView({busy:saving||operating,submitting:executionSubmitting,confirmed:executionConfirmed,
    hasPreview:!!result,available:!!result?.token,page:executionPage,pages,
    pageLabel:`确认清单第 ${executionPage+1}/${pages} 页 · 共 ${result?.items?.length||0} 项${result?.mode==='trash'?'待清理':'复制'}、${result?.skipped?.length||0} 项未加入`});
}
function renderExecutionPreview(){
  const result=executionPreview;if(!result)return;
  const pages=Math.max(1,Math.ceil((result.items.length+(result.skipped||[]).length)/100));
  executionPage=Math.max(0,Math.min(executionPage,pages-1));
  if(!executionRowsView)executionRowsView=mountExecutionPreview($('execution-items'));
  executionRowsView({preview:result,keeper:executionKeeper,page:executionPage});
  syncExecutionControls();
}
function changeExecutionPage(change){
  if(!executionPreview||executionSubmitting||saving||operating)return;
  executionPage+=change;renderExecutionPreview();$('execution-items').scrollTop=0;
}
async function previewOperation(mode,bundle=false,photoBundle=false,quick=false,explicitIds=null,keeper=''){
  if(executionSubmitting||operating||saving||!quick&&!(explicitIds?explicitIds.length:selected.size))return;
  saving=true;error();$('notice').textContent='正在检查文件和目标位置…';render();
  try{
    executionPreview=await api(quick?'api/operations/quick-preview':'api/operations/preview',quick?{destination:$('quick-destination').value.trim(),all_batches:$('quick-all-batches').checked}:{mode,bundle,photo_bundle:photoBundle,ids:explicitIds||[...selected],destination:$('destination').value.trim()});
    $('execution-help').textContent=mode==='trash'?'只将清单中的文件移到系统废纸篓 / 回收站，可恢复。其他文件保持原位置；若中途失败，停止后续项，请查看操作记录。':'只复制清单中的文件并保留原件；若中途失败，保留已成功项并停止后续项，请查看操作记录。';
    $('execution-title').textContent=quick?'确认省心整理（复制并保留原件）':photoBundle?'确认照片和配套文件整组复制':bundle?'确认影片和附件整组复制':mode==='copy'?'确认复制到分类目录':'确认移到废纸篓 / 回收站';
    $('execution-summary').textContent=`${executionPreview.items.length} 个文件 · ${fileSize(executionPreview.bytes)} · ${mode==='copy'?'复制后保留原件，目标副本使用 SHA-256 校验':'移到系统废纸篓 / 回收站，可通过文件管理器恢复'}`;
    executionKeeper=keeper;executionPage=0;executionConfirmed=false;renderExecutionPreview();
    if(quick&&typeof updateQuickIssues==='function')updateQuickIssues(executionPreview);
    $('execution').showModal();$('notice').textContent='检查完成，请核对整份清单；可翻页查看全部确认项。';
  }catch(err){executionPreview=null;error(err.message);$('notice').textContent='';}finally{saving=false;render();syncExecutionControls();}
}
$('copy-files').onclick=()=>previewOperation('copy');$('trash-files').onclick=()=>previewOperation('trash');
$('copy-bundles').onclick=()=>previewOperation('copy',true);
$('copy-photo-bundles').onclick=()=>previewOperation('copy',false,true);
$('quick-preview').onclick=()=>previewOperation('copy',false,false,true);
$('quick-choose').onclick=async()=>{if(operating||saving)return;saving=true;render();try{const result=await api('api/operations/destination',{});if(result.destination)$('quick-destination').value=result.destination;}catch(err){error(err.message);}finally{saving=false;render();}};
function notifyReview(type,detail){if(typeof CustomEvent==='function'&&typeof window!=='undefined'&&typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent(type,{detail}));}
function cancelPreview(){
  const mode=executionPreview?.mode;executionPreview=null;executionConfirmed=false;executionPage=0;
  $('notice').textContent='已取消预览，未执行文件操作。';syncExecutionControls();
  if(mode)notifyReview('media-review-cancelled',{mode});
}
function cancelExecution(){
  if(executionSubmitting)return;
  cancelPreview();$('execution').close();
}
$('execution').addEventListener('cancel',event=>{event.preventDefault();cancelExecution();});
// Keep the submitting dialog open when native Escape is pressed repeatedly.
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&executionSubmitting&&$('execution').open)event.preventDefault();
},true);
let submittedOperationId='';
async function submitExecution(){
  if(!executionPreview?.token||!executionConfirmed||executionSubmitting||operating||saving)return;
  executionSubmitting=true;saving=true;operating=true;monitorRevision++;clearTimeout(operationTimer);render();
  syncExecutionControls();
  try{
    const started=await api('api/operations/start',{token:executionPreview.token});submittedOperationId=started.id;
    selected.clear();if($('risk').value==='selected')$('risk').value='';$('notice').textContent='操作已开始，请保持本机服务运行，完成后重新扫描。';
  }catch(err){
    error('启动请求未确认：'+err.message+'。正在读取操作记录，请勿重复执行。');
  }finally{
    executionPreview=null;executionConfirmed=false;executionSubmitting=false;$('execution').close();saving=false;operating=true;
    syncExecutionControls();refreshOperations();
  }
}
function activeOperation(job){return ['running','external_running'].includes(job.status);}
const operationViews=new Map();
function operationItemState(job,item){
  if(item.status==='processing')return activeOperation(job)?'processing':'attention';
  return ['failed','unknown'].includes(item.status)?'attention':item.status;
}
function operationItemText(job,item){
  const labels={success:'成功',cancelled:'已取消，原件保留',pending:activeOperation(job)?'尚未开始':'未处理（原位置保留）',failed:'失败',unknown:'结果未确认'};
  const label=item.status==='processing'?(activeOperation(job)?`正在${({copying:'复制',verifying:'校验副本',verified:'完成校验'})[item.phase]||'处理'}${job.mode==='copy'?'（'+fileSize(item.processed_bytes||0)+' / '+fileSize(item.bytes||0)+'）':''}`:'处理结果待确认'):labels[item.status]||'结果待核对';
  return `${label}：${item.role?'['+item.role+'] ':''}${item.path}\n→ ${item.trashed_path||item.target}${item.error?'\n'+item.error:''}`;
}
function renderOperationView(view){
  const query=view.query.trim().toLocaleLowerCase(),status=view.filterStatus;
  const rows=view.records.filter(item=>(!status||operationItemState(view.job,item)===status)&&(!query||(item.path+' '+item.target+' '+(item.trashed_path||'')+' '+(item.error||'')).toLocaleLowerCase().includes(query)));
  const pages=Math.max(1,Math.ceil(rows.length/50));view.page=Math.max(0,Math.min(view.page,pages-1));
  view.pages=pages;
  view.renderer.render({id:view.job.id,job:view.job,counts:view.counts,active:activeOperation(view.job),
    current:view.current,canReview:view.canReview,busy:operating||saving,stopping:!!view.stopping,
    recovering:!!view.recovering,recovery:view.recovery,recoveryPage:view.recoveryPage,
    rows:rows.slice(view.page*50,(view.page+1)*50).map(item=>({path:item.path,
      attention:operationItemState(view.job,item)==='attention',text:operationItemText(view.job,item)})),
    query:view.query,filter:view.filterStatus,page:view.page,pages,total:view.records.length,matching:rows.length});
}
function operationCard(job){
  let view=operationViews.get(job.id);
  if(!view){
    view={page:0,query:'',filterStatus:'',recoveryPage:0,box:make('div','operation')};
    view.renderer=mountOperationCard(view.box,{
      search:value=>{view.query=value;view.page=0;renderOperationView(view);},
      filter:value=>{view.filterStatus=value;view.page=0;renderOperationView(view);},
      previous:()=>{if(view.page>0){view.page--;renderOperationView(view);}},
      next:()=>{if(view.page+1<view.pages){view.page++;renderOperationView(view);}},
      attention:()=>{view.filterStatus='attention';view.query='';view.page=0;renderOperationView(view);},
      stop:async()=>{
        if(view.stopping||view.job.stop_requested||!activeOperation(view.job))return;
        view.stopping=true;operationCard(view.job);
        try{await stopOperation(view.job.id);}
        finally{view.stopping=false;if(operationViews.get(view.job.id)===view)operationCard(view.job);}
      },
      review:()=>reviewRemaining(view.job.id),
      previousRecovery:()=>{if(view.recoveryPage>0){view.recoveryPage--;renderOperationView(view);}},
      nextRecovery:()=>{if((view.recoveryPage+1)*50<(view.recovery?.advice?.skipped.length||0)){view.recoveryPage++;renderOperationView(view);}},
    });
    operationViews.set(job.id,view);
  }
  view.job=job;
  const active=activeOperation(job);
  const started=new Set(job.items.map(item=>item.path)),pending=(job.planned_items||[]).filter(item=>!started.has(item.path));
  view.records=[...job.items,...pending.map(item=>({...item,status:'pending'}))];
  const counts={success:0,attention:0,processing:0,pending:0,cancelled:0};for(const item of view.records)counts[operationItemState(job,item)]++;
  view.counts=counts;
  const current=active?job.items.find(item=>item.status==='processing'):null;
  view.current=current?operationItemText(job,current):'';
  view.canReview=!job.bundle&&!job.quick&&['cancelled','stopped'].includes(job.status)&&!job.error&&!!job.planned_items&&!!(pending.length||counts.cancelled);
  renderOperationView(view);return view.box;
}
function renderOperationCards(result){
  const log=$('operation-log');if(log&&((result.warnings||[]).length||result.jobs.some(activeOperation)))log.open=true;
  const parent=$('operations'),keys=new Set(result.jobs.map(job=>job.id));
  for(const [key,view] of operationViews)if(!keys.has(key)){
    view.renderer.dispose();operationViews.delete(key);
  }
  const desired=[...(result.warnings||[]).map(warning=>make('p','warning',warning)),...result.jobs.map(operationCard)];
  if(!desired.length)desired.push(make('p','','本次扫描还没有文件操作记录。'));
  for(const child of [...parent.children])if(!desired.includes(child))child.remove();
  desired.forEach((child,index)=>{if(parent.children[index]!==child)parent.insertBefore(child,parent.children[index]||null);});
}
async function reviewRemaining(id){
  const view=operationViews.get(id);
  if(operating||saving||!data||!view||!view.canReview||view.recovering)return;
  let showSelection=false;
  saving=true;view.recovering=true;view.recovery=null;view.recoveryPage=0;error();render();operationCard(view.job);
  try{
    const advice=await api('api/operations/'+id+'/remaining');
    if(operationViews.get(id)!==view||!view.canReview||operating)return;
    selected=new Set(advice.ids);
    showSelection=advice.ids.length>0;
    for(const filter of filters)$(filter).value='';
    $('risk').value='selected';$('view-mode').value='list';page=0;
    view.recovery={advice};
    $('notice').textContent=advice.ids.length?`已勾选 ${advice.ids.length} 个未处理文件。请核对分类位置与目标文件夹，再重新预览确认。`:'没有可直接继续核对的文件，请查看此批次的跳过原因。';
  }catch(err){if(operationViews.get(id)===view){view.recovery={error:err.message};error(err.message);}}
  finally{saving=false;view.recovering=false;render();if(operationViews.get(id)===view){operationCard(view.job);if(showSelection)$('selection').scrollIntoView({block:'start'});}}
}
async function stopOperation(id){
  try{
    await api('api/operations/stop',{id});
    $('notice').textContent='已请求安全停止，请等待逐项结果；已完成项保留。';
  }catch(err){error('停止请求未确认，请查看进度后重试：'+err.message);}
  await refreshOperations();
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
    renderOperationCards(result);
    if(wasRetrying)error();
    if(result.jobs.some(activeOperation)){
      $('notice').textContent=result.jobs.some(job=>activeOperation(job)&&job.stop_requested)?'正在安全停止，请保持本机服务运行，等待当前文件结果。':'文件操作执行中，进度会自动更新。请保持本机服务运行；完成前暂停修改计划。';
      operationTimer=setTimeout(refreshOperations,1000);
    }else if((result.warnings||[]).length){
      $('notice').textContent='部分操作记录无法读取，暂时锁定文件操作。请先核对记录与 文件管理器，再点击“读取操作记录”。';
    }else if(wasBusy&&result.jobs.length){
      if(typeof refreshOrganizedResults==='function')refreshOrganizedResults();
      $('notice').textContent=result.jobs[0].status==='complete'?'操作完成，请重新扫描更新文件清单。':result.jobs[0].status==='cancelled'?'已安全停止，已成功项保留。请查看未处理文件清单，重新选择后预览。':'操作已停止，请查看逐项结果；成功项保留，未确认项先在 文件管理器 核对。';
    }else if(wasRetrying){$('notice').textContent='已恢复连接，没有正在执行的文件操作。';}
    render();
    const submitted=result.jobs.find(job=>job.id===submittedOperationId);
    if(submitted&&!activeOperation(submitted)){
      submittedOperationId='';
      notifyReview('media-operation-finished',{status:submitted.status,mode:submitted.mode});
    }
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

// Source-scoped preferences, report-only exceptions and successful-copy browser.
let quickSettings=null,organizedItems=[],organizedNode='',organizedPage=0,organizedFolderPage=0;
async function loadQuickSettings(){
  try{quickSettings=await api('api/organize-settings');$('quick-destination').value=quickSettings.destination;$('quick-all-batches').checked=quickSettings.all_batches;$('quick-settings-status').textContent=quickSettings.destination?'已读取当前扫描来源的常用设置。':'设置按扫描来源保存，换来源时分别记住。';}
  catch(err){$('quick-settings-status').textContent='设置未读取：'+err.message;}
}
$('quick-save-settings').onclick=async()=>{
  if(saving||operating)return;saving=true;render();
  try{if(!quickSettings)quickSettings=await api('api/organize-settings');quickSettings=await api('api/organize-settings',{action:'preferences',revision:quickSettings.revision,destination:$('quick-destination').value.trim(),all_batches:$('quick-all-batches').checked});$('quick-settings-status').textContent='已记住当前来源的整理设置；每次执行仍会重新校验目标。';}
  catch(err){error(err.message);}finally{saving=false;render();}
};
$('quick-correct').onclick=async()=>{
  if(saving||operating)return;
  const problemPaths = quickIssuePanel.selectedPaths();
  const ids = problemPaths.length ? problemPaths.map(path => mediaIds[path]).filter(Boolean) : [...selected];
  if(!ids.length||ids.length>200){error('请勾选 1–200 个媒体或问题项；附件需核对其主媒体。');return;}
  const field=$('quick-correction-field').value,value=field==='region'?$('quick-region').value:field==='category'?$('quick-category').value:$('quick-month').value.trim().replace('-','/');
  saving=true;render();try{if(!quickSettings)quickSettings=await api('api/organize-settings');quickSettings=await api('api/organize-settings',{action:'correct',revision:quickSettings.revision,ids,field,value});executionPreview=null;quickIssuePanel.clearSelection('分类修正已保存，问题勾选已清空；请重新预览更新清单。');$('quick-settings-status').textContent='分类修正已保存，原件未修改。请重新生成整理预览。';}
  catch(err){error(err.message);}finally{saving=false;render();}
};
var quickIssuePanel = globalThis.mountIssueList($('quick-issue-panel'), error);
function updateQuickIssues(preview) {
  quickIssuePanel.replace(preview, data?.items || []);
}
function syncQuickIssueControls() {
  const busy = saving || operating;
  quickIssuePanel?.updateControls(busy, mediaIds);
  for (const id of ['quick-correction-field', 'quick-region', 'quick-month', 'quick-category']) {
    $(id).disabled = busy;
  }
}
function syncQuickCorrectionField(){const field=$('quick-correction-field').value;for(const[id,expected]of [['quick-region','region'],['quick-month','month'],['quick-category','category']])$(id).hidden=field!==expected;}
$('quick-correction-field').onchange=syncQuickCorrectionField;syncQuickCorrectionField();
async function openOrganizedCopy(item,action,button,onError=error){
  if(operating||saving||organizedCopyPending(item.id)||!item.can_open)return;
  organizedPendingActions.set(item.id,action);
  button.copyPending=true;button.disabled=true;
  const updateControls=typeof syncBrowseControls==='function'?syncBrowseControls:syncOrganizedCards;
  updateControls();
  try{await api('api/organized-results/action',{id:item.id,action});}
  catch(err){if(button.isConnected!==false||organizedCardsState?.items.some(current=>current.id===item.id))onError(err.message);}
  finally{
    organizedPendingActions.delete(item.id);button.copyPending=false;button.disabled=operating||saving;
    updateControls();
  }
}
const organizedPendingActions=new Map();
let organizedRefreshRevision=0,organizedNavigationView,organizedCardsView,organizedCardsState;
function organizedCopyPending(id){return organizedPendingActions.has(id);}
function syncOrganizedCards(){
  if(!organizedCardsState)return;
  if(!organizedCardsView)organizedCardsView=mountOrganizedCopies($('organized-items'),{
    open:openOrganizedCopy,
    preview:item=>{if(!saving&&!operating)viewPictures([item],{copies:true,sequence:organizedCardsState.sequence});},
  });
  organizedCardsView({...organizedCardsState,busy:saving||operating,pending:organizedPendingActions,formatSize:fileSize,
    items:organizedCardsState.items.map(item=>({...item,preview:photoPreviewSource(mediaIds[item.path])}))});
}
async function refreshOrganizedResults(){
  const revision=++organizedRefreshRevision,button=$('organized-refresh');
  button.disabled=true;button.textContent='正在读取…';
  $('organized-message').textContent='正在读取整理结果，可继续浏览已有清单…';
  try{
    const result=await api('api/organized-results');
    if(revision!==organizedRefreshRevision)return;
    organizedItems=result.items;
    $('organized-message').textContent=result.message+' 缩略图沿用扫描预览。';
    renderOrganizedResults();
  }catch(err){
    if(revision===organizedRefreshRevision)$('organized-message').textContent='整理结果暂时无法读取：'+err.message+'。可以重试。';
  }finally{
    if(revision===organizedRefreshRevision){button.disabled=false;button.textContent='刷新整理结果';}
  }
}
function navigateOrganizedResults(key){
  organizedNode=key;organizedPage=organizedFolderPage=0;$('organized-search').value='';renderOrganizedResults();
}
function renderOrganizedResults(){
  const view=organizedResultView(organizedItems,{key:organizedNode,kind:$('organized-kind').value,query:$('organized-search').value,order:$('organized-sort').value,descendants:$('organized-descendants').checked});
  const {active:node,folders,rows,ancestors}=view;organizedNode=node.key;
  const folderPages=Math.max(1,Math.ceil(folders.length/30));organizedFolderPage=Math.max(0,Math.min(organizedFolderPage,folderPages-1));
  if(!organizedNavigationView)organizedNavigationView=mountOrganizedNavigation($('organized-breadcrumbs'),$('organized-folders'),navigateOrganizedResults);
  organizedNavigationView({ancestors,currentKey:node.key,
    folders:folders.slice(organizedFolderPage*30,organizedFolderPage*30+30),formatSize:fileSize});
  $('organized-folder-page').textContent=`当前类型的子目录 ${folders.length} 个 · 第 ${organizedFolderPage+1}/${folderPages} 页`;
  $('organized-folder-previous').disabled=organizedFolderPage===0;$('organized-folder-next').disabled=organizedFolderPage>=folderPages-1;
  const pages=Math.max(1,Math.ceil(rows.length/50));organizedPage=Math.max(0,Math.min(organizedPage,pages-1));
  organizedCardsState={items:rows.slice(organizedPage*50,(organizedPage+1)*50),sequence:rows,
    emptyMessage:!organizedItems.length?'还没有成功复制记录，完成整理后点击“刷新整理结果”。':'当前范围没有匹配的副本，可清空搜索、切换类型或勾选包含子文件夹。'};
  syncOrganizedCards();
  const scope=$('organized-search').value.trim()?'搜索当前目录及子目录':!node.key?'全部目录':$('organized-descendants').checked?'当前目录及子目录':'仅当前目录';
  $('organized-summary').textContent=(node.path||'全部整理结果')+` · 目录内共 ${node.count} 项 · ${scope}：${rows.length} 项 · 照片 ${view.photos} · 视频 ${view.videos} · 附件 ${rows.length-view.photos-view.videos} · ${fileSize(view.bytes)}（逻辑大小）`;
  $('organized-page').textContent=`结果第 ${organizedPage+1}/${pages} 页 · 每页最多 50 项`;
  $('organized-up').disabled=!node.key;$('organized-descendants').disabled=!node.key;$('organized-previous').disabled=organizedPage===0;$('organized-next').disabled=organizedPage>=pages-1;
  $('organized-up').onclick=()=>navigateOrganizedResults(node.parent||'');
}
$('organized-refresh').onclick=refreshOrganizedResults;$('organized-root').onclick=()=>navigateOrganizedResults('');
$('organized-search').oninput=$('organized-kind').onchange=$('organized-sort').onchange=$('organized-descendants').onchange=()=>{organizedPage=organizedFolderPage=0;renderOrganizedResults();};
$('organized-reset').onclick=()=>{$('organized-kind').value='';$('organized-sort').value='name';$('organized-descendants').checked=true;navigateOrganizedResults('');};
$('organized-previous').onclick=()=>{organizedPage--;renderOrganizedResults();};$('organized-next').onclick=()=>{organizedPage++;renderOrganizedResults();};
$('organized-folder-previous').onclick=()=>{organizedFolderPage--;renderOrganizedResults();};$('organized-folder-next').onclick=()=>{organizedFolderPage++;renderOrganizedResults();};
// Initialize the organizing workspace once all controls are wired.
syncExecutionControls();
loadQuickSettings();
refreshOrganizedResults();
syncQuickIssueControls();
