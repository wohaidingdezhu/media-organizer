// Saved scan statistics and manual candidates; neither action executes cleanup.
let basketIds=new Set(),basketReady=false,basketLoading=false,basketError='',basketLimit=200;
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
function acceptBasket(result){basketIds=new Set(result.ids);if($('risk').value==='basket')for(const id of selected)if(!basketIds.has(id))selected.delete(id);basketLimit=result.limit;basketReady=true;basketError='';}
async function refreshBasket(){
  if(basketLoading||(saving&&basketReady))return;
  basketLoading=true;updateBasketControls();renderStorage();
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
const renderStoragePanel = mountStoragePanel($('storage-section'), {
  load: () => api('api/storage'),
  formatSize: fileSize,
  filter: storageFilter,
  add: id => mutateBasket('add', [id]),
});
function renderStorage() {
  renderStoragePanel({
    saving: saving || !data,
    canAdd: basketReady && !basketLoading && !saving && !operating,
    basket: basketIds,
    handled: handledTrash,
  });
}
renderStorage();
