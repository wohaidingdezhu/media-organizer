// Saved scan statistics and manual candidates; neither action executes cleanup.
let basketIds=new Set(),basketReady=false,basketLoading=false,basketError='',basketLimit=200;
let storageData=null,storageLoading=false,storagePage=0;
function updateBasketControls(){
  if(!data)return;
  const chosen=data.items.filter(item=>selected.has(item.id)),trashed=data.items.filter(item=>basketIds.has(item.id)&&handledTrash.has(item.path)).length;
  const busy=saving||operating||basketLoading;
  $('basket-summary').textContent=basketError||(!basketReady?'正在读取候选篮…':`${basketIds.size}/${basketLimit} 个候选 · ${fileSize(data.items.filter(item=>basketIds.has(item.id)).reduce((sum,item)=>sum+item.bytes,0))} 逻辑大小${trashed?' · '+trashed+' 项已有清理成功记录，可逐项移出':''}。候选记录仅属于本次扫描。`);
  $('basket-summary').className=basketError?'warning':'';
  $('basket-add').disabled=!basketReady||busy||!chosen.some(item=>!basketIds.has(item.id)&&!handledTrash.has(item.path));
  $('basket-view').disabled=!basketReady||saving;
  $('basket-select').disabled=!basketReady||saving||operating||!data.items.some(item=>basketIds.has(item.id)&&!handledTrash.has(item.path)&&mediaIds[item.path]);
  $('basket-remove').disabled=!basketReady||busy||!chosen.some(item=>basketIds.has(item.id));
  $('basket-clear').disabled=!basketReady||busy||!basketIds.size;
  $('basket-refresh').disabled=saving||basketLoading;
}
function appendBasketStatus(item,parent){
  if(!basketIds.has(item.id))return;
  const row=make('div','target-actions');row.append(make('span','badge warning','清理候选'));
  const remove=make('button','','移出候选篮');remove.setAttribute('aria-label','移出候选篮 '+item.name);remove.disabled=!basketReady||saving||operating||basketLoading;
  remove.onclick=()=>mutateBasket('remove',[item.id]);row.append(remove);parent.append(row);
}
function acceptBasket(result){basketIds=new Set(result.ids);if($('risk').value==='basket')for(const id of selected)if(!basketIds.has(id))selected.delete(id);basketLimit=result.limit;basketReady=true;basketError='';}
async function refreshBasket(){
  if(basketLoading||(saving&&basketReady))return;
  basketLoading=true;updateBasketControls();
  try{acceptBasket(await api('api/basket'));}
  catch(err){basketReady=false;basketError='候选篮读取失败，候选记录已保留：'+err.message;}
  finally{basketLoading=false;render();}
}
async function mutateBasket(action,ids){
  if(saving||operating||basketLoading||!basketReady)return;
  saving=true;basketError='';error();render();
  try{
    acceptBasket(await api('api/basket',{action,ids}));
    $('notice').textContent=action==='add'?'候选已保存，可继续搜索其他文件；原媒体未变化。':action==='remove'?'已移出候选篮；原媒体未变化。':'候选篮已清空；原媒体未变化。';
  }catch(err){basketError=err.message;error('候选篮未更新：'+err.message);}
  finally{saving=false;render();}
}
function resetListFilters(){for(const id of filters)$(id).value='';$('view-mode').value='list';page=0;selected.clear();}
function showBasket(selectAll){
  if(saving||!basketReady||!data||(selectAll&&operating))return;
  resetListFilters();$('risk').value='basket';
  if(selectAll){selected=new Set(data.items.filter(item=>basketIds.has(item.id)&&!handledTrash.has(item.path)&&mediaIds[item.path]).map(item=>item.id));$('notice').textContent=`已勾选候选篮中 ${selected.size} 个可预览文件，仍须核对并重新预览确认；跳过已清理或缺少操作信息的项。`;}
  render();$('selection').scrollIntoView({block:'start'});
}
$('basket-add').onclick=()=>mutateBasket('add',data.items.filter(item=>selected.has(item.id)&&!basketIds.has(item.id)&&!handledTrash.has(item.path)).map(item=>item.id));
$('basket-remove').onclick=()=>mutateBasket('remove',[...selected].filter(id=>basketIds.has(id)));
$('basket-clear').onclick=()=>mutateBasket('clear',[]);
$('basket-view').onclick=()=>showBasket(false);$('basket-select').onclick=()=>showBasket(true);$('basket-refresh').onclick=refreshBasket;

