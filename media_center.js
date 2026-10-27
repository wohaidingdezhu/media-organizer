// Shared cards and detail navigation. Browsing does not modify original media.
function centerYear(item){const value=String(item.metadata.year||item.metadata.date_original||item.metadata.date||item.metadata.date_taken||'');return /^\d{4}(?:$|[-:/ ])/.test(value)?value.slice(0,4):'';}
function centerFolderIds(directories,path,recursive=false){
  const ids=new Set(),visited=new Set(),pending=[path];
  while(pending.length){const key=pending.pop(),node=directories[key];if(!node||visited.has(key))continue;visited.add(key);for(const id of node.ids)ids.add(id);if(recursive)for(const child of node.children)pending.push(child);}return ids;
}
function centerMatches(item,query){return !query||[item.title,...item.files.map(file=>file.path),...item.tags,JSON.stringify(item.metadata),item.personal.note].join(' ').normalize('NFC').toLocaleLowerCase().includes(query);}
function centerDuration(seconds){if(!Number.isFinite(seconds)||seconds<=0)return '时长未知';const total=Math.max(1,Math.round(seconds)),h=Math.floor(total/3600),m=Math.floor(total%3600/60),sec=String(total%60).padStart(2,'0');return h?h+':'+String(m).padStart(2,'0')+':'+sec:m+':'+sec;}
function centerVideoText(file){const v=file.video||{};return centerDuration(v.duration_seconds)+' · '+(v.width&&v.height?v.width+' × '+v.height:'尺寸未知');}
function centerVideoMatch(file,duration,resolution){const v=file.video||{},d=v.duration_seconds,edge=Math.min(v.width||0,v.height||0),known=Number.isFinite(d)&&d>0;
  return (!duration||(duration==='unknown'?!known:known&&(duration==='short'?d<600:duration==='medium'?d>=600&&d<3600:duration==='long'?d>=3600:false)))&&(!resolution||(resolution==='unknown'?!edge:resolution==='uhd'?edge>=2160:resolution==='fhd'?edge>=1080&&edge<2160:resolution==='hd'?edge>=720&&edge<1080:resolution==='sd'?edge>0&&edge<720:false));
}
function centerFilter(items,{view='home',query='',global=false,kind='',tag='',ids=null,folder='',order='recent',year='',genre='',duration='',resolution=''}={}){
  const q=query.trim().normalize('NFC').toLocaleLowerCase();
  let rows=items.filter(item=>(!kind||item.kind===kind)&&(!tag||item.tags.includes(tag))&&(!ids||ids.has(item.id))&&(!folder||item.folder===folder)&&(!year||centerYear(item)===year)&&(!genre||(item.metadata.genres||[]).includes(genre))&&
    (global?true:view==='movies'?item.kind==='movie':view==='videos'?item.kind==='video':view==='photos'?item.kind==='photo':view==='favorites'?item.favorite:true)&&centerMatches(item,q)&&(!(duration||resolution)||item.kind!=='photo'&&item.files.some(file=>centerVideoMatch(file,duration,resolution))));
  const name=(a,b)=>a.title.localeCompare(b.title,'zh-CN',{numeric:true})||a.id.localeCompare(b.id);
  return [...rows].sort((a,b)=>order==='name'?name(a,b):order==='size'?b.bytes-a.bytes||name(a,b):order==='oldest'?a.mtime-b.mtime||name(a,b):b.mtime-a.mtime||name(a,b));
}
function centerTimelineOrder(rows,order='recent'){
  return [...rows].sort((a,b)=>{const first=a.metadata.timeline_month||'',second=b.metadata.timeline_month||'';if(!first||!second)return first?-1:second?1:0;return order==='oldest'?first.localeCompare(second):second.localeCompare(first);});
}
function centerReviewGroups(review,{mode='exact',query='',kind='',year='',genre='',order='recent',duration='',resolution='',owners=new Map()}={}){
  const q=query.trim().normalize('NFC').toLocaleLowerCase();
  const rows=(review?.[mode]||[]).filter(group=>group.files.some(file=>{const owner=owners.get(file.owner);return (!(duration||resolution)||file.kind!=='photo'&&centerVideoMatch(file,duration,resolution))&&(!kind||file.media_kind===kind)&&(!year||centerYear(file)===year)&&(!genre||(file.metadata.genres||[]).includes(genre))&&centerMatches({title:owner?.title||file.name,files:[file],tags:owner?.tags||[],metadata:file.metadata||{},personal:owner?.personal||{note:''}},q);}));
  const name=(a,b)=>(a.files[0]?.name||'').localeCompare(b.files[0]?.name||'','zh-CN',{numeric:true})||(a.files[0]?.id||'').localeCompare(b.files[0]?.id||'');
  const stamp=(group,oldest=false)=>group.files.reduce((value,file)=>oldest?Math.min(value,file.mtime||0):Math.max(value,file.mtime||0),oldest?Infinity:0);
  const bytes=group=>group.files.reduce((value,file)=>Math.max(value,file.bytes||0),0);
  return rows.sort((a,b)=>order==='name'?name(a,b):order==='size'?bytes(b)-bytes(a)||name(a,b):order==='oldest'?stamp(a,true)-stamp(b,true)||name(a,b):stamp(b)-stamp(a)||name(a,b));
}
function centerMediaIds(items){return [...new Set(items.flatMap(item=>item.files.map(file=>file.id)))];}
function centerArt(path){return typeof path==='string'&&/^(previews|covers|stills)\/[A-Za-z0-9_-]+\.png$/.test(path)?path:'';}
if(typeof module!=='undefined'&&module.exports)module.exports={centerTimelineOrder,centerFilter,centerMediaIds,centerArt,centerYear,centerFolderIds,centerReviewGroups,centerDuration,centerVideoText,centerVideoMatch};
if(typeof document!=='undefined'){
if(window.parent!==window)document.body.classList.add('embedded-center');
const $=id=>document.getElementById(id),make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
const titles={home:'媒体中心',movies:'电影',videos:'视频',photos:'照片',collections:'相册与合集',favorites:'收藏',tags:'标签与分类',resources:'资源管理',review:'重复与相似'};
let catalog=null,byId=new Map(),mediaAccess={ids_by_path:{},available:false},view='home',page=0,manage=false,busy=false,selected=new Set(),visible=[],pageItems=[],album='',collection='',tag='',editingCollection='',detailSequence=[],detailIndex=0,loadTicket=0,loadsInFlight=0,directory='',folderPage=0,reviewMode='exact',showDismissedSimilar=false,byFile=new Map(),reviewSelected=new Set(),reviewInitialized=false,reviewFeedback='',centerOperationTimer=null,centerStale=false,detailBindings=null,detailEpoch=0,collectionEpoch=0,detailOriginKey='',availabilityPending=false,availabilityPolling=false,centerStatusBase='';
const size=value=>value>=1073741824?(value/1073741824).toFixed(1)+' GB':value>=1048576?(value/1048576).toFixed(1)+' MB':(value/1024).toFixed(0)+' KB';
const labels={movie:'电影',video:'视频',photo:'照片'};
const galleryPageSize=48; // Full rows in common 2-, 3-, 4- and 6-column layouts.
function message(value,kind='error'){const node=$('center-error');node.textContent=value||'';node.className='notice'+(value&&kind==='success'?' success':'');node.setAttribute('role',value&&kind==='success'?'status':'alert');}
function globalSearch(){return !!$('query').value.trim()&&$('search-scope').value==='all';}
async function request(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=Error(result.error||'操作未完成');error.status=response.status;throw error;}return result;}
function autoRefreshSafe(){const active=document.activeElement;return !busy&&!manage&&!selected.size&&!reviewSelected.size&&!catalog?.operation_state?.running&&!document.querySelector?.('dialog[open]')&&!$('media-detail').open&&!$('collection-form').open&&!(active&&(active.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(active.tagName)))&&document.visibilityState!=='hidden';}
function availabilityNotice(){const text=[centerStatusBase,availabilityPending?'检测到媒体文件变化；当前选择或编辑会保留，完成后将自动更新列表。':''].filter(Boolean).join(' ');$('center-status').hidden=!text;$('center-status').textContent=text;}
async function load(automatic=false){
  clearTimeout(centerOperationTimer);const ticket=++loadTicket;loadsInFlight++;
  try{const [data,access]=await Promise.all([request('api/center'),request('api/media')]);if(ticket!==loadTicket)return null;if(automatic===true&&!autoRefreshSafe()){availabilityPending=true;availabilityNotice();return null;}
    centerStale=false;availabilityPending=false;catalog=data;byId=new Map(data.items.map(item=>[item.id,item]));byFile=new Map(data.items.flatMap(item=>item.files.map(file=>[file.id,{...file,kind:item.kind}])));mediaAccess=access;for(const id of reviewSelected)if(!byFile.has(id))reviewSelected.delete(id);
    if(directory&&!data.directories?.[directory])directory='';fillFilters();for(const id of selected)if(!byId.has(id))selected.delete(id);
    const scroll=[window.scrollX||0,window.scrollY||0];message();render();if(typeof window.scrollTo==='function')window.scrollTo(...scroll);
    if($('media-detail').open){const current=detailSequence[detailIndex]?.id;detailSequence=detailSequence.map(item=>byId.get(item.id)).filter(Boolean);detailIndex=detailSequence.findIndex(item=>item.id===current);if(detailIndex<0)$('media-detail').close();else refreshDetail(current);}
    if(data.operation_state?.running)centerOperationTimer=setTimeout(load,1000);return true;
  }catch(error){if(ticket===loadTicket){if(automatic===true){availabilityPending=true;availabilityNotice();return false;}centerStale=true;if(catalog)render();message(error.message);$('center-subtitle').textContent='资料库暂时无法读取，请点击刷新重试。';if(catalog?.operation_state?.running)centerOperationTimer=setTimeout(load,3000);return false;}return null;}finally{loadsInFlight--;}
}
async function pollAvailability(){
  if(availabilityPolling||loadsInFlight||!catalog||document.visibilityState==='hidden')return;availabilityPolling=true;
  try{const state=await request('api/media/availability');if(!Number.isInteger(state.revision)||!Number.isInteger(catalog.availability_revision))return;
    if(state.revision===catalog.availability_revision){if(availabilityPending){availabilityPending=false;availabilityNotice();}return;}
    if(loadsInFlight||!autoRefreshSafe()){availabilityPending=true;availabilityNotice();return;}
    await load(true);
  }catch(_error){/* A temporary monitor failure must not interrupt browsing. */}finally{availabilityPolling=false;}
}
if(typeof setInterval==='function')setInterval(pollAvailability,10000);
function canTrash(ids){return !busy&&!centerStale&&mediaAccess.available&&!catalog?.restored&&!catalog?.operation_state?.running&&!catalog?.operation_state?.warnings?.length&&ids.length>0&&ids.length<=200&&ids.every(id=>{const file=byFile.get(id);return file&&mediaAccess.ids_by_path[file.path]===id;});}
function openFileReview(action,ids,summary=''){
  if(busy)return;if(action==='delete'&&!canTrash(ids))return;if(action==='delete'&&$('media-detail').open)$('media-detail').close();
  if(action==='compare'&&(ids.length!==2||ids.some(id=>byFile.get(id)?.kind!=='photo')))return;
  reportActionOpen(action,ids,load,summary);
}
const pendingMediaActions=new Set();
function canOpenFile(file,action='open'){return !busy&&!centerStale&&mediaAccess.available&&!catalog.restored&&mediaAccess.ids_by_path[file.path]===file.id&&!pendingMediaActions.has(action+':'+file.id);}
async function performMediaAction(item,file,action='open'){
  if(!canOpenFile(file,action))return;
  const key=action+':'+file.id,context=detailContext();pendingMediaActions.add(key);syncAvailability();
  const notice=text=>{message(text);detailNotice(context,'detail-status',text);};
  try{
    await request('api/media/action',{id:file.id,action});
    if(action==='open'){
      try{await request('api/center',{action:'recent',id:item.id});}
      catch(error){notice('已打开，最近记录未保存：'+error.message);return;}
      if(await load()===false)notice('已打开，资料库刷新失败，请稍后刷新。');
    }
  }catch(error){notice(error.message);}
  finally{pendingMediaActions.delete(key);syncAvailability();}
}
function openOriginal(item,file){return performMediaAction(item,file);}
function detailContext(){return {id:detailSequence[detailIndex]?.id,epoch:detailEpoch};}
function currentDetail(context){return $('media-detail').open&&context.epoch===detailEpoch&&detailSequence[detailIndex]?.id===context.id;}
function detailNotice(context,node,text){if(currentDetail(context))$(node).textContent=text;}
async function reloadAfterSave(context){const refreshed=await load();if(refreshed)return true;if(refreshed===null)return false;const text='修改已保存，但资料库刷新失败，请刷新后核对。';message(text);detailNotice(context,'detail-status',text);return false;}
async function update(payload,onSaved=null){const context=detailContext();if(busy)return false;busy=true;syncSelection();$('detail-feedback').textContent=$('detail-status').textContent='';try{const saved=await request('api/center',payload);if(onSaved)onSaved(saved);return await reloadAfterSave(context);}catch(error){message(error.message);detailNotice(context,'detail-status',error.message);return false;}finally{busy=false;syncSelection();}}
let previewObserver=null,previewQueue=[],previewWorking=false,previewDone=0,previewFailed=0,previewTotal=0;
const previewWork=new Map();
function previewStatus(){const jobs=[...previewWork.values()];previewTotal=jobs.length;previewDone=jobs.filter(job=>job.state!=='queued').length;previewFailed=jobs.filter(job=>job.state==='failed').length;const active=previewWorking||previewQueue.length;const label=$('preview-progress-label'),meter=$('preview-progress');label.hidden=meter.hidden=!previewTotal;label.textContent=active?'正在补充预览 '+previewDone+'/'+previewTotal+'（'+Math.floor(previewDone*100/previewTotal)+'%）':'预览处理完成 '+previewDone+'/'+previewTotal+(previewFailed?' · '+previewFailed+' 项未生成，可点击补预览重试':'');meter.value=previewTotal?previewDone*100/previewTotal:0;}
function previewFile(item){return item.files?.[0]||byFile.get(item.id);}
function previewAllowed(item){const file=previewFile(item);return file&&mediaAccess.available&&!catalog.restored&&mediaAccess.ids_by_path[file.path]===file.id;}
function previewUnavailable(item){return catalog.restored?'备份中没有预览，需重新扫描本机原文件':!mediaAccess.available?'原文件暂时无法访问，请检查磁盘连接后刷新':'原文件不在当前可访问列表，请重新扫描来源目录';}
function previewPlaceholder(item,parent,text){parent.replaceChildren();const block=make('div','placeholder');block.append(make('span','',item.kind==='photo'?'▧':'▷'),make('small','',text));parent.append(block);}
function previewControl(parent,state){const control=parent.previewRepairControl;if(!control)return;control.hidden=state==='done';control.disabled=!control.previewAllowed||state==='queued';control.textContent=state==='queued'?'正在生成…':state==='failed'?'重试补预览':control.previewLabel;}
function queuePreview(item,parent,manual=false){
  if(!previewAllowed(item))return Promise.resolve(false);
  const file=previewFile(item);let job=previewWork.get(file.id);
  if(job?.state==='done'){parent.replaceChildren();image({...item,preview:job.preview},parent);previewControl(parent,'done');return Promise.resolve(true);}
  if(job?.state==='failed'&&!manual){previewPlaceholder(item,parent,'预览未生成：'+job.error);previewControl(parent,'failed');return Promise.resolve(false);}
  if(!job||job.state==='failed'){
    job={id:file.id,state:'queued',targets:[]};job.promise=new Promise(resolve=>{job.resolve=resolve;});previewWork.set(file.id,job);if(manual)previewQueue.unshift(job);else previewQueue.push(job);
  }
  if(!job.targets.some(target=>target.parent===parent))job.targets.push({item,parent});
  previewControl(parent,'queued');previewPlaceholder(item,parent,'正在排队生成预览…');previewStatus();runPreviewQueue();return job.promise;
}
function observePreview(item,parent){
  if(!previewAllowed(item)){if(parent.previewFallback?.())return;previewPlaceholder(item,parent,previewUnavailable(item));return;}
  if(catalog.preview_policy?.[item.kind==='photo'?'images':'videos']===false){if(parent.previewFallback?.())return;previewPlaceholder(item,parent,'本次未启用解析，可点击补预览单独生成');return;}
  const previous=previewWork.get(previewFile(item).id);if(previous?.state==='failed'){if(parent.previewFallback?.())return;previewPlaceholder(item,parent,'预览未生成：'+previous.error);previewControl(parent,'failed');return;}
  if(typeof IntersectionObserver==='undefined'){Promise.resolve().then(()=>{if(parent.isConnected!==false)queuePreview(item,parent);});return;}
  if(!previewObserver)previewObserver=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){previewObserver.unobserve(entry.target);if(entry.target.isConnected!==false)entry.target.previewEnqueue?.();}},{rootMargin:'160px'});
  parent.previewEnqueue=()=>queuePreview(item,parent);previewObserver.observe(parent);
}
async function requestPreview(id){
  for(let attempt=0;attempt<2;attempt++){
    try{return await request('api/media/cover',{id});}
    catch(error){const temporary=error.name==='TypeError'||error.status>=500||error.message==='正在补充另一张预览，请稍候再试';if(!temporary||attempt)throw error;await new Promise(resolve=>setTimeout(resolve,500));}
  }
}
async function runPreviewQueue(){
  if(previewWorking)return;previewWorking=true;
  try{while(previewQueue.length){
    const job=previewQueue.shift();job.targets=job.targets.filter(target=>target.parent.isConnected!==false);
    // Navigation may remove a queued card before decoding starts. Leave it
    // eligible for a later visit and spend decoding time on the current page.
    if(!job.targets.length){previewWork.delete(job.id);job.resolve(false);previewStatus();continue;}
    previewStatus();
    try{
      const result=await requestPreview(job.id);if(!centerArt(result.preview))throw Error('预览地址无效');job.state='done';job.preview=result.preview;
      for(const item of catalog.items)if(item.files.some(file=>file.id===job.id)){item.preview=result.preview;delete item.preview_status;}
      for(const key of ['exact','similar','dismissed_similar'])for(const group of catalog.review?.[key]||[])for(const file of group.files)if(file.id===job.id)file.preview=result.preview;
      for(const target of job.targets)if(target.parent.isConnected!==false){target.parent.replaceChildren();image({...target.item,preview:result.preview},target.parent);previewControl(target.parent,'done');target.parent.closest?.('.card,.review-file')?.querySelector('.preview-status')?.remove();}
      if(detailBindings&&job.targets.some(target=>target.parent===detailBindings.art))detailBindings.preview=result.preview;
      job.resolve(true);
    }catch(error){job.state='failed';job.error=error.message;for(const target of job.targets)if(target.parent.isConnected!==false){if(target.parent.previewFallback?.())continue;previewPlaceholder(target.item,target.parent,'预览未生成：'+error.message);previewControl(target.parent,'failed');}job.resolve(false);}
    finally{job.targets=[];previewStatus();}
  }}finally{previewWorking=false;previewStatus();}
}
function image(item,parent,large=false){
  const art=centerArt(item.preview);
  if(art){const img=make('img');img.src=art;img.alt=item.title;img.loading=large?'eager':'lazy';img.onerror=()=>{
    const file=previewFile(item),job=file&&previewWork.get(file.id);
    const wasDone=job?.state==='done';if(wasDone){job.state='failed';job.error='预览文件不可读取，可点击重试';previewStatus();}
    if(parent.previewFallback?.())return;
    previewControl(parent,'failed');
    if(wasDone){previewPlaceholder(item,parent,job.error);return;}
    previewPlaceholder(item,parent,'预览缺失，正在准备补充…');observePreview({...item,preview:''},parent);
  };parent.append(img);}else{previewPlaceholder(item,parent,catalog.restored?'备份中没有预览，需重新扫描本机原文件':'滚动到这里会自动补充预览');observePreview(item,parent);}
}
function addPreviewRepair(item,parent,actions){
  const repair=button(item.kind==='photo'?'补预览':'补封面',()=>queuePreview(item,parent,true));repair.previewLabel=repair.textContent;repair.hidden=!!item.preview;repair.previewAllowed=previewAllowed(item);repair.disabled=!repair.previewAllowed;repair.setAttribute('data-preview-repair','true');parent.previewRepairControl=repair;actions.append(repair);
  const state=previewWork.get(previewFile(item)?.id)?.state;if(state&&(!item.preview||state==='failed'||state==='queued'))previewControl(parent,state);
}
function button(label,handler,cls=''){const node=make('button',cls,label);node.type='button';node.onclick=handler;return node;}
function metadataButton(label,handler,cls=''){const control=button(label,handler,cls);trackAvailability(control,()=>!busy);return control;}
function showRows(rows,parent,kind=''){const wall=make('div','wall'+(kind==='photo'?' photo-wall':kind==='video'?' video-wall':''));for(const item of rows){
  const card=make('article','card'+(item.kind==='photo'?' photo-card':'')),cover=button('',()=>openDetail(item));card.setAttribute('data-media-id',item.id);cover.className='cover';cover.setAttribute('aria-label','查看'+labels[item.kind]+' '+item.title);image(item,cover);card.append(cover);
  if(manage){const label=make('label','pick'),check=make('input');check.type='checkbox';check.checked=selected.has(item.id);check.setAttribute('aria-label','选择 '+item.title);if(!selectionChecks.has(item.id))selectionChecks.set(item.id,[]);selectionChecks.get(item.id).push(check);check.onchange=()=>{if(busy){check.checked=selected.has(item.id);return;}if(check.checked){if(selected.size>=200){check.checked=false;message('一次最多选择200项，请分批处理。');return;}selected.add(item.id);}else selected.delete(item.id);syncSelection();};label.append(check);card.append(label);}
  if(item.favorite)card.append(make('span','favorite-mark','★'));
  card.append(make('h3','',item.title));const meta=item.kind==='movie'?[item.metadata.year,...(item.metadata.genres||[]).slice(0,2)].filter(Boolean).join(' · '):item.kind==='photo'?String(item.metadata.date_original||item.metadata.date||item.metadata.date_taken||''):[labels[item.kind],size(item.bytes)].join(' · ');
  card.append(make('div','meta',meta||sourceFolder(item.folder)));
  if(item.preview_status)card.append(make('div','meta preview-status',item.preview_status));
  if(item.kind!=='photo')card.append(make('div','meta video-facts',item.files.length===1?centerVideoText(item.files[0]):item.files.length+' 个视频文件 · 详情查看各版本 / 分段'));
  if($('query').value.trim()){const path=item.files[0]?.path||item.folder,parts=path.split(/[\\/]/).filter(Boolean),hint=parts.length>4?'…/'+parts.slice(-4).join('/'):path,location=make('div','meta search-path',hint+(item.files.length>1?' 等 '+item.files.length+' 个文件':''));location.setAttribute('title',item.files.map(file=>file.path).join('\n'));cover.setAttribute('aria-label','查看'+labels[item.kind]+' '+item.title+' · '+path);card.append(location);}
  const actions=make('div','card-actions');addPreviewRepair(item,cover,actions);actions.append(metadataButton(item.favorite?'★ 已收藏':'☆ 收藏',()=>update({action:'favorite',id:item.id,value:!item.favorite})));
  if(item.kind!=='photo'&&item.files.length){
    if(item.files.length===1){const play=button('播放',()=>openOriginal(item,item.files[0]));trackAvailability(play,()=>canOpenFile(item.files[0]));actions.append(play);}
    else{const choose=button('选择播放',()=>openDetail(item));choose.setAttribute('title','选择要播放的版本或分段');actions.append(choose);}
  }
  if(manage||item.kind!=='photo'){const ids=centerMediaIds([item]),trash=button('删除…',()=>openFileReview('delete',ids),'danger');trackAvailability(trash,()=>canTrash(ids));actions.append(trash);}card.append(actions);wall.append(card);
}parent.append(wall);}
function sourceFolder(path){return path.split(/[\\/]/).filter(Boolean).pop()||path;}
function collectionCard(name,ids,handler,preview,path='',missing=0){
  const card=button('',handler,'collection'),items=ids.map(id=>byId.get(id)).filter(Boolean);
  // Try at most three distinct files, preferring existing art over decoding.
  const rank=item=>centerArt(item.preview)?2:previewAllowed(item)?1:0;
  const candidates=[...new Map([...items].sort((a,b)=>rank(b)-rank(a)).map(item=>[previewFile(item)?.id,item])).values()].slice(0,3);
  if(candidates.length){const art=make('div','collection-art');let index=0;art.previewFallback=()=>{if(index+1>=candidates.length)return false;art.replaceChildren();image(candidates[++index],art);return true;};image(candidates[0],art);card.append(art);}
  const count=ids.length+' 项媒体'+(missing?' · 另有 '+missing+' 项不在本次扫描结果或已隐藏':'');card.append(make('strong','',name),make('span','muted',count));if(path){const location=make('div','muted album-path',path);location.setAttribute('title',path);card.append(location);card.setAttribute('aria-label',name+' · '+path+' · '+count);}return card;
}

