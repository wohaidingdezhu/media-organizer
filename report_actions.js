// Reports reuse the same preview and confirmation as photo/file browsing.
let reportFocusReady=false;
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
function reportActionOpen(action,ids){
  if(!['delete','compare'].includes(action)||(action==='delete'?(ids.length<1||ids.length>200):ids.length!==2)||ids.some(id=>!/^[0-9a-f]{64}$/.test(id)))return;
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
focusReportSection();
if(typeof window!=='undefined')window.addEventListener('hashchange',focusReportSection);
addReportFileActions();