async function loadStorage(){
  if(storageLoading||storageData)return;
  storageLoading=true;$('storage-summary').textContent='正在读取扫描时的媒体大小…';$('storage-retry').hidden=true;
  try{storageData=await api('api/storage');renderStorage();}
  catch(err){$('storage-summary').textContent='空间分析读取失败：'+err.message;$('storage-retry').hidden=false;}
  finally{storageLoading=false;}
}
function storageFilter(field,value){
  if(saving||!data)return;
  resetListFilters();$('sort').value='size';
  if(field==='folder'){
    if(![...$('source-folder').options].some(option=>option.value===value)){const option=make('option','',value);option.value=value;$('source-folder').append(option);}
    $('source-folder').value=value;
  }else if(field==='kind')$('kind').value=value;
  else if(field==='path')$('search').value=value;
  else if(field==='month'){
    $('kind').value='照片';
    if(![...$('photo-month').options].some(option=>option.value===value)){const option=make('option','',value);option.value=value;$('photo-month').append(option);}
    $('photo-month').value=value;
  }
  $('storage-panel').open=false;render();$('selection').scrollIntoView({block:'start'});
}
function renderStorage(){
  if(!storageData||!$('storage-panel').open)return;
  $('storage-summary').replaceChildren();
  for(const [label,value] of [['扫描媒体逻辑大小',fileSize(storageData.logical_bytes)],['精确重复副本逻辑大小',fileSize(storageData.duplicate_logical_bytes)],['报告标记的硬链接引用',String(storageData.hardlink_references)]]){
    const card=make('div','stat');card.append(make('span','',label),make('strong','',value));$('storage-summary').append(card);
  }
  $('storage-warnings').replaceChildren();for(const warning of storageData.warnings)$('storage-warnings').append(make('p','warning',warning));
  const view=$('storage-view').value,rows=storageData[view],pages=Math.max(1,Math.ceil(rows.length/20));storagePage=Math.min(storagePage,pages-1);
  $('storage-rows').replaceChildren();
  for(const item of rows.slice(storagePage*20,storagePage*20+20)){
    const row=make('div','storage-row'),main=make('div');main.append(make('strong','',item.name),make('div','path',view==='largest'?item.path:`${item.count} 个媒体文件`));
    const percent=storageData.logical_bytes?100*item.bytes/storageData.logical_bytes:0;
    main.append(make('p','muted',`${fileSize(item.bytes)} · 占已扫描媒体逻辑大小 ${percent.toFixed(1)}%${item.hardlink?' · 硬链接引用':''}`));
    const bar=make('progress');bar.max=100;bar.value=percent;bar.setAttribute('aria-label',item.name+' 大小比例');main.append(bar);row.append(main);
    const actions=make('div','target-actions');
    const field={folders:'folder',kinds:'kind',largest:'path',photo_months:'month'}[view];
    if(field&&(view!=='photo_months'||/^\d{4}\/\d{2}$/.test(item.name))){const button=make('button','',view==='largest'?'查看此文件':'查看这些文件');button.disabled=saving;button.setAttribute('aria-label','查看文件 '+(item.path||item.name));button.onclick=()=>storageFilter(field,item.path||item.name);actions.append(button);}
    if(view==='largest'){
      const add=make('button','',basketIds.has(item.id)?'已在候选篮':'加入候选篮');add.setAttribute('aria-label','加入候选篮 '+item.name);add.disabled=!basketReady||basketLoading||basketIds.has(item.id)||handledTrash.has(item.path)||saving||operating;add.onclick=()=>mutateBasket('add',[item.id]);actions.append(add);
      if(handledTrash.has(item.path))actions.append(make('span','warning','已有清理成功记录'));
    }
    row.append(actions);$('storage-rows').append(row);
  }
  if(!rows.length)$('storage-rows').append(make('p','empty','没有此维度的媒体记录。'));
  $('storage-page').textContent=`${view==='largest'?'最多展示前 '+storageData.largest_limit+' 个大文件；':''}共 ${rows.length} 项 · 第 ${storagePage+1}/${pages} 页 · 按逻辑大小排序`;
  $('storage-previous').disabled=storagePage===0;$('storage-next').disabled=storagePage>=pages-1;
}
$('storage-panel').addEventListener('toggle',()=>{if($('storage-panel').open){if(storageData)renderStorage();else loadStorage();}});
$('storage-view').onchange=()=>{storagePage=0;renderStorage();};$('storage-retry').onclick=loadStorage;
$('storage-previous').onclick=()=>{storagePage--;renderStorage();$('storage-view').scrollIntoView({block:'start'});};
$('storage-next').onclick=()=>{storagePage++;renderStorage();$('storage-view').scrollIntoView({block:'start'});};