const availabilityControls=new Map(),selectionChecks=new Map();
function trackAvailability(control,allowed){availabilityControls.set(control,allowed);control.disabled=!allowed();}
function syncAvailability(){for(const [control,allowed]of availabilityControls){if(control.isConnected===false)availabilityControls.delete(control);else control.disabled=!allowed();}}
function syncSelection(){if(!catalog)return;syncAvailability();for(const [id,checks]of selectionChecks)for(const check of checks)check.checked=selected.has(id);$('select-page').disabled=busy||!pageItems.length;$('collection-submit').disabled=busy;$('collection-submit').textContent=busy?'正在保存…':'保存';const reviewing=view==='review'&&!globalSearch();$('manage-bar').hidden=!manage||reviewing;$('manage-toggle').hidden=reviewing;$('selected-count').textContent=`已选 ${selected.size} 项`;$('manage-toggle').textContent=manage?'完成整理':'整理模式';$('manage-toggle').setAttribute('aria-pressed',String(manage));
  for(const id of ['add-collection','create-selection-collection','delete-selected','remove-collection','clear-selection'])$(id).disabled=busy||!selected.size;
  const chosen=catalog.items.filter(item=>selected.has(item.id));$('compare-selected').disabled=busy||chosen.length!==2||chosen.some(item=>item.kind!=='photo'||item.files.length!==1);$('delete-selected').disabled=!canTrash(centerMediaIds(chosen));$('add-collection').disabled=busy||!selected.size||!Object.keys(catalog.state.collections).length;$('remove-collection').hidden=!collection||globalSearch();
  const noCollections=!Object.keys(catalog.state.collections).length;$('target-collection').hidden=$('add-collection').hidden=noCollections;const prior=$('target-collection').value;$('target-collection').replaceChildren();for(const [id,item]of Object.entries(catalog.state.collections)){const option=make('option','',item.name);option.value=id;$('target-collection').append(option);}if(catalog.state.collections[prior])$('target-collection').value=prior;
}
function retainSelection(selection,ids){const allowed=new Set(ids);for(const id of selection)if(!allowed.has(id))selection.delete(id);}
function resetLocation(){reviewSelected.clear();reviewInitialized=false;reviewFeedback='';page=folderPage=0;album=collection=tag=directory='';selected.clear();}
function fillFilters(){
  for(const [id,values,label]of [['filter-year',[...new Set(catalog.items.map(centerYear).filter(Boolean))].sort().reverse(),'全部年份'],['filter-genre',[...new Set(catalog.items.flatMap(item=>item.metadata.genres||[]))].sort(),'全部类型标签']]){
    const prior=$(id).value;$(id).replaceChildren();for(const value of ['',...values]){const option=make('option','',value||label);option.value=value;$(id).append(option);}$(id).value=values.includes(prior)?prior:'';
  }
}
const browsePositions=new Map();
function positionKey(){return JSON.stringify([view,directory,album,collection,tag,...['query','search-scope','media-kind','media-order','filter-year','filter-genre','filter-duration','filter-resolution','photo-layout'].map(id=>$(id).value),$('include-subfolders').checked]);}
function scrollBrowse(position){if(typeof window.scrollTo==='function')window.scrollTo(position?.x||0,position?.y||0);}
function browseTo(next={}){
  const key=positionKey();browsePositions.delete(key);browsePositions.set(key,{page,folderPage,x:window.scrollX||0,y:window.scrollY||0});if(browsePositions.size>100)browsePositions.delete(browsePositions.keys().next().value);
  view=next.view||view;directory=next.directory||'';album=next.album||'';collection=next.collection||'';tag=next.tag||'';selected.clear();reviewSelected.clear();
  const prior=browsePositions.get(positionKey());page=prior?.page||0;folderPage=prior?.folderPage||0;render();scrollBrowse(prior);
}
function turnPage(offset,folders=false){if(folders)folderPage+=offset;else page+=offset;render();scrollBrowse();}
function enterDirectory(path){browseTo({directory:path});}

