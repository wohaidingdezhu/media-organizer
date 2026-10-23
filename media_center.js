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
if(typeof module!=='undefined'&&module.exports)module.exports={centerFilter,centerMediaIds,centerArt,centerYear,centerFolderIds,centerReviewGroups,centerDuration,centerVideoText,centerVideoMatch};
if(typeof document!=='undefined'){
if(window.parent!==window)document.body.classList.add('embedded-center');
const $=id=>document.getElementById(id),make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
const titles={home:'媒体中心',movies:'电影',videos:'视频',photos:'照片',collections:'相册与合集',favorites:'收藏',tags:'标签与分类',resources:'资源管理',review:'重复与相似'};
let catalog=null,byId=new Map(),mediaAccess={ids_by_path:{},available:false},view='home',page=0,manage=false,busy=false,selected=new Set(),visible=[],pageItems=[],album='',collection='',tag='',editingCollection='',detailSequence=[],detailIndex=0,loadTicket=0,directory='',folderPage=0,reviewMode='exact',byFile=new Map(),centerOperationTimer=null,centerStale=false;
const size=value=>value>=1073741824?(value/1073741824).toFixed(1)+' GB':value>=1048576?(value/1048576).toFixed(1)+' MB':(value/1024).toFixed(0)+' KB';
const labels={movie:'电影',video:'视频',photo:'照片'};
function message(value){$('center-error').textContent=value||'';}
function globalSearch(){return !!$('query').value.trim()&&$('search-scope').value==='all';}
async function request(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(result.error||'操作未完成');return result;}
async function load(){
  clearTimeout(centerOperationTimer);const ticket=++loadTicket;
  try{const [data,access]=await Promise.all([request('api/center'),request('api/media')]);if(ticket!==loadTicket)return;
    centerStale=false;catalog=data;byId=new Map(data.items.map(item=>[item.id,item]));byFile=new Map(data.items.flatMap(item=>item.files.map(file=>[file.id,{...file,kind:item.kind}])));mediaAccess=access;
    if(directory&&!data.directories?.[directory])directory='';fillFilters();for(const id of selected)if(!byId.has(id))selected.delete(id);
    const scroll=[window.scrollX||0,window.scrollY||0];message();render();if(typeof window.scrollTo==='function')window.scrollTo(...scroll);
    if($('media-detail').open){const current=detailSequence[detailIndex]?.id;detailSequence=detailSequence.map(item=>byId.get(item.id)).filter(Boolean);detailIndex=detailSequence.findIndex(item=>item.id===current);if(detailIndex<0)$('media-detail').close();else{const position=$('media-detail').scrollTop;renderDetail();$('media-detail').scrollTop=position;}}
    if(data.operation_state?.running)centerOperationTimer=setTimeout(load,1000);
  }catch(error){if(ticket===loadTicket){centerStale=true;if(catalog)render();message(error.message);$('center-subtitle').textContent='资料库暂时无法读取，请点击刷新重试。';if(catalog?.operation_state?.running)centerOperationTimer=setTimeout(load,3000);}}
}
function canTrash(ids){return !busy&&!centerStale&&mediaAccess.available&&!catalog?.restored&&!catalog?.operation_state?.running&&!catalog?.operation_state?.warnings?.length&&ids.length>0&&ids.length<=200&&ids.every(id=>{const file=byFile.get(id);return file&&mediaAccess.ids_by_path[file.path]===id;});}
function openFileReview(action,ids,summary=''){
  if(busy)return;if(action==='delete'&&!canTrash(ids))return;
  if(action==='compare'&&(ids.length!==2||ids.some(id=>byFile.get(id)?.kind!=='photo')))return;
  reportActionOpen(action,ids,load,summary);
}
async function openOriginal(item,file){
  if(busy||catalog.restored||mediaAccess.ids_by_path[file.path]!==file.id)return;
  try{await request('api/media/action',{id:file.id,action:'open'});await request('api/center',{action:'recent',id:item.id});await load();}catch(error){message(error.message);}
}
async function update(payload){if(busy)return false;busy=true;try{await request('api/center',payload);await load();return true;}catch(error){message(error.message);if($('media-detail').open)$('detail-status').textContent=error.message;return false;}finally{busy=false;syncSelection();}}
function image(item,parent,large=false){const art=centerArt(item.preview);if(art){const img=make('img');img.src=art;img.alt=item.title;img.loading=large?'eager':'lazy';parent.append(img);}else parent.append(make('div','placeholder',item.kind==='photo'?'▧':item.kind==='video'?'▷':'▤'));}
function button(label,handler,cls=''){const node=make('button',cls,label);node.type='button';node.onclick=handler;return node;}
function showRows(rows,parent,kind=''){const wall=make('div','wall'+(kind==='photo'?' photo-wall':kind==='video'?' video-wall':''));for(const item of rows){
  const card=make('article','card'),cover=button('',()=>openDetail(item));cover.className='cover';cover.setAttribute('aria-label','查看'+labels[item.kind]+' '+item.title);image(item,cover);card.append(cover);
  if(manage){const label=make('label','pick'),check=make('input');check.type='checkbox';check.checked=selected.has(item.id);check.setAttribute('aria-label','选择 '+item.title);check.onchange=()=>{if(busy){check.checked=selected.has(item.id);return;}if(check.checked){if(selected.size>=200){check.checked=false;message('一次最多选择200项，请分批处理。');return;}selected.add(item.id);}else selected.delete(item.id);syncSelection();};label.append(check);card.append(label);}
  if(item.favorite)card.append(make('span','favorite-mark','★'));
  card.append(make('h3','',item.title));const meta=item.kind==='movie'?[item.metadata.year,...(item.metadata.genres||[]).slice(0,2)].filter(Boolean).join(' · '):item.kind==='photo'?String(item.metadata.date_original||item.metadata.date||item.metadata.date_taken||''):[labels[item.kind],size(item.bytes)].join(' · ');
  card.append(make('div','meta',meta||sourceFolder(item.folder)));
  if(item.kind!=='photo')card.append(make('div','meta video-facts',item.files.length===1?centerVideoText(item.files[0]):item.files.length+' 个视频文件 · 详情查看各版本 / 分段'));
  if($('query').value.trim()){const path=item.files[0]?.path||item.folder,parts=path.split(/[\\/]/).filter(Boolean),hint=parts.length>4?'…/'+parts.slice(-4).join('/'):path,location=make('div','meta search-path',hint+(item.files.length>1?' 等 '+item.files.length+' 个文件':''));location.setAttribute('title',item.files.map(file=>file.path).join('\n'));cover.setAttribute('aria-label','查看'+labels[item.kind]+' '+item.title+' · '+path);card.append(location);}
  const actions=make('div','card-actions');actions.append(button(item.favorite?'★ 已收藏':'☆ 收藏',()=>update({action:'favorite',id:item.id,value:!item.favorite})));
  if(item.kind!=='photo'&&item.files.length===1){const play=button('播放',()=>openOriginal(item,item.files[0]));play.disabled=!mediaAccess.available||catalog.restored||mediaAccess.ids_by_path[item.files[0].path]!==item.files[0].id;actions.append(play);}
  if(manage){const ids=centerMediaIds([item]),trash=button('删除…',()=>openFileReview('delete',ids),'danger');trash.disabled=!canTrash(ids);actions.append(trash);}card.append(actions);wall.append(card);
}parent.append(wall);}
function sourceFolder(path){return path.split(/[\\/]/).filter(Boolean).pop()||path;}
function collectionCard(name,ids,handler,preview,path=''){const card=button('',handler,'collection');if(preview){const art=centerArt(preview);if(art){const img=make('img');img.src=art;img.alt='';img.style.cssText='width:100%;height:110px;object-fit:cover;border-radius:8px;margin-bottom:8px';card.append(img);}}card.append(make('strong','',name),make('span','muted',ids.length+' 项媒体'));if(path){const location=make('div','muted album-path',path);location.setAttribute('title',path);card.append(location);card.setAttribute('aria-label',name+' · '+path+' · '+ids.length+' 项媒体');}return card;}
function syncSelection(){if(!catalog)return;const reviewing=view==='review'&&!globalSearch();$('manage-bar').hidden=!manage||reviewing;$('manage-toggle').hidden=reviewing;$('selected-count').textContent=`已选 ${selected.size} 项`;$('manage-toggle').textContent=manage?'完成整理':'整理模式';$('manage-toggle').setAttribute('aria-pressed',String(manage));
  for(const id of ['add-collection','delete-selected','remove-collection','clear-selection'])$(id).disabled=busy||!selected.size;
  const chosen=catalog.items.filter(item=>selected.has(item.id));$('compare-selected').disabled=busy||chosen.length!==2||chosen.some(item=>item.kind!=='photo'||item.files.length!==1);$('delete-selected').disabled=!canTrash(centerMediaIds(chosen));$('add-collection').disabled=busy||!selected.size||!Object.keys(catalog.state.collections).length;$('remove-collection').hidden=!collection||globalSearch();
  const prior=$('target-collection').value;$('target-collection').replaceChildren();for(const [id,item]of Object.entries(catalog.state.collections)){const option=make('option','',item.name);option.value=id;$('target-collection').append(option);}if(catalog.state.collections[prior])$('target-collection').value=prior;
}
function resetLocation(){page=folderPage=0;album=collection=tag=directory='';selected.clear();}
function fillFilters(){
  for(const [id,values,label]of [['filter-year',[...new Set(catalog.items.map(centerYear).filter(Boolean))].sort().reverse(),'全部年份'],['filter-genre',[...new Set(catalog.items.flatMap(item=>item.metadata.genres||[]))].sort(),'全部类型标签']]){
    const prior=$(id).value;$(id).replaceChildren();for(const value of ['',...values]){const option=make('option','',value||label);option.value=value;$(id).append(option);}$(id).value=values.includes(prior)?prior:'';
  }
}
function enterDirectory(path){directory=path;page=folderPage=0;selected.clear();render();}
function renderDirectories(paths){
  const nodes=catalog.directories||{},kind=$('media-kind').value,year=$('filter-year').value,genre=$('filter-genre').value,duration=$('filter-duration').value,resolution=$('filter-resolution').value;
  const rows=paths.map(path=>{const ids=centerFolderIds(nodes,path,true);return {path,items:centerFilter([...ids].map(id=>byId.get(id)).filter(Boolean),{kind,year,genre,duration,resolution})};}).filter(row=>!(kind||year||genre||duration||resolution)||row.items.length);
  rows.sort((a,b)=>a.path.localeCompare(b.path,'zh-CN',{numeric:true}));const pages=Math.max(1,Math.ceil(rows.length/50));folderPage=Math.max(0,Math.min(folderPage,pages-1));
  const wall=make('div','wall video-wall');for(const row of rows.slice(folderPage*50,folderPage*50+50)){const card=collectionCard(nodes[row.path].name,row.items.map(item=>item.id),()=>enterDirectory(row.path),'',row.path);card.classList.add('directory-card');wall.append(card);}$('center-content').append(wall);
  if(rows.length>50){const pager=make('div','pager'),previous=button('上一页文件夹',()=>{folderPage--;render();}),next=button('下一页文件夹',()=>{folderPage++;render();});previous.disabled=folderPage===0;next.disabled=folderPage>=pages-1;pager.append(previous,make('span','muted',`文件夹第 ${folderPage+1}/${pages} 页 · 共 ${rows.length} 个`),next);$('center-content').append(pager);}
  if(!rows.length&&!directory)$('center-content').append(make('p','empty','没有匹配的媒体目录。试试清除筛选，或从“媒体库”添加目录并扫描。'));return rows.length;
}
function renderDirectoryBreadcrumb(){
  const nodes=catalog.directories||{},chain=[],visited=new Set();let path=directory;
  while(nodes[path]&&!visited.has(path)){visited.add(path);chain.unshift(path);path=nodes[path].parent;}
  $('center-breadcrumb').append(button('↑ 上一级',()=>enterDirectory(nodes[directory]?.parent||'')),button('全部目录',()=>enterDirectory('')));for(const key of chain)$('center-breadcrumb').append(button(nodes[key].name,()=>enterDirectory(key)));
  $('center-breadcrumb').append(make('div','muted album-path',directory));
}

function renderReview(){
  const tabs=make('div','actions');for(const [mode,label]of [['exact','精确重复'],['similar','相似照片']]){const control=button(label+' · '+(catalog.review?.[mode]?.length||0),()=>{reviewMode=mode;page=0;render();},reviewMode===mode?'primary':'');control.setAttribute('aria-pressed',String(reviewMode===mode));tabs.append(control);}$('center-content').append(tabs);
  $('center-content').append(make('p','muted',reviewMode==='exact'?'这些文件经完整 SHA-256 确认内容相同。可选一份保留，预览清理其余副本；也可逐项删除，至少保留一份。':'这些只是视觉相似候选，可能是不同照片。先并排对比，再决定保留或删除。'));
  if(reviewMode==='similar'&&catalog.review?.similar_note)$('center-content').append(make('p','notice',catalog.review.similar_note));
  for(const warning of catalog.review?.warnings||[])$('center-content').append(make('p','notice',warning));
  const query=$('query').value.trim(),groups=centerReviewGroups(catalog.review,{mode:reviewMode,query,kind:$('media-kind').value,year:$('filter-year').value,genre:$('filter-genre').value,order:$('media-order').value,owners:byId,duration:$('filter-duration').value,resolution:$('filter-resolution').value}),pages=Math.max(1,Math.ceil(groups.length/10));page=Math.max(0,Math.min(page,pages-1));$('center-subtitle').textContent=(query?'在当前分组中搜索 · ':'')+groups.length+' 组 · 显示整组供核对';
  for(const [index,group]of groups.slice(page*10,page*10+10).entries()){
    const section=make('section','review-group');section.append(make('h2','',(reviewMode==='exact'?'精确重复':'相似照片')+' · 第 '+(page*10+index+1)+' 组'));
    const first=group.files[0];if(reviewMode==='exact')section.append(make('p','muted',group.files.length+' 个文件 · 每个 '+size(first.bytes)));
    if(reviewMode==='exact'&&group.files.length>201)section.append(make('p','muted','其余副本超过200个文件，请先逐项分批清理，再选择保留的一份。'));
    const canKeep=reviewMode==='exact'&&group.files.length>1&&group.files.length<=201&&group.files.every(file=>canTrash([file.id]));
    if(group.files.length===2&&group.files.every(file=>file.kind==='photo'))section.append(button('对比这两张',()=>openFileReview('compare',group.files.map(file=>file.id))));
    const files=make('div','review-files');for(const file of group.files){
      const card=make('article','review-file'),preview=button('',()=>{const owner=byId.get(file.owner);if(owner)openDetail(owner);},'cover');preview.setAttribute('aria-label','预览 '+file.name);image({title:file.name,preview:file.preview,kind:file.kind},preview);
      const actions=make('div','actions');if(group.files.length>2&&file.kind==='photo'&&first.kind==='photo'&&file.id!==first.id)actions.append(button('与第一张对比',()=>openFileReview('compare',[first.id,file.id])));
      if(reviewMode==='exact'&&group.files.length>1){const keep=button('保留这份，清理其余…',()=>{const others=group.files.filter(other=>other.id!==file.id).map(other=>other.id);openFileReview('delete',others,'将保留：'+file.path+'；预览清理其余 '+others.length+' 份副本。');});keep.setAttribute('aria-label','保留 '+file.path+'，预览清理其余副本');keep.disabled=!canKeep;if(group.files.length>201)keep.setAttribute('title','其余副本超过200个文件，请使用逐项删除分批清理。');actions.append(keep);}
      const trash=button('删除…',()=>openFileReview('delete',[file.id]),'danger');trash.setAttribute('aria-label','删除 '+file.path);trash.disabled=!canTrash([file.id]);actions.append(trash);card.append(make('h3','',file.name),actions,preview,make('p','muted',file.path));files.append(card);
    }section.append(files);$('center-content').append(section);
  }
  if(!groups.length)$('center-content').append(make('p','empty','没有匹配的'+(reviewMode==='exact'?'精确重复':'相似照片')+'分组。可清除筛选，或重新扫描更新结果。'));
  $('center-pager').hidden=!groups.length;$('center-page').textContent=`第 ${page+1}/${pages} 页 · 共 ${groups.length} 组`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=pages-1;syncSelection();
}
function render(){if(!catalog)return;const query=$('query').value.trim(),kind=$('media-kind').value,all=globalSearch();const ids=all?null:directory?centerFolderIds(catalog.directories||{},directory,$('include-subfolders').checked):album?new Set(catalog.folders[album]||[]):collection?new Set(catalog.state.collections[collection]?.ids||[]):null;
  const context=collection?catalog.state.collections[collection]?.name||'合集':album?sourceFolder(album):directory?sourceFolder(directory):titles[view]||titles.home;
  pageItems=[];visible=centerFilter(catalog.items,{view,query,global:all,kind,tag:all?'':tag,ids,order:$('media-order').value||'recent',year:$('filter-year').value,genre:$('filter-genre').value,duration:$('filter-duration').value,resolution:$('filter-resolution').value});$('center-title').textContent=query?(all?'全部媒体 · 搜索结果':view==='review'?titles.review:context+' · 搜索结果'):context;
  $('center-subtitle').textContent=(catalog.restored?'备份快照 · ':'')+(query?(all?'在本次媒体库中全局搜索 · ':'在当前页面中搜索 · '):'')+`${visible.length} 项媒体`+(!all&&tag?' · 标签：'+tag:'');$('clear-search').hidden=!query;$('create-collection').hidden=view!=='collections'||!!collection||!!album||!!query;
  $('recursive-label').hidden=view!=='resources'||!directory||all;const filters=[kind,$('filter-duration').value,$('filter-resolution').value,$('filter-year').value,$('filter-genre').value,$('media-order').value&&$('media-order').value!=='recent'].filter(Boolean).length;$('center-options-summary').textContent=filters?`筛选与排序 · ${filters} 项`:'筛选与排序';$('reset-filters').hidden=!filters;
  $('center-content').replaceChildren();$('center-breadcrumb').replaceChildren();$('center-pager').hidden=true;
  const status=[];if(catalog.hidden_files)status.push(`已按本次扫描的回收站成功记录隐藏 ${catalog.hidden_files} 个文件；如已恢复，请重新扫描。`);if(catalog.operation_state?.running)status.push('文件操作正在进行，状态将自动更新。');if(catalog.operation_state?.warnings?.length)status.push('操作记录待核对，已暂停清理。请从整理工具查看操作记录。');$('center-status').hidden=!status.length;$('center-status').textContent=status.join(' ');
  if(view==='review'&&!all){$('recursive-label').hidden=true;renderReview();return;}
  if(all)$('center-breadcrumb').append(button('← 返回'+context,clearSearch));
  if(directory&&!all)renderDirectoryBreadcrumb();
  if(!all&&(album||collection||tag)){$('center-breadcrumb').append(button('← 返回'+(tag?'标签':'相册与合集'),()=>{$('query').value='';resetLocation();render();}));if(collection){$('center-breadcrumb').append(button('重命名合集',()=>editCollection(collection)),button('删除合集记录',async()=>{if(await update({action:'delete_collection',id:collection})){resetLocation();render();}}));$('center-breadcrumb').append(make('span','muted','只修改合集记录，原文件保留。'));}}
  if(view==='home'&&!query&&!kind&&!tag&&!$('filter-year').value&&!$('filter-genre').value&&!$('filter-duration').value&&!$('filter-resolution').value&&$('media-order').value==='recent'){const counts=make('div','counts');for(const [value,label]of [['movie','电影'],['video','视频'],['photo','照片'],['favorite','收藏']]){const node=make('div','count');node.append(make('span','muted',label),make('strong','',catalog.items.filter(item=>value==='favorite'?item.favorite:item.kind===value).length));counts.append(node);}$('center-content').append(counts);
    if(catalog.recent.length){$('center-content').append(make('h2','','最近打开'));showRows(catalog.recent.map(id=>byId.get(id)).filter(Boolean).slice(0,8),$('center-content'));}
    $('center-content').append(make('h2','','最近更新'),make('p','muted','按文件修改时间展示；系统播放器不回传播放进度。'));pageItems=visible.slice(0,12);showRows(pageItems,$('center-content'));
    const albums=Object.entries(catalog.folders).map(([path,ids])=>({path,ids,mtime:ids.reduce((value,id)=>Math.max(value,byId.get(id)?.mtime||0),0)})).sort((a,b)=>b.mtime-a.mtime).slice(0,4).map(item=>[item.path,item.ids]);if(albums.length){$('center-content').append(make('h2','','目录相册'));const grid=make('div','wall');for(const [path,ids]of albums)grid.append(collectionCard(sourceFolder(path),ids,()=>{view='collections';album=path;page=0;selected.clear();render();},'',path));$('center-content').append(grid);}
    syncSelection();return;
  }
  if(view==='resources'&&!query){const nodes=catalog.directories||{};const folderCount=renderDirectories(directory?nodes[directory]?.children||[]:Object.keys(nodes).filter(path=>!nodes[path].parent));if(directory)$('center-subtitle').textContent=(catalog.restored?'备份快照 · ':'')+folderCount+' 个子文件夹 · '+($('include-subfolders').checked?'含子目录 ':'本层 ')+visible.length+' 项媒体';if(!directory){$('center-subtitle').textContent='选择目录逐层浏览 · '+catalog.items.length+' 项媒体';syncSelection();return;}if(visible.length)$('center-content').append(make('h2','', $('include-subfolders').checked?'本目录及子目录的媒体':'本层媒体'));}
  if(view==='collections'&&!query&&!collection&&!album){
    const grid=make('div','wall video-wall'),tiles=[],matched=new Set(visible.map(item=>item.id)),filtered=kind||$('filter-year').value||$('filter-genre').value||$('filter-duration').value||$('filter-resolution').value;
    function tile(name,ids,handler,path=''){
      const shown=ids.filter(id=>matched.has(id));if(filtered&&!shown.length)return;
      const items=shown.map(id=>byId.get(id));tiles.push({name,mtime:items.reduce((value,item)=>Math.max(value,item.mtime),0),oldest:items.length?items.reduce((value,item)=>Math.min(value,item.mtime),Infinity):0,bytes:items.reduce((value,item)=>value+item.bytes,0),create:()=>collectionCard(name,shown,handler,items[0]?.preview,path)});
    }
    for(const [id,item]of Object.entries(catalog.state.collections))tile(item.name,item.ids,()=>{collection=id;page=0;selected.clear();render();});
    for(const [path,ids]of Object.entries(catalog.folders))tile(sourceFolder(path),ids,()=>{album=path;page=0;selected.clear();render();},path);
    const order=$('media-order').value,name=(a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true});tiles.sort((a,b)=>order==='name'?name(a,b):order==='size'?b.bytes-a.bytes||name(a,b):order==='oldest'?a.oldest-b.oldest||name(a,b):b.mtime-a.mtime||name(a,b));
    const tilePages=Math.max(1,Math.ceil(tiles.length/50));page=Math.max(0,Math.min(page,tilePages-1));grid.append(...tiles.slice(page*50,page*50+50).map(tile=>tile.create()));$('center-content').append(grid);$('center-pager').hidden=!tiles.length;$('center-page').textContent=`第 ${page+1}/${tilePages} 页 · ${tiles.length} 个相册与合集`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=tilePages-1;
    if(!tiles.length)$('center-content').append(make('p','empty','没有匹配的相册与合集。试试清除筛选，或添加媒体目录。'));syncSelection();return;
  }
  if(view==='tags'&&!query&&!tag){const counts=new Map();for(const item of visible)for(const label of item.tags)counts.set(label,(counts.get(label)||0)+1);const chips=make('div','chips');for(const [label,count]of [...counts].sort((a,b)=>b[1]-a[1]))chips.append(button(label+' · '+count,()=>{tag=label;page=0;render();},'chip'));$('center-content').append(chips);if(!counts.size)$('center-content').append(make('p','empty','打开媒体详情添加标签，就可以在这里集中浏览。'));syncSelection();return;}
  const pages=Math.max(1,Math.ceil(visible.length/50));page=Math.max(0,Math.min(page,pages-1));pageItems=visible.slice(page*50,page*50+50);showRows(pageItems,$('center-content'),kind||(!query&&(view==='photos'?'photo':view==='videos'?'video':''))||(visible.length&&visible.every(item=>item.kind===visible[0].kind)?visible[0].kind:''));
  if(!visible.length)$('center-content').append(make('p','empty',view==='resources'&&directory&&!$('include-subfolders').checked&&catalog.directories[directory]?.children.length?'本层没有匹配的媒体。可进入子文件夹，或勾选“包含子文件夹”。':view==='favorites'?'没有匹配的收藏。可在媒体详情收藏，或清除当前筛选。':'没有匹配的媒体。试试清除筛选或添加媒体目录。'));
  $('center-pager').hidden=!visible.length;$('center-page').textContent=`第 ${page+1}/${pages} 页 · 共 ${visible.length} 项`;$('center-prev').disabled=page===0;$('center-next').disabled=page>=pages-1;syncSelection();
}
function openDetail(item,sequence=visible){detailSequence=sequence.filter(media=>media.kind===item.kind);if(!detailSequence.some(media=>media.id===item.id))detailSequence=[item];detailIndex=detailSequence.findIndex(media=>media.id===item.id);renderDetail();if(!$('media-detail').open)$('media-detail').showModal();}
function renderDetail(){const item=detailSequence[detailIndex];if(!item)return;$('detail-title').textContent=item.title;$('detail-status').textContent='';$('detail-content').replaceChildren();$('detail-actions').replaceChildren();const grid=make('div','detail-grid'),art=make('div','detail-art'+(item.kind==='photo'?' photo':''));image(item,art,true);grid.append(art);const info=make('div'),metadata=item.metadata||{},facts=make('dl','facts');
  for(const [label,value]of [['类型',labels[item.kind]],['年份',metadata.year],['类型标签',(metadata.genres||[]).join('、')],['演员',(metadata.actors||[]).join('、')],['导演',(metadata.directors||[]).join('、')],['尺寸',metadata.width&&metadata.height?metadata.width+' × '+metadata.height:''],[metadata.date_source==='tiff_datetime'?'照片记录时间':'拍摄时间',metadata.date_original||metadata.date||metadata.date_taken],['相机',metadata.camera||metadata.model],['文件大小',size(item.bytes)]])if(value){facts.append(make('dt','',label),make('dd','',value));}info.append(facts);if(item.kind!=='photo'&&item.files.some(file=>!file.video?.duration_seconds||!file.video?.width))info.append(make('p','muted','部分视频信息未知。启用视频解析后重新扫描可补充；解码器不支持的格式仍可浏览和查重。'));if(metadata.plot)info.append(make('p','',metadata.plot));const chips=make('div','chips');for(const label of item.tags)chips.append(make('span','chip',label));info.append(chips);
  const tags=make('input');tags.setAttribute('aria-label','编辑本地标签');tags.placeholder='本地标签，用逗号分隔';tags.value=item.local_tags.join(', ');info.append(tags,button('保存标签',async()=>{if(await update({action:'labels',id:item.id,tags:tags.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)}))refreshDetail(item.id);}));
  if(item.kind==='movie'&&item.tag_key){const rating=make('select');rating.setAttribute('aria-label','电影评分');for(let n=0;n<=5;n++){const option=make('option','',n?n+' 星':'未评分');option.value=n;rating.append(option);}rating.value=item.personal.rating;const note=make('textarea');note.value=item.personal.note;note.maxLength=2000;note.setAttribute('aria-label','电影观看备注');note.style.cssText='display:block;width:100%;margin:10px 0';info.append(rating,note,button('保存评分与备注',async()=>{if(busy)return;busy=true;try{await request('api/notes',{key:item.tag_key,rating:Number(rating.value),note:note.value});await load();refreshDetail(item.id);}catch(error){$('detail-status').textContent=error.message;}finally{busy=false;}}));}
  for(const file of item.files){const row=make('div','file');row.append(make('strong','',file.name),make('p','',file.path));if(item.kind!=='photo')row.append(make('p','video-facts',centerVideoText(file)+' · 编码尺寸'));const canOpen=mediaAccess.available&&mediaAccess.ids_by_path[file.path]===file.id&&!catalog.restored;
    for(const [action,label]of [['open',item.kind==='photo'?'打开原图':'播放'],['reveal','打开所在文件夹']]){const control=button(label,async()=>{if(busy)return;control.disabled=true;try{await request('api/media/action',{id:file.id,action});if(action==='open'){try{await request('api/center',{action:'recent',id:item.id});await load();}catch(error){$('detail-status').textContent='已打开，最近记录未保存：'+error.message;}}}catch(error){$('detail-status').textContent=error.message;}finally{control.disabled=!canOpen;}});control.disabled=!canOpen;row.append(control);}if(manage){const trash=button('删除此文件…',()=>openFileReview('delete',[file.id]),'danger');trash.disabled=!canTrash([file.id]);row.append(trash);}info.append(row);
  }
  grid.append(info);$('detail-content').append(grid);
  $('detail-actions').append(button(item.favorite?'★ 已收藏':'☆ 收藏',async()=>{if(await update({action:'favorite',id:item.id,value:!item.favorite}))refreshDetail(item.id);},'primary'));
  if(item.kind==='photo'){const previous=button('上一张',()=>{if(detailIndex>0){detailIndex--;renderDetail();}}),next=button('下一张',()=>{if(detailIndex<detailSequence.length-1){detailIndex++;renderDetail();}});previous.disabled=detailIndex===0;next.disabled=detailIndex>=detailSequence.length-1;$('detail-actions').append(previous,make('span','muted',`${detailIndex+1} / ${detailSequence.length}`),next);}
  if(catalog.restored)$('detail-actions').append(make('span','muted','备份快照：重新扫描后才能打开原文件。'));
}
function refreshDetail(id){const updated=byId.get(id);if(updated){const position=$('media-detail').scrollTop;detailSequence[detailIndex]=updated;renderDetail();$('media-detail').scrollTop=position;}}
function editCollection(id=''){editingCollection=id;$('collection-form-title').textContent=id?'重命名合集':'新建合集';$('collection-name').value=id?catalog.state.collections[id].name:'';$('collection-error').textContent='';$('collection-form').showModal();$('collection-name').focus();}
$('collection-save').onsubmit=async event=>{event.preventDefault();const ok=await update({action:editingCollection?'rename':'create',id:editingCollection,name:$('collection-name').value});if(ok)$('collection-form').close();else $('collection-error').textContent=$('center-error').textContent;};$('collection-cancel').onclick=()=>$('collection-form').close();$('create-collection').onclick=()=>editCollection();
function clearSearch(){$('query').value='';page=folderPage=0;selected.clear();render();}
$('center-search').onsubmit=event=>{event.preventDefault();page=folderPage=0;selected.clear();render();};$('clear-search').onclick=clearSearch;$('search-scope').onchange=()=>{page=folderPage=0;selected.clear();render();};$('media-kind').onchange=()=>{page=folderPage=0;selected.clear();render();};
for(const id of ['media-order','filter-year','filter-genre','filter-duration','filter-resolution','include-subfolders'])$(id).onchange=()=>{page=folderPage=0;selected.clear();render();};
$('reset-filters').onclick=()=>{$('media-kind').value=$('filter-year').value=$('filter-genre').value=$('filter-duration').value=$('filter-resolution').value='';$('media-order').value='recent';page=folderPage=0;selected.clear();render();};
$('manage-toggle').onclick=()=>{if(busy)return;manage=!manage;selected.clear();render();};$('select-page').onclick=()=>{if(busy)return;for(const item of pageItems.slice(0,200-selected.size))selected.add(item.id);render();};$('clear-selection').onclick=()=>{selected.clear();render();};
$('add-collection').onclick=async()=>{if(await update({action:'members',id:$('target-collection').value,ids:[...selected]}))message('已加入合集，原文件位置不变。');};$('remove-collection').onclick=async()=>{if(collection&&!globalSearch()&&await update({action:'members',id:collection,ids:[...selected],remove:true})){selected.clear();render();}};
$('delete-selected').onclick=()=>{if(busy||!manage||!mediaAccess.available||catalog.restored)return;const ids=centerMediaIds(catalog.items.filter(item=>selected.has(item.id)));if(!ids.length)return;if(ids.length>200){message('所选作品包含超过200个文件，请减少选择后预览。');return;}openFileReview('delete',ids);};
$('compare-selected').onclick=()=>{const chosen=catalog.items.filter(item=>selected.has(item.id));if(chosen.length===2&&chosen.every(item=>item.kind==='photo'&&item.files.length===1))openFileReview('compare',centerMediaIds(chosen));};
$('detail-close').onclick=()=>$('media-detail').close();$('media-detail').addEventListener('keydown',event=>{if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;const item=detailSequence[detailIndex];if(item?.kind!=='photo')return;if(event.key==='ArrowRight'&&detailIndex<detailSequence.length-1){event.preventDefault();detailIndex++;renderDetail();}else if(event.key==='ArrowLeft'&&detailIndex>0){event.preventDefault();detailIndex--;renderDetail();}});
$('center-prev').onclick=()=>{page--;render();};$('center-next').onclick=()=>{page++;render();};$('center-refresh').onclick=load;
function navigate(){const next=location.hash.slice(1);view=titles[next]?next:'home';$('query').value='';$('search-scope').value='view';$('media-kind').value=$('filter-year').value=$('filter-genre').value=$('filter-duration').value=$('filter-resolution').value='';$('media-order').value='recent';$('include-subfolders').checked=false;manage=false;resetLocation();if($('media-detail').open)$('media-detail').close();render();}
window.addEventListener('hashchange',navigate);navigate();load();
}
