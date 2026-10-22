// Reports reuse the same preview and confirmation as photo/file browsing.
function reportActionOpen(action,ids){
  if(!['delete','compare'].includes(action)||ids.length!==(action==='delete'?1:2)||ids.some(id=>!/^[0-9a-f]{64}$/.test(id)))return;
  const dialog=document.createElement('dialog'),frame=document.createElement('iframe'),close=document.createElement('button');
  dialog.setAttribute('aria-label',action==='delete'?'删除文件预览':'对比照片');
  dialog.style.cssText='width:min(1200px,96vw);height:94vh;padding:12px;border:1px solid #adbbb3;border-radius:12px';
  close.textContent='返回文件列表';close.onclick=()=>dialog.close();
  frame.title=action==='delete'?'核对后移到废纸篓 / 回收站':'并排比较并选择删除';
  frame.style.cssText='display:block;width:100%;height:calc(100% - 48px);border:0;margin-top:8px';
  frame.src='organize.html#'+action+'='+ids.join(',');
  dialog.append(close,frame);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
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
addReportFileActions();