function renderDirectories(paths){
  const nodes=catalog.directories||{},kind=$('media-kind').value,year=$('filter-year').value,genre=$('filter-genre').value,duration=$('filter-duration').value,resolution=$('filter-resolution').value;
  const rows=paths.map(path=>{const ids=centerFolderIds(nodes,path,true);return {path,items:centerFilter([...ids].map(id=>byId.get(id)).filter(Boolean),{kind,year,genre,duration,resolution})};}).filter(row=>!(kind||year||genre||duration||resolution)||row.items.length);
  rows.sort((a,b)=>a.path.localeCompare(b.path,'zh-CN',{numeric:true}));const pages=Math.max(1,Math.ceil(rows.length/galleryPageSize));folderPage=Math.max(0,Math.min(folderPage,pages-1));
  const wall=make('div','wall video-wall');for(const row of rows.slice(folderPage*galleryPageSize,folderPage*galleryPageSize+galleryPageSize)){const card=collectionCard(nodes[row.path].name,row.items.map(item=>item.id),()=>enterDirectory(row.path),'',row.path);card.classList.add('directory-card');wall.append(card);}$('center-content').append(wall);
  if(rows.length>galleryPageSize){const pager=make('div','pager'),previous=button('上一页文件夹',()=>turnPage(-1,true)),next=button('下一页文件夹',()=>turnPage(1,true));previous.disabled=folderPage===0;next.disabled=folderPage>=pages-1;pager.append(previous,make('span','muted',`文件夹第 ${folderPage+1}/${pages} 页 · 共 ${rows.length} 个`),next);$('center-content').append(pager);}
  if(!rows.length&&!directory)$('center-content').append(make('p','empty','没有匹配的媒体目录。试试清除筛选，或从“媒体库”添加目录并扫描。'));return rows.length;
}
function renderDirectoryBreadcrumb(){
  const nodes=catalog.directories||{},chain=[],visited=new Set();let path=directory;
  while(nodes[path]&&!visited.has(path)){visited.add(path);chain.unshift(path);path=nodes[path].parent;}
  $('center-breadcrumb').append(button('↑ 上一级',()=>enterDirectory(nodes[directory]?.parent||'')),button('全部目录',()=>enterDirectory('')));for(const key of chain)$('center-breadcrumb').append(button(nodes[key].name,()=>enterDirectory(key)));
  $('center-breadcrumb').append(make('div','muted album-path',directory));
  const folderLabel=catalog.state.folder_labels?.[directory],label=sourceFolder(directory);
  $('center-breadcrumb').append(button(folderLabel?'查看标签：'+folderLabel:'以“'+label+'”作为目录标签',async()=>{if(folderLabel){browseTo({view:'tags',tag:folderLabel});}else if(await update({action:'folder_label',path:directory,label}))message('已绑定目录标签。该目录及子目录的照片、视频会集中在此标签；以后新增文件重扫后自动加入。','success');}));
  if(folderLabel)$('center-breadcrumb').append(button('取消目录标签',()=>update({action:'folder_label',path:directory,label:''})));
  const folder=directory,classification=make('select');classification.setAttribute('aria-label','本目录的视频分类');
  for(const [value,text]of [['auto','继承上级 / 自动识别'],['movie','电影目录'],['video','普通视频目录']]){const option=make('option','',text);option.value=value;classification.append(option);}
  classification.value=catalog.state.folder_kinds?.[folder]||'auto';trackAvailability(classification,()=>!busy);
  classification.onchange=async()=>{
    const kind=classification.value,previous=catalog.state.folder_kinds?.[folder]||'auto';let saved=false;
    if(await update({action:'folder_kind',path:folder,kind},()=>{saved=true;}))message('目录分类已保存，当前及子目录的视频已更新；照片不受影响。单项手动分类优先，新文件扫描后沿用。','success');
    else if(!saved)classification.value=previous;
  };
  const setting=make('label','actions muted');setting.append(make('span','','视频分类（含子目录）'),classification);$('center-breadcrumb').append(setting);


}

