const byId = id => document.getElementById(id);
const node = (tag, text, cls) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
let userData=null,userBusy=false,userOperating=true,userSelected=new Set(),userPage=0,userEditing=null,userDeleting=null,userPreview=null;
let userMediaIds={},userMediaAvailable=false;
function userError(text, id='user-error'){byId(id).textContent=text||'';byId(id).hidden=!text;}
function filteredUserItems(data, query, owner, kind){
  const search=query.trim().toLocaleLowerCase();
  return data.items.filter(item=>(!kind||item.kind===kind)&&(!owner||item.user_id===(owner==='unassigned'?'':owner))&&
    (!search||[item.title,item.username,...item.files.map(file=>file.path)].some(value=>String(value).toLocaleLowerCase().includes(search))));
}
function userControls(){
  const locked=userBusy||userOperating||!userData;
  for(const id of ['user-create','user-editor-save','user-delete-confirm','user-assign','user-plan','user-plan-save'])byId(id).disabled=locked;
  byId('user-assign').disabled=locked||!userSelected.size||userSelected.size>200;
  byId('user-plan').disabled=locked||!userSelected.size||userSelected.size>200;
  byId('user-plan-save').disabled=locked||!userPreview;
  for(const id of ['user-name','user-editor-cancel','user-delete-cancel','user-plan-cancel','user-refresh'])byId(id).disabled=userBusy;
  byId('user-selected').textContent=`已勾选 ${userSelected.size} 项（跨页保留）`;
}
function renderUsers(){
  userControls();if(!userData)return;
  byId('user-list').replaceChildren();
  const query=byId('user-search').value.trim().toLocaleLowerCase();
  for(const user of userData.users.filter(user=>user.name.toLocaleLowerCase().includes(query))){
    const box=node('div',undefined,'user');box.append(node('strong',user.name),node('p',`本次 ${user.media_count} 项 · 历次归属 ${user.file_count} 个媒体文件`,'muted'));
    const show=node('button','查看媒体');show.onclick=()=>{byId('user-filter').value=user.id;userPage=0;renderUsers();};
    const rename=node('button','改名');rename.disabled=userBusy||userOperating;rename.onclick=()=>editUser(user);
    const remove=node('button','删除');remove.className='danger';remove.disabled=userBusy||userOperating;remove.onclick=()=>{
      userDeleting=user;userError('','user-delete-error');byId('user-delete-summary').textContent=`删除“${user.name}”？将解除历次记录中 ${user.file_count} 个媒体文件的归属。`;byId('user-delete-dialog').showModal();};
    box.append(show,rename,remove);byId('user-list').append(box);
  }
  if(!byId('user-list').childNodes.length)byId('user-list').append(node('p','没有匹配的用户名，可以新增。','muted'));
  const items=filteredUserItems(userData,byId('media-search').value,byId('user-filter').value,byId('media-kind').value);
  const pages=Math.max(1,Math.ceil(items.length/50));userPage=Math.min(userPage,pages-1);byId('user-media').replaceChildren();
  for(const item of items.slice(userPage*50,userPage*50+50)){
    const card=node('article',undefined,'card');
    if(typeof item.preview==='string'&&/^(previews|covers)\/[A-Za-z0-9_-]+\.png$/.test(item.preview)){const image=node('img');image.src=item.preview;image.alt=item.title;image.loading='lazy';card.append(image);}else card.append(node('div','预览未提供','placeholder'));
    const label=node('label'),check=node('input');check.type='checkbox';check.checked=userSelected.has(item.id);check.disabled=userBusy;check.setAttribute('aria-label','选择 '+item.title);check.onchange=()=>{if(check.checked)userSelected.add(item.id);else userSelected.delete(item.id);userControls();};label.append(check,node('span',item.title));
    card.append(label,node('span',item.username,'badge'),node('p',`${item.kind} · ${item.files.length} 个媒体文件`,'muted'));
    const details=node('details');details.append(node('summary','查看原位置与打开文件'));for(const file of item.files){details.append(node('p',file.path,'path'));if(userMediaAvailable&&userMediaIds[file.path]===file.id){for(const [action,text] of [['open',item.kind==='照片'?'打开原图':'播放影片'],['reveal','定位文件']]){const button=node('button',text);button.disabled=userBusy||userOperating;button.onclick=async()=>{button.disabled=true;try{const result=await userApi({id:file.id,action},'api/media/action');byId('user-notice').textContent=result.message;}catch(error){userError(error.message);}finally{button.disabled=userBusy||userOperating;}};details.append(button);}}}card.append(details);byId('user-media').append(card);
  }
  if(!items.length)byId('user-media').append(node('p','没有符合条件的照片或电影。'));
  byId('user-page').textContent=`${items.length} 项 · 第 ${userPage+1}/${pages} 页`;
  byId('user-previous').disabled=userBusy||userPage===0;byId('user-next').disabled=userBusy||userPage>=pages-1;
}
function userChoices(){
  for(const [id,initial] of [['user-filter',[['','全部用户名'],['unassigned','未分配'],['mixed','归属不一致']]],['assign-user',[['','未分配']]]]){
    const select=byId(id),previous=select.value;select.replaceChildren();
    for(const [value,text] of [...initial,...userData.users.map(user=>[user.id,user.name])]){const option=node('option',text);option.value=value;select.append(option);}
    if([...select.options].some(option=>option.value===previous))select.value=previous;
  }
}
async function userApi(payload, route='api/users'){
  const response=await fetch(route,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'请求失败');return result;
}
async function refreshUsers(){
  if(userBusy)return;userBusy=true;userPreview=null;userError();renderUsers();
  try{const [data,operations,media]=await Promise.all([userApi(),userApi(null,'api/operations'),userApi(null,'api/media')]);userData=data;userMediaIds=media.ids_by_path||{};userMediaAvailable=media.available===true;userOperating=operations.jobs.some(job=>['running','external_running'].includes(job.status))||!!operations.warnings.length;userSelected.clear();userChoices();byId('user-notice').textContent=userOperating?'当前文件操作未完成或记录需核对，暂缓修改用户分类。':'已读取最新用户名与归属。';}
  catch(error){userOperating=true;userError(error.message);}finally{userBusy=false;renderUsers();}
}
async function changeUser(payload,errorId='user-error'){
  if(userBusy||userOperating)return false;userBusy=true;userError('',errorId);renderUsers();
  try{userData=await userApi({...payload,revision:userData.revision});userChoices();byId('user-notice').textContent='用户分类已保存；重新打开和同路径重新扫描后仍保留。';return true;}
  catch(error){userError(error.message+'；请取消当前操作后刷新分类。',errorId);return false;}finally{userBusy=false;renderUsers();}
}
function editUser(user){userEditing=user;byId('user-editor-title').textContent=user?'修改用户名':'新增用户名';byId('user-name').value=user?.name||'';userError('','user-editor-error');byId('user-editor').showModal();byId('user-name').focus();}
byId('user-create').onclick=()=>editUser(null);
byId('user-form').onsubmit=async event=>{event.preventDefault();if(await changeUser({action:userEditing?'rename':'create',user_id:userEditing?.id,name:byId('user-name').value},'user-editor-error'))byId('user-editor').close();};
byId('user-delete-confirm').onclick=async()=>{if(await changeUser({action:'delete',user_id:userDeleting.id},'user-delete-error'))byId('user-delete-dialog').close();};
byId('user-assign').onclick=async()=>{if(await changeUser({action:'assign',user_id:byId('assign-user').value,ids:[...userSelected]}))renderUsers();};
for(const id of ['user-editor','user-delete','user-plan']){byId(id+'-cancel').onclick=()=>byId(id==='user-editor'?id:id+'-dialog').close();byId(id==='user-editor'?id:id+'-dialog').addEventListener('cancel',event=>{if(userBusy)event.preventDefault();});}
byId('user-plan').onclick=async()=>{
  if(userBusy||userOperating||!userSelected.size)return;userBusy=true;userPreview=null;userError();renderUsers();
  try{const ids=[...userSelected],revision=userData.revision;const result=await userApi({action:'plan-preview',ids,revision});userPreview={...result,selected_ids:ids,user_revision:revision};
    byId('user-plan-items').replaceChildren();for(const item of result.items){const row=node('li');row.append(node('div',item.path,'path'),node('div',item.before+' → '+item.after,'path'));byId('user-plan-items').append(row);}
    byId('user-plan-summary').textContent=`${result.items.length} 个媒体文件 · ${result.changed_count} 项位置变化，保存后需重新核对。`;userError('','user-plan-error');byId('user-plan-dialog').showModal();
  }catch(error){userError(error.message);}finally{userBusy=false;renderUsers();}
};
byId('user-plan-save').onclick=async()=>{
  if(userBusy||userOperating||!userPreview)return;const preview=userPreview;userBusy=true;userError('','user-plan-error');renderUsers();
  try{const result=await userApi({action:'plan-apply',ids:preview.selected_ids,revision:preview.user_revision,plan_revision:preview.revision});byId('user-plan-dialog').close();userPreview=null;
    byId('user-notice').replaceChildren(node('span','分类计划已保存。请在整理页核对、纳入计划，再预览确认复制。 '));const link=node('a','打开本批整理计划','button');link.href='organize.html#user-plan='+result.ids.join(',');byId('user-notice').append(link);
  }catch(error){userPreview=null;userError(error.message+'；请取消后刷新分类并重新预览。','user-plan-error');}finally{userBusy=false;renderUsers();}
};
byId('user-refresh').onclick=refreshUsers;
byId('user-search').oninput=renderUsers;
for(const id of ['media-search','user-filter','media-kind'])byId(id).addEventListener(id==='media-search'?'input':'change',()=>{userPage=0;renderUsers();});
byId('user-reset').onclick=()=>{for(const id of ['media-search','user-filter','media-kind'])byId(id).value='';userPage=0;renderUsers();};
byId('user-clear').onclick=()=>{userSelected.clear();renderUsers();};
byId('user-select-page').onclick=()=>{if(!userData||userBusy)return;const items=filteredUserItems(userData,byId('media-search').value,byId('user-filter').value,byId('media-kind').value).slice(userPage*50,userPage*50+50);for(const item of items)userSelected.add(item.id);renderUsers();};
byId('user-previous').onclick=()=>{userPage--;renderUsers();};byId('user-next').onclick=()=>{userPage++;renderUsers();};
refreshUsers();