function renderReview(){
  const tabs=make('div','actions');for(const [mode,label]of [['exact','精确重复'],['similar','相似照片']]){const control=button(label+' · '+(catalog.review?.[mode]?.length||0),()=>{reviewMode=mode;applyBrowseFilters();},reviewMode===mode?'primary':'');control.setAttribute('aria-pressed',String(reviewMode===mode));tabs.append(control);}$('center-content').append(tabs);
  $('center-content').append(make('p','muted',reviewMode==='exact'?'这些文件经完整 SHA-256 确认内容相同。可选一份保留，勾选其余副本；也可逐项删除，至少保留一份。':'这些只是视觉相似候选，可能是不同照片。先并排对比，再决定保留或删除。'));
  if(reviewMode==='similar'&&catalog.review?.similar_note)$('center-content').append(make('p','notice',catalog.review.similar_note));
  for(const warning of catalog.review?.warnings||[])$('center-content').append(make('p','notice',warning));
  if(reviewMode==='similar'){const hidden=catalog.review?.dismissed_similar||[];tabs.append(button((showDismissedSimilar?'返回待核对':'已确认不相似')+' · '+hidden.length,()=>{showDismissedSimilar=!showDismissedSimilar;applyBrowseFilters();}));}
  const reviewData=showDismissedSimilar&&reviewMode==='similar'?{...catalog.review,similar:catalog.review.dismissed_similar||[]}:catalog.review;
  const query=$('query').value.trim(),groups=centerReviewGroups(reviewData,{mode:reviewMode,query,kind:$('media-kind').value,year:$('filter-year').value,genre:$('filter-genre').value,order:$('media-order').value,owners:byId,duration:$('filter-duration').value,resolution:$('filter-resolution').value}),pages=Math.max(1,Math.ceil(groups.length/10));page=Math.max(0,Math.min(page,pages-1));$('center-subtitle').textContent=(query?'在当前分组中搜索 · ':'')+groups.length+' 组 · 显示整组供核对';
  const selectedBefore=reviewSelected.size;retainSelection(reviewSelected,groups.flatMap(group=>group.files.map(file=>file.id)));if(reviewSelected.size!==selectedBefore)reviewFeedback='';
  // Only exact matches may be preselected: keep the last copy in each group.
  // This is a selection for review, never an instruction to delete.
  const selectLeft=()=>{const next=new Set();let skipped=0;for(const group of groups){if(group.files.length<2||!group.files.every(file=>canTrash([file.id])))continue;for(const file of group.files.slice(0,-1)){if(next.size<200)next.add(file.id);else skipped++;}}reviewSelected=next;reviewFeedback=skipped?'已选前 200 个文件，请分批处理其余文件。':'已选择每组左侧文件，并保留每组最后一份。';};
  if(reviewMode==='exact'&&!reviewInitialized&&groups.some(group=>group.files.length>1&&group.files.every(file=>canTrash([file.id])))){reviewInitialized=true;selectLeft();}
  const controls=make('div','actions review-selection-tools');
  const selectAll=button(reviewMode==='exact'?'全选左侧（每组保留一份）':'全选当前结果',()=>{if(reviewMode==='exact')selectLeft();else{const ids=[...new Set(groups.flatMap(group=>group.files.filter(file=>canTrash([file.id])).map(file=>file.id)))];reviewSelected=new Set(ids.slice(0,200));reviewFeedback=ids.length>200?'已选前 200 个文件，请分批处理其余文件。':'已选择当前结果中的文件；相似照片可能不同，请逐一核对。';}render();});
  const invert=button('反选当前结果',()=>{const ids=[...new Set(groups.flatMap(group=>group.files.filter(file=>canTrash([file.id])).map(file=>file.id)))],next=new Set(ids.filter(id=>!reviewSelected.has(id)));if(reviewMode==='exact')for(const group of groups)if(group.files.every(file=>next.has(file.id)))next.delete(group.files[group.files.length-1].id);reviewSelected=new Set([...next].slice(0,200));reviewFeedback=next.size>200?'反选结果超过 200 个，已选前 200 个，请分批处理。':reviewMode==='exact'?'已反选，并为每组保留至少一份。':'已反选；相似照片可能不同，请逐一核对。';render();});
  const canSelect=()=>!!groups.length&&!busy&&!centerStale&&mediaAccess.available&&!catalog.restored&&!catalog.operation_state?.running&&!catalog.operation_state?.warnings?.length;trackAvailability(selectAll,canSelect);trackAvailability(invert,canSelect);controls.append(selectAll,invert);$('center-content').append(controls);
  for(const [index,group]of groups.slice(page*10,page*10+10).entries()){
    const section=make('section','review-group');section.append(make('h2','',(reviewMode==='exact'?'精确重复':'相似照片')+' · 第 '+(page*10+index+1)+' 组'));
    const groupActions=make('div','actions review-group-actions');if(reviewMode==='similar')groupActions.append(metadataButton(showDismissedSimilar?'恢复到待核对':'确认不相似',()=>update({action:'review_similarity',id:group.id,dismissed:!showDismissedSimilar})));
    const first=group.files[0];if(reviewMode==='exact')section.append(make('p','muted',group.files.length+' 个文件 · 每个 '+size(first.bytes)));
    if(reviewMode==='exact'&&group.files.length>201)section.append(make('p','muted','其余副本超过 200 个文件，请分批勾选清理，每次都会保留至少一份。'));
    const canKeep=()=>reviewMode==='exact'&&group.files.length>1&&group.files.length<=201&&group.files.every(file=>canTrash([file.id]));
    if(group.files.length===2&&group.files.every(file=>file.kind==='photo'))groupActions.append(button('对比这两张',()=>openFileReview('compare',group.files.map(file=>file.id))));section.append(groupActions);
    const files=make('div','review-files');for(const file of group.files){
      const card=make('article','review-file'),preview=button('',()=>{const owner=byId.get(file.owner);if(owner)openDetail(owner);},'cover');preview.setAttribute('aria-label','预览 '+file.name);image({title:file.name,preview:file.preview,kind:file.kind,files:[file]},preview);
      const pick=make('label','review-pick'),check=make('input');check.type='checkbox';check.checked=reviewSelected.has(file.id);trackAvailability(check,()=>canTrash([file.id]));check.setAttribute('aria-label','选择清理 '+file.path);check.onchange=()=>{if(check.checked&&reviewSelected.size>=200){check.checked=false;reviewFeedback='每批最多 200 个文件，请分批处理。';render();return;}if(check.checked)reviewSelected.add(file.id);else reviewSelected.delete(file.id);reviewFeedback='';render();};pick.append(check,make('span','','选择清理'));card.append(pick);
      const actions=make('div','actions');addPreviewRepair({title:file.name,preview:file.preview,kind:file.kind,files:[file]},preview,actions);if(group.files.length>2&&file.kind==='photo'&&first.kind==='photo'&&file.id!==first.id)actions.append(button('与第一张对比',()=>openFileReview('compare',[first.id,file.id])));
      if(reviewMode==='exact'&&group.files.length>1){const keep=button('保留这份，勾选其余',()=>{if(!canKeep())return;const next=new Set(reviewSelected);for(const other of group.files)next.delete(other.id);for(const other of group.files)if(other.id!==file.id)next.add(other.id);if(next.size>200){message('合计超过200个文件，请先清理当前选择。');return;}reviewSelected=next;render();});keep.setAttribute('aria-label','保留 '+file.path+'，勾选其余副本');trackAvailability(keep,canKeep);if(group.files.length>201)keep.setAttribute('title','其余副本超过200个文件，请使用逐项删除分批清理。');actions.append(keep);}
      const trash=button('删除…',()=>openFileReview('delete',[file.id]),'danger');trash.setAttribute('aria-label','删除 '+file.path);trackAvailability(trash,()=>canTrash([file.id]));actions.append(trash);card.append(make('h3','',file.name),actions,preview,make('p','muted',file.path));files.append(card);
    }section.append(files);$('center-content').append(section);
  }
  const batch=make('div','actions review-batch');batch.hidden=!reviewSelected.size&&!reviewFeedback;batch.append(make('strong','',`已选 ${reviewSelected.size} 个文件（可跨页选择）`));const clear=button('清空选择',()=>{reviewSelected.clear();reviewFeedback='';render();});clear.disabled=!reviewSelected.size;const trash=button('删除所选…',()=>{const complete=(catalog.review?.exact||[]).some(group=>group.files.every(file=>reviewSelected.has(file.id)));if(complete){reviewFeedback='精确重复组至少保留一份，请取消勾选要保留的文件。';message(reviewFeedback);render();return;}if(!canTrash([...reviewSelected])){reviewFeedback='所选文件暂时无法处理，请检查磁盘连接或刷新后重试；选择会保留。';render();return;}const kept=(catalog.review?.exact||[]).filter(group=>group.files.some(file=>reviewSelected.has(file.id))).flatMap(group=>group.files.filter(file=>!reviewSelected.has(file.id)).map(file=>file.path));openFileReview('delete',[...reviewSelected],kept.length?'将保留：'+kept.join('、')+'；清理所选 '+reviewSelected.size+' 份。':'');},'danger');trackAvailability(trash,()=>canTrash([...reviewSelected]));batch.append(clear,trash);if(reviewFeedback){const feedback=make('p','review-selection-feedback',reviewFeedback);feedback.setAttribute('role','status');batch.append(feedback);}$('center-content').append(batch);
  if(!groups.length){const reviewed=reviewMode==='similar'&&!query&&!$('media-kind').value&&!$('filter-year').value&&!$('filter-genre').value&&!$('filter-duration').value&&!$('filter-resolution').value;const note=reviewed?(showDismissedSimilar?'没有已确认不相似的照片。':(catalog.review.dismissed_similar||[]).length?'当前相似候选已全部核对，可从“已确认不相似”恢复。':'当前没有待核对的相似照片。'):'没有匹配的'+(reviewMode==='exact'?'精确重复':'相似照片')+'分组。可清除筛选，或重新扫描更新结果。';$('center-content').append(make('p','empty',note));}
  $('center-pager').hidden=!groups.length;$('center-page').textContent=`第 ${page+1}/${pages} 页 · 共 ${groups.length} 组`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=pages-1;syncSelection();
}
function syncMediaFilters(){
  const fixedKind=!globalSearch()&&({movies:'movie',videos:'video',photos:'photo'}[view]||(view==='review'&&reviewMode==='similar'?'photo':''));
  // The page already defines its type. Hidden filters must not exclude its media.
  $('media-kind').hidden=!!fixedKind;if(fixedKind)$('media-kind').value='';
  const photosOnly=fixedKind==='photo'||$('media-kind').value==='photo';
  for(const id of ['filter-duration','filter-resolution','filter-genre']){$(id).hidden=photosOnly;if(photosOnly)$(id).value='';}
}
function render(){if(!catalog)return;announceCenterView();syncMediaFilters();previewObserver?.disconnect();$('photo-layout').hidden=view!=='photos'||globalSearch();const query=$('query').value.trim(),kind=$('media-kind').value,all=globalSearch();const ids=all?null:directory?centerFolderIds(catalog.directories||{},directory,$('include-subfolders').checked):album?new Set(catalog.folders[album]||[]):collection?new Set(catalog.state.collections[collection]?.ids||[]):null;
  const context=collection?catalog.state.collections[collection]?.name||'合集':album?sourceFolder(album):directory?sourceFolder(directory):titles[view]||titles.home;
  selectionChecks.clear();pageItems=[];visible=centerFilter(catalog.items,{view,query,global:all,kind,tag:all?'':tag,ids,order:$('media-order').value||'recent',year:$('filter-year').value,genre:$('filter-genre').value,duration:$('filter-duration').value,resolution:$('filter-resolution').value});$('center-title').textContent=query?(all?'全部媒体 · 搜索结果':view==='review'?titles.review:context+' · 搜索结果'):context;
  retainSelection(selected,visible.map(item=>item.id));
  $('center-subtitle').textContent=(catalog.restored?'备份快照 · ':'')+(query?(all?'在本次媒体库中全局搜索 · ':'在当前页面中搜索 · '):'')+`${visible.length} 项媒体`+(!all&&tag?' · 标签：'+tag:'');if(!query&&!all&&view==='movies')$('center-subtitle').textContent+=' · 按作品计数，版本和分段在详情中选择';if(!query&&!all&&view==='videos')$('center-subtitle').textContent+=' · 含尚未归类为电影的文件，可按时长筛选';$('clear-search').hidden=!query;$('create-collection').hidden=view!=='collections'||!!collection||!!album||!!query;
  $('recursive-label').hidden=view!=='resources'||!directory||all;const filters=[kind,$('filter-duration').value,$('filter-resolution').value,$('filter-year').value,$('filter-genre').value,$('media-order').value&&$('media-order').value!=='recent'].filter(Boolean).length;$('center-options-summary').textContent=filters?`筛选与排序 · ${filters} 项`:'筛选与排序';$('reset-filters').hidden=!filters;
  $('center-content').replaceChildren();$('center-breadcrumb').replaceChildren();$('center-pager').hidden=true;
  const status=[];if(catalog.hidden_files)status.push(`已按本次扫描的回收站成功记录隐藏 ${catalog.hidden_files} 个文件；如已恢复，请重新扫描。`);if(catalog.missing_files)status.push(`已暂时隐藏 ${catalog.missing_files} 个确认不在原位置的文件；文件恢复后刷新列表即可重新显示。`);if(collection&&!all){const missing=(catalog.state.collections[collection]?.ids||[]).filter(id=>!byId.has(id)).length;if(missing)status.push(`此合集另有 ${missing} 项不在本次扫描结果或已隐藏；合集记录仍保留，可切换扫描结果或重新扫描对应目录。`);}if(catalog.operation_state?.running)status.push('文件操作正在进行，状态将自动更新。');if(catalog.operation_state?.warnings?.length)status.push('操作记录待核对，已暂停清理。请从整理工具查看操作记录。');centerStatusBase=status.join(' ');availabilityNotice();
  if(view==='review'&&!all){$('recursive-label').hidden=true;renderReview();return;}
  if(all)$('center-breadcrumb').append(button('← 返回'+context,clearSearch));
  if(directory&&!all)renderDirectoryBreadcrumb();
  if(!all&&(album||collection||tag)){$('center-breadcrumb').append(button('← 返回'+(tag?'标签':'相册与合集'),()=>{$('query').value='';browseTo();}));if(collection){$('center-breadcrumb').append(button('重命名合集',()=>editCollection(collection)),button('删除合集记录',async()=>{const id=collection;if(await update({action:'delete_collection',id})&&view==='collections'&&collection===id){resetLocation();render();}}));$('center-breadcrumb').append(make('span','muted','只修改合集记录，原文件保留。'));}}
  if(view==='home'&&!query&&!kind&&!tag&&!$('filter-year').value&&!$('filter-genre').value&&!$('filter-duration').value&&!$('filter-resolution').value&&$('media-order').value==='recent'){const counts=make('div','counts');for(const [value,label]of [['movie','电影'],['video','视频'],['photo','照片'],['favorite','收藏']]){const target={movie:'movies',video:'videos',photo:'photos',favorite:'favorites'}[value],node=button('',()=>switchCenterView(target),'count');node.setAttribute('aria-label','查看'+label);node.append(make('span','muted',label),make('strong','',catalog.items.filter(item=>value==='favorite'?item.favorite:item.kind===value).length));if(value==='movie')node.append(make('small','muted','部作品 · '+catalog.items.filter(item=>item.kind==='movie').reduce((sum,item)=>sum+item.files.length,0)+' 个视频文件'));else if(value==='video')node.append(make('small','muted','个普通视频文件'));node.append(make('span','count-link','查看'+label+' →'));counts.append(node);}$('center-content').append(counts);
    const recentItems=catalog.recent.map(id=>byId.get(id)).filter(Boolean).slice(0,8),latestItems=visible.slice(0,12);if(recentItems.length){$('center-content').append(make('h2','','最近打开'));showRows(recentItems,$('center-content'));}
    $('center-content').append(make('h2','','最近更新'),make('p','muted','按文件修改时间展示；系统播放器不回传播放进度。'));pageItems=[...new Map([...recentItems,...latestItems].map(item=>[item.id,item])).values()];showRows(latestItems,$('center-content'));
    const albums=Object.entries(catalog.folders).map(([path,ids])=>({path,ids,mtime:ids.reduce((value,id)=>Math.max(value,byId.get(id)?.mtime||0),0)})).sort((a,b)=>b.mtime-a.mtime).slice(0,4).map(item=>[item.path,item.ids]);if(albums.length){$('center-content').append(make('h2','','目录相册'));const grid=make('div','wall');for(const [path,ids]of albums)grid.append(collectionCard(sourceFolder(path),ids,()=>browseTo({view:'collections',album:path}),'',path));$('center-content').append(grid);}
    syncSelection();return;
  }
  if(view==='resources'&&!query){const nodes=catalog.directories||{};const folderCount=renderDirectories(directory?nodes[directory]?.children||[]:Object.keys(nodes).filter(path=>!nodes[path].parent));if(directory)$('center-subtitle').textContent=(catalog.restored?'备份快照 · ':'')+folderCount+' 个子文件夹 · '+($('include-subfolders').checked?'含子目录 ':'本层 ')+visible.length+' 项媒体';if(!directory){$('center-subtitle').textContent='选择目录逐层浏览 · '+catalog.items.length+' 项媒体';syncSelection();return;}if(visible.length)$('center-content').append(make('h2','', $('include-subfolders').checked?'本目录及子目录的媒体':'本层媒体'));}
  if(view==='collections'&&!query&&!collection&&!album){
    const grid=make('div','wall video-wall'),tiles=[],matched=new Set(visible.map(item=>item.id)),filtered=kind||$('filter-year').value||$('filter-genre').value||$('filter-duration').value||$('filter-resolution').value;
    function tile(name,ids,handler,path=''){
      const shown=ids.filter(id=>matched.has(id));if(filtered&&!shown.length)return;
      const items=shown.map(id=>byId.get(id)),missing=ids.filter(id=>!byId.has(id)).length;tiles.push({name,mtime:items.reduce((value,item)=>Math.max(value,item.mtime),0),oldest:items.length?items.reduce((value,item)=>Math.min(value,item.mtime),Infinity):0,bytes:items.reduce((value,item)=>value+item.bytes,0),create:()=>collectionCard(name,shown,handler,items[0]?.preview,path,missing)});
    }
    for(const [id,item]of Object.entries(catalog.state.collections))tile(item.name,item.ids,()=>browseTo({collection:id}));
    for(const [path,ids]of Object.entries(catalog.folders))tile(sourceFolder(path),ids,()=>browseTo({album:path}),path);
    const order=$('media-order').value,name=(a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true});tiles.sort((a,b)=>order==='name'?name(a,b):order==='size'?b.bytes-a.bytes||name(a,b):order==='oldest'?a.oldest-b.oldest||name(a,b):b.mtime-a.mtime||name(a,b));
    const tilePages=Math.max(1,Math.ceil(tiles.length/galleryPageSize));page=Math.max(0,Math.min(page,tilePages-1));grid.append(...tiles.slice(page*galleryPageSize,page*galleryPageSize+galleryPageSize).map(tile=>tile.create()));$('center-content').append(grid);$('center-pager').hidden=!tiles.length;$('center-page').textContent=`第 ${page+1}/${tilePages} 页 · ${tiles.length} 个相册与合集`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=tilePages-1;
    if(!tiles.length)$('center-content').append(make('p','empty','没有匹配的相册与合集。试试清除筛选，或添加媒体目录。'));syncSelection();return;
  }
  if(view==='tags'&&!query&&!tag){const counts=new Map();for(const item of visible)for(const label of item.tags)counts.set(label,(counts.get(label)||0)+1);const chips=make('div','chips');for(const [label,count]of [...counts].sort((a,b)=>b[1]-a[1]))chips.append(button(label+' · '+count,()=>browseTo({tag:label}),'chip'));$('center-content').append(chips);$('center-subtitle').textContent=counts.size+' 个标签 · '+visible.length+' 项媒体';if(!counts.size){$('center-content').append(make('p','empty',catalog.items.some(item=>item.tags.length)?'当前筛选下没有标签，可清除筛选查看已有标签。':'还没有标签，不需要重新扫描。可在媒体详情添加标签，或直接用下方的文件夹名创建标签。'));const folders=make('div','actions');for(const path of Object.keys(catalog.folders).slice(0,20)){const label=sourceFolder(path),control=button('以“'+label+'”建标签',()=>update({action:'folder_label',path,label}));control.setAttribute('title',path);folders.append(control);}$('center-content').append(folders);}syncSelection();return;}
  if(view==='photos'&&!all&&$('photo-layout').value==='timeline'){const order=$('media-order').value||'recent';visible=centerTimelineOrder(visible,order);$('center-subtitle').textContent+=' · '+(order==='oldest'?'月份从旧到新':'月份从新到旧')+' · 月内'+({recent:'最近更新',oldest:'最早更新',name:'按名称',size:'按大小'}[order]||'最近更新');}
  const pages=Math.max(1,Math.ceil(visible.length/galleryPageSize));page=Math.max(0,Math.min(page,pages-1));pageItems=visible.slice(page*galleryPageSize,page*galleryPageSize+galleryPageSize);if(view==='photos'&&!all&&$('photo-layout').value==='timeline'){const months=new Map();for(const item of pageItems){const month=item.metadata.timeline_month||'';if(!months.has(month))months.set(month,[]);months.get(month).push(item);}for(const [month,items]of months){$('center-content').append(make('h2','',month||'日期未知'),make('p','muted',items.some(item=>item.metadata.timeline_source!=='EXIF 拍摄日期')?'含文件名或文件时间推测日期，详情可查看来源。':'按拍摄日期归组'));showRows(items,$('center-content'),'photo');}}else showRows(pageItems,$('center-content'),kind||(!query&&(view==='photos'?'photo':view==='videos'?'video':''))||(visible.length&&visible.every(item=>item.kind===visible[0].kind)?visible[0].kind:''));
  const unclassifiedMovies=view==='movies'&&!query&&!filters&&!catalog.items.some(item=>item.kind==='movie')&&catalog.items.some(item=>item.kind==='video');if(!visible.length&&unclassifiedMovies){$('center-content').append(make('p','empty','目前没有归类为电影的项目。普通视频仍在“视频”中；没有本地影片资料或明确电影目录时不会自动猜测，不必为分类重新扫描。'),button('查看视频并标为电影',()=>switchCenterView('videos'),'primary'));}
  if(!visible.length&&!unclassifiedMovies)$('center-content').append(make('p','empty',view==='resources'&&directory&&!$('include-subfolders').checked&&catalog.directories[directory]?.children.length?'本层没有匹配的媒体。可进入子文件夹，或勾选“包含子文件夹”。':collection&&!query&&!filters?(catalog.state.collections[collection]?.ids.length?'此合集的媒体不在当前扫描结果中。可切换到包含这些文件的扫描结果，或重新扫描对应目录；合集记录仍然保留。':'这个合集还没有媒体。在照片、视频或电影页进入整理模式，勾选后选择此合集，再点击“加入合集”；原文件位置不变。'):view==='favorites'?'没有匹配的收藏。可在媒体详情收藏，或清除当前筛选。':'没有匹配的媒体。试试清除筛选或添加媒体目录。'));
  $('center-pager').hidden=!visible.length;$('center-page').textContent=`第 ${page+1}/${pages} 页 · 共 ${visible.length} 项`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=pages-1;syncSelection();
}
function centerOverlay(open){if(typeof reportOverlay==='function')reportOverlay(open);}
function openDetail(item,sequence=visible){detailOriginKey=positionKey();detailEpoch++;detailSequence=sequence.filter(media=>media.kind===item.kind);if(!detailSequence.some(media=>media.id===item.id))detailSequence=[item];detailIndex=detailSequence.findIndex(media=>media.id===item.id);renderDetail();if(!$('media-detail').open)$('media-detail').showModal();centerOverlay(true);}
function detailKey(item){return JSON.stringify([item.id,item.kind,item.title,item.tag_key,item.manual_kind,item.folder_kind,item.metadata,item.files,mediaAccess.available,catalog.restored]);}
function renderDetail(){stopDetailVideo();detailBindings=null;const item=detailSequence[detailIndex];if(!item)return;$('detail-feedback').textContent='';$('detail-title').textContent=item.title;$('detail-status').textContent='';$('detail-content').replaceChildren();$('detail-actions').replaceChildren();const grid=make('div','detail-grid'+(item.kind==='photo'?' photo-detail':'')),art=make('div','detail-art'+(item.kind==='photo'?' photo':''));image(item,art,true);grid.append(art);const info=make('div'),metadata=item.metadata||{},facts=make('dl','facts');
  for(const [label,value]of [['类型',labels[item.kind]],['年份',metadata.year],['类型标签',(metadata.genres||[]).join('、')],['演员',(metadata.actors||[]).join('、')],['导演',(metadata.directors||[]).join('、')],['尺寸',metadata.width&&metadata.height?metadata.width+' × '+metadata.height:''],[metadata.date_source==='tiff_datetime'?'照片记录时间':'拍摄时间',metadata.date_original||metadata.date||metadata.date_taken],['日期依据',metadata.timeline_source],['相机',metadata.camera||metadata.model],['文件大小',size(item.bytes)]])if(value){facts.append(make('dt','',label),make('dd','',value));}info.append(facts);if(item.kind!=='photo'&&item.files.some(file=>!file.video?.duration_seconds||!file.video?.width))info.append(make('p','muted','部分视频信息未知。启用视频解析后重新扫描可补充；解码器不支持的格式仍可浏览和查重。'));if(metadata.plot)info.append(make('p','',metadata.plot));const chips=make('div','chips');for(const label of item.tags)chips.append(make('span','chip',label));info.append(chips);
  const tags=make('input');tags.setAttribute('aria-label','编辑本地标签');tags.placeholder='本地标签，用逗号分隔';tags.value=item.local_tags.join(', ');const tagActions=make('div','actions detail-controls');tagActions.append(tags,metadataButton('保存标签',async()=>{
    const context=detailContext(),submitted=tags.value;
    if(await update({action:'labels',id:item.id,tags:submitted.split(/[,，]/).map(s=>s.trim()).filter(Boolean)})&&currentDetail(context)){
      const saved=byId.get(item.id)?.local_tags.join(', ');if(saved===undefined)return;
      const currentTags=detailBindings.tags;if(currentTags.value===submitted)currentTags.value=saved;
      refreshDetail(item.id);detailNotice(context,'detail-feedback',currentTags.value===saved?'标签已保存。':'本次标签已保存；新输入的内容尚未保存。');
    }
  }));info.append(tagActions);
  let rating=null,note=null;if(item.kind==='movie'&&item.tag_key){rating=make('select');rating.setAttribute('aria-label','电影评分');for(let n=0;n<=5;n++){const option=make('option','',n?n+' 星':'未评分');option.value=n;rating.append(option);}rating.value=item.personal.rating;note=make('textarea');note.value=item.personal.note;note.maxLength=2000;note.setAttribute('aria-label','电影观看备注');note.style.cssText='display:block;width:100%;margin:10px 0';info.append(rating,note,metadataButton('保存评分与备注',async()=>{if(busy)return;const context=detailContext(),submitted={rating:Number(rating.value),note:note.value};busy=true;syncSelection();try{await request('api/notes',{key:item.tag_key,...submitted});if(await reloadAfterSave(context)&&currentDetail(context)){refreshDetail(item.id);detailNotice(context,'detail-feedback',Number(detailBindings.rating?.value)===submitted.rating&&detailBindings.note?.value===submitted.note?'评分与备注已保存。':'本次评分与备注已保存；新修改的内容尚未保存。');}}catch(error){detailNotice(context,'detail-status',error.message);}finally{busy=false;syncSelection();}}));}
  for(const file of item.files){const row=make('div','file');row.append(make('strong','',file.name),make('p','',file.path));if(item.kind!=='photo')row.append(make('p','video-facts',centerVideoText(file)+' · 编码尺寸'));
    const fileActions=make('div','actions');if(item.kind!=='photo'){
      const getPlayer=()=>row.children.find?row.children.find(n=>n.tagName==='VIDEO'):row.querySelector('video');
      const inline=button('页内预览',()=>{
        const existing=getPlayer();
        if(existing){existing.pause();existing.removeAttribute('src');existing.load();row.removeChild(existing);inline.textContent='页内预览';if($('detail-status').textContent.startsWith('页内预览无法读取'))$('detail-status').textContent='';syncAvailability();return;}
        if(!canOpenFile(file,'preview'))return;
        const player=make('video');player.controls=true;player.preload='metadata';player.style.cssText='display:block;width:100%;max-height:50vh;margin:12px 0;background:#15151c';player.setAttribute('aria-label','预览 '+file.name);player.src='api/video/'+file.id;const context=detailContext();
        player.onerror=()=>{if(currentDetail(context)&&getPlayer()===player)$('detail-status').textContent='页内预览无法读取：可能是浏览器不支持此编码，或文件自扫描后变化。可尝试系统播放；文件变化时需重扫。';};row.append(player);inline.textContent='关闭预览';syncAvailability();
      });trackAvailability(inline,()=>!!getPlayer()||canOpenFile(file,'preview'));fileActions.append(inline);
    }for(const [action,label]of [['open',item.kind==='photo'?'打开原图':'播放'],['reveal','打开所在文件夹']]){const control=button(label,()=>performMediaAction(item,file,action));trackAvailability(control,()=>canOpenFile(file,action));fileActions.append(control);}if(manage||item.kind!=='photo'){const trash=button('删除此文件…',()=>openFileReview('delete',[file.id]),'danger');trackAvailability(trash,()=>canTrash([file.id]));fileActions.append(trash);}row.append(fileActions);info.append(row);
  }
  if(item.kind!=='photo'){info.append(metadataButton(item.kind==='video'?'标为电影':'改为普通视频',async()=>{
    const context=detailContext(),kind=item.kind==='video'?'movie':'video';
    if(await update({action:'media_kind',ids:item.files.map(file=>file.id),kind})){
      message('“'+item.title+'”已改为'+(kind==='movie'?'电影':'普通视频')+'，可在“'+(kind==='movie'?'电影':'视频')+'”中查看。','success');
      if(currentDetail(context)){refreshDetail(item.id);detailNotice(context,'detail-feedback','分类已保存，无需重新扫描。');}
    }
  }));}
  if(item.manual_kind)info.append(metadataButton('恢复目录 / 自动分类',async()=>{await update({action:'media_kind',ids:item.files.map(file=>file.id),kind:'auto'});}));
  const previewActions=make('div','actions detail-controls');addPreviewRepair(item,art,previewActions);info.append(previewActions);grid.append(info);$('detail-content').append(grid);
  const favorite=metadataButton(item.favorite?'★ 已收藏':'☆ 收藏',async()=>{const context=detailContext(),current=byId.get(item.id);if(current&&await update({action:'favorite',id:item.id,value:!current.favorite})&&currentDetail(context)){refreshDetail(item.id);detailNotice(context,'detail-feedback',byId.get(item.id).favorite?'已加入收藏。':'已取消收藏。');}},'primary');$('detail-actions').append(favorite);
  detailBindings={id:item.id,key:detailKey(item),tags,tagsSaved:item.local_tags.join(', '),chips,favorite,rating,note,personal:{...item.personal},tagKey:item.tag_key,art,preview:item.preview};
  if(item.kind==='photo'){const previous=button('上一张',()=>{if(detailIndex>0){detailIndex--;detailEpoch++;renderDetail();}}),next=button('下一张',()=>{if(detailIndex<detailSequence.length-1){detailIndex++;detailEpoch++;renderDetail();}});const counter=make('span','muted');detailBindings.navigation={previous,next,counter};syncDetailNavigation();$('detail-actions').append(previous,counter,next);}
  if(catalog.restored)$('detail-actions').append(make('span','muted','备份快照：重新扫描后才能打开原文件。'));
}
function syncDetailNavigation(){const nav=detailBindings?.navigation;if(!nav)return;nav.previous.disabled=detailIndex===0;nav.next.disabled=detailIndex>=detailSequence.length-1;nav.counter.textContent=`${detailIndex+1} / ${detailSequence.length}`;}
function refreshDetail(id){if(!$('media-detail').open||detailSequence[detailIndex]?.id!==id)return;const updated=byId.get(id);if(!updated)return;detailSequence[detailIndex]=updated;
  syncDetailNavigation();const binding=detailBindings;if(binding?.id===id&&binding.key===detailKey(updated)){
    // Update saved records in place: keep the player, focus and unsaved fields.
    const labels=updated.local_tags.join(', ');if(binding.tags.value===binding.tagsSaved)binding.tags.value=labels;binding.tagsSaved=labels;
    binding.chips.replaceChildren();for(const tag of updated.tags)binding.chips.append(make('span','chip',tag));binding.favorite.textContent=updated.favorite?'★ 已收藏':'☆ 收藏';
    if(binding.rating&&String(binding.rating.value)===String(binding.personal.rating))binding.rating.value=updated.personal.rating;
    if(binding.note&&binding.note.value===binding.personal.note)binding.note.value=updated.personal.note;binding.personal={...updated.personal};
    if(binding.preview!==updated.preview){binding.art.replaceChildren();image(updated,binding.art,true);binding.preview=updated.preview;}return;
  }
  // A structural refresh may replace fields; carry only dirty values for this media.
  const drafts={},focused=document.activeElement;let focusField='',selection=null;
  if(binding?.id===id){
    for(const field of ['tags','rating','note']){
      const input=binding[field];if(!input)continue;
      if(field!=='tags'&&binding.tagKey!==updated.tag_key)continue;
      const saved=field==='tags'?binding.tagsSaved:binding.personal[field];
      if(String(input.value)!==String(saved))drafts[field]=input.value;
      if(input===focused){focusField=field;if(typeof input.selectionStart==='number')selection=[input.selectionStart,input.selectionEnd,input.selectionDirection];}
    }
  }
  const position=$('media-detail').scrollTop;renderDetail();
  for(const [field,value]of Object.entries(drafts))if(detailBindings[field])detailBindings[field].value=value;
  const input=detailBindings[focusField];if(input){input.focus({preventScroll:true});if(selection&&typeof input.setSelectionRange==='function')input.setSelectionRange(...selection);}
  $('media-detail').scrollTop=position;
}
let collectionInitialIds=[];
function editCollection(id='',ids=[]){collectionEpoch++;collectionInitialIds=[...ids];editingCollection=id;$('collection-form-title').textContent=id?'重命名合集':'新建合集';$('collection-name').value=id?catalog.state.collections[id].name:'';$('collection-error').textContent='';$('collection-help').textContent=ids.length?'保存后将选中的 '+ids.length+' 项媒体加入新合集；原文件位置不变。':'合集仅保存分类记录，原文件位置不变。';$('collection-form').showModal();centerOverlay(true);$('collection-name').focus();}
$('collection-save').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  const epoch=collectionEpoch,submitted=$('collection-name').value,initialCount=collectionInitialIds.length;
  const ok=await update({action:editingCollection?'rename':'create',id:editingCollection,name:submitted,...(!editingCollection?{ids:collectionInitialIds}:{})},saved=>{
    // Keep the server-issued identity even if the following refresh fails.
    if(epoch===collectionEpoch&&$('collection-form').open&&saved.created_collection_id){editingCollection=saved.created_collection_id;$('collection-form-title').textContent='重命名合集';}
  });
  if(epoch!==collectionEpoch||!$('collection-form').open)return;
  if(ok&&$('collection-name').value===submitted){$('collection-form').close();if(initialCount){$('target-collection').value=editingCollection;message('合集“'+submitted.trim()+'”已保存，已加入 '+initialCount+' 项媒体；原文件位置不变。','success');}}
  else $('collection-error').textContent=ok?'本次名称已保存；新输入的名称尚未保存。':$('center-error').textContent;
};$('collection-form').addEventListener('close',()=>{collectionEpoch++;centerOverlay(false);});$('collection-cancel').onclick=()=>$('collection-form').close();$('create-collection').onclick=()=>editCollection();$('create-selection-collection').onclick=()=>{if(!busy&&manage&&selected.size)editCollection('',[...selected]);};
function applyBrowseFilters(clearSelection=true){page=folderPage=0;if(clearSelection){selected.clear();reviewSelected.clear();reviewFeedback='';}render();scrollBrowse();}
function clearSearch(){$('query').value='';applyBrowseFilters();}
$('center-search').onsubmit=event=>{event.preventDefault();applyBrowseFilters();};$('clear-search').onclick=clearSearch;for(const id of ['search-scope','media-kind'])$(id).onchange=()=>applyBrowseFilters();
for(const id of ['media-order','photo-layout'])$(id).onchange=()=>applyBrowseFilters(false);
for(const id of ['filter-year','filter-genre','filter-duration','filter-resolution','include-subfolders'])$(id).onchange=()=>applyBrowseFilters();
$('reset-filters').onclick=()=>{$('media-kind').value=$('filter-year').value=$('filter-genre').value=$('filter-duration').value=$('filter-resolution').value='';$('media-order').value='recent';applyBrowseFilters();};
$('manage-toggle').onclick=()=>{if(busy)return;manage=!manage;selected.clear();render();};$('select-page').onclick=()=>{if(busy)return;const pending=pageItems.filter(item=>!selected.has(item.id)),room=200-selected.size;for(const item of pending.slice(0,room))selected.add(item.id);render();if(pending.length>room)message('已达到200项选择上限，请分批处理。');};$('clear-selection').onclick=()=>{selected.clear();render();};
$('add-collection').onclick=async()=>{if(await update({action:'members',id:$('target-collection').value,ids:[...selected]}))message('已加入合集，原文件位置不变。');};$('remove-collection').onclick=async()=>{if(collection&&!globalSearch()&&await update({action:'members',id:collection,ids:[...selected],remove:true})){selected.clear();render();}};
$('delete-selected').onclick=()=>{if(busy||!manage||!mediaAccess.available||catalog.restored)return;const ids=centerMediaIds(catalog.items.filter(item=>selected.has(item.id)));if(!ids.length)return;if(ids.length>200){message('所选作品包含超过200个文件，请减少选择后预览。');return;}openFileReview('delete',ids);};
$('compare-selected').onclick=()=>{const chosen=catalog.items.filter(item=>selected.has(item.id));if(chosen.length===2&&chosen.every(item=>item.kind==='photo'&&item.files.length===1))openFileReview('compare',centerMediaIds(chosen));};
function stopDetailVideo(){for(const player of ($('detail-content').querySelectorAll?.('video')||[])){player.pause();player.removeAttribute('src');player.load();}}
$('media-detail').addEventListener('close',()=>{
  const last=detailSequence[detailIndex],sameContext=detailOriginKey===positionKey(),inGrid=globalSearch()||(view!=='home'&&view!=='review'&&!(view==='resources'&&!directory)&&!(view==='collections'&&!collection&&!album&&!$('query').value.trim())&&!(view==='tags'&&!tag&&!$('query').value.trim()));
  detailEpoch++;stopDetailVideo();detailBindings=null;centerOverlay(false);detailOriginKey='';
  if(last?.kind!=='photo'||!sameContext||!inGrid)return;
  const index=visible.findIndex(item=>item.id===last.id);if(index<0)return;const targetPage=Math.floor(index/galleryPageSize);if(targetPage===page)return;
  page=targetPage;render();scrollBrowse();const card=$('center-content').querySelector?.('.card[data-media-id="'+last.id+'"]');card?.scrollIntoView?.({block:'center'});
});$('detail-close').onclick=()=>$('media-detail').close();$('media-detail').addEventListener('keydown',event=>{if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;const item=detailSequence[detailIndex];if(item?.kind!=='photo')return;if(event.key==='ArrowRight'&&detailIndex<detailSequence.length-1){event.preventDefault();detailIndex++;detailEpoch++;renderDetail();}else if(event.key==='ArrowLeft'&&detailIndex>0){event.preventDefault();detailIndex--;detailEpoch++;renderDetail();}});
const dashboardLink=document.querySelector?.('nav[aria-label="报告导航"] a');
let announcedView='';
function announceCenterView(){
  if(location.hash!=='#'+view&&window.history?.replaceState)window.history.replaceState(null,'','#'+view);
  if(announcedView===view)return;announcedView=view;
  if(window.parent&&window.parent!==window&&dashboardLink)window.parent.postMessage({type:'media-context',view},new URL(dashboardLink.href).origin);
}

function switchCenterView(next){if(window.parent!==window&&dashboardLink)window.parent.postMessage({type:'media-navigate',view:next},new URL(dashboardLink.href).origin);else{location.hash='#'+next;navigate();}}
if(window.parent!==window&&dashboardLink){$('center-rescan').hidden=false;$('center-rescan').onclick=()=>{window.parent.postMessage({type:'media-rescan'},new URL(dashboardLink.href).origin);message('已请求重新扫描当前资料库的来源目录；扫描期间可继续浏览。');};}
$('center-prev').onclick=()=>turnPage(-1);$('center-next').onclick=()=>turnPage(1);$('center-refresh').onclick=()=>{for(const [id,job]of previewWork)if(job.state==='failed')previewWork.delete(id);previewStatus();return load();};
function navigate(){const next=location.hash.slice(1);view=titles[next]?next:'home';$('query').value='';$('search-scope').value='view';$('media-kind').value=$('filter-year').value=$('filter-genre').value=$('filter-duration').value=$('filter-resolution').value='';$('media-order').value='recent';$('include-subfolders').checked=false;manage=false;resetLocation();if($('media-detail').open)$('media-detail').close();render();}
window.addEventListener('hashchange',navigate);navigate();load();
}
