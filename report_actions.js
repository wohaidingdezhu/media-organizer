// Reports reuse the same preview and confirmation as photo/file browsing.
let reportFocusReady=false,reportScrollLock=null;
function reportOverlay(open){
  if(typeof window==='undefined')return;
  open=open||!!document.querySelector?.('dialog[open]');
  const root=document.documentElement||document.body;
  if(open&&!reportScrollLock){reportScrollLock={body:document.body.style.overflow,root:root.style.overflow};document.body.style.overflow=root.style.overflow='hidden';}
  if(!open&&reportScrollLock){document.body.style.overflow=reportScrollLock.body;root.style.overflow=reportScrollLock.root;reportScrollLock=null;}
  if(window.parent===window)return;
  const link=document.querySelector?.('nav[aria-label="报告导航"] a');
  if(link)window.parent.postMessage({type:'media-overlay',open},new URL(link.href).origin);
}
function reportTrashOpen(ids,onClose,summary=''){
  if(!Array.isArray(ids)||ids.length<1||ids.length>200||new Set(ids).size!==ids.length||ids.some(id=>!/^[0-9a-f]{64}$/.test(id)))return;
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  const dialog=make('dialog'),title=make('h2','移到废纸篓 / 回收站？'),message=make('p','正在检查所选文件…'),list=make('ul'),content=make('div'),actions=make('div'),cancel=make('button','取消'),confirm=make('button','确认删除'),stop=make('button','停止处理');
  dialog.setAttribute('aria-label','确认移到废纸篓 / 回收站');
  dialog.style.cssText='width:min(680px,92vw);max-height:90vh;overflow:hidden;flex-direction:column;box-sizing:border-box;overflow-wrap:anywhere;word-break:break-word;padding:24px;border:1px solid #d7d3e3;border-radius:16px;background:#fcfcfe;color:#272a36';
  content.style.cssText='flex:1;min-height:0;overflow-x:hidden;overflow-y:auto;overflow-wrap:anywhere;word-break:break-word';list.style.cssText='padding-left:24px;overflow-wrap:anywhere;word-break:break-word';title.style.cssText='flex:none;margin:0 0 12px';
  actions.style.cssText='display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap;flex:none;background:#fcfcfe;padding:14px 0 0';
  for(const b of [cancel,confirm,stop]){b.type='button';b.style.cssText='padding:10px 16px;border:1px solid #d7d3e3;border-radius:8px;cursor:pointer';}confirm.style.color='#a33b4a';confirm.disabled=true;stop.hidden=true;
  message.setAttribute('role','status');actions.append(cancel,stop,confirm);content.append(message);dialog.append(title);if(summary){const kept=make('p',summary),notes=make('details'),caption=make('summary','保留文件与清理说明（展开查看）');kept.style.cssText='max-width:100%;overflow-wrap:anywhere;word-break:break-word';notes.append(caption,kept);content.append(notes);}content.append(list);dialog.append(content,actions);
  let preview=null,running=false,closed=false,jobId='',timer=null;
  const api=async(path,body)=>{const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(result.error||'请求未完成');return result;};
  const finish=()=>{running=false;stop.hidden=true;cancel.disabled=false;cancel.textContent='返回列表';};
  const poll=async()=>{if(closed)return;try{const job=await api('api/operations/'+jobId);if(closed)return;if(job.id!==jobId||job.mode!=='trash')throw Error('操作记录不匹配');
    if(job.status==='complete'){running=false;dialog.close();return;}
    if(['running','external_running'].includes(job.status)){message.textContent='正在移到回收站，请稍候…';timer=setTimeout(poll,1000);return;}
    finish();message.textContent='处理已停止。已完成的文件在回收站中，其余文件未自动重试。';list.replaceChildren();
    for(const item of job.items||[]){const row=make('li',(item.status==='complete'||item.status==='success'?'已完成：':'待核对：')+item.path+(item.error?' · '+item.error:''));list.append(row);}
  }catch(error){if(closed)return;message.textContent='连接暂时中断，正在核对处理结果，请勿重复删除：'+error.message;timer=setTimeout(poll,3000);}};
  cancel.onclick=()=>{if(!running)dialog.close();};
  dialog.addEventListener('cancel',event=>{if(running)event.preventDefault();});
  dialog.addEventListener('close',()=>{closed=true;clearTimeout(timer);dialog.remove();reportOverlay(false);if(typeof onClose==='function')onClose();});
  stop.onclick=async()=>{if(!jobId)return;stop.disabled=true;try{await api('api/operations/stop',{id:jobId});message.textContent='正在安全停止…';}catch(error){message.textContent='停止请求未确认：'+error.message;stop.disabled=false;}};
  confirm.onclick=async()=>{if(running||!preview?.token)return;running=true;confirm.disabled=true;cancel.disabled=true;const token=preview.token;preview=null;message.textContent='正在提交…';
    try{const started=await api('api/operations/start',{token});jobId=started.id;stop.hidden=false;await poll();}
    catch(error){finish();message.textContent='启动结果未确认，请在整理工具中核对操作记录后再处理：'+error.message;}
  };
  document.body.append(dialog);dialog.showModal();dialog.style.display='flex';reportOverlay(true);
  api('api/operations/preview',{mode:'trash',ids}).then(result=>{if(closed)return;preview=result;message.textContent=result.items.length+' 个文件将移到系统回收站，可以恢复。';for(const item of result.items)list.append(make('li',item.path));confirm.disabled=!result.token;}).catch(error=>{if(!closed){message.textContent=error.message;cancel.textContent='返回列表';}});
}
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
function reportActionOpen(action,ids,onClose,summary=''){
  if(action==='delete')return reportTrashOpen(ids,onClose,summary);
  if(action!=='compare'||!Array.isArray(ids)||ids.length!==2||ids[0]===ids[1]||ids.some(id=>!/^[0-9a-f]{64}$/.test(id)))return;
  const make=(tag,text)=>{const node=document.createElement(tag);if(text)node.textContent=text;return node;},dialog=make('dialog'),title=make('h2','对比照片'),status=make('p','正在读取照片…'),grid=make('div'),close=make('button','返回列表');
  dialog.setAttribute('aria-label','对比照片');dialog.style.cssText='width:min(1100px,94vw);max-height:90vh;overflow-x:hidden;overflow-y:auto;overflow-wrap:anywhere;padding:24px;border:1px solid #d7d3e3;border-radius:16px;background:#fcfcfe;color:#272a36';grid.style.cssText='display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:20px';close.style.cssText='margin:16px 0;padding:10px 16px';close.onclick=()=>dialog.close();let closed=false,handoff=false;
  dialog.append(title,status,grid,close);dialog.addEventListener('close',()=>{closed=true;dialog.remove();reportOverlay(false);if(!handoff&&typeof onClose==='function')onClose();});document.body.append(dialog);dialog.showModal();reportOverlay(true);
  Promise.all(['api/center','api/media'].map(async path=>{const response=await fetch(path),value=await response.json();if(!response.ok)throw Error(value.error||'读取失败');return value;})).then(([data,access])=>{
    if(closed)return;const photos=data.items.filter(item=>item.kind==='photo').flatMap(item=>item.files.map(file=>({...file,preview:item.preview,metadata:item.metadata})));const chosen=ids.map(id=>photos.find(file=>file.id===id));if(chosen.some(file=>!file))throw Error('照片已不在当前清单，请刷新列表。');status.textContent='并排核对后选择要删除的照片。相似不代表内容完全相同。';
    for(const file of chosen){const card=make('section');card.append(make('h3',file.name));if(/^(previews|covers)\/[A-Za-z0-9_-]+\.png$/.test(file.preview||'')){const image=make('img');image.src=file.preview;image.alt=file.name;image.style.cssText='display:block;width:100%;height:45vh;object-fit:contain;background:#eeebf4';card.append(image);}else card.append(make('p','尚未生成图片预览'));
      const path=make('p',file.path);path.style.cssText='overflow-wrap:anywhere;font-size:12px';card.append(path);const trash=make('button','删除这张…');trash.style.cssText='margin:8px 0;padding:10px 16px';trash.disabled=!access.available||data.restored||data.operation_state?.running||data.operation_state?.warnings?.length||access.ids_by_path[file.path]!==file.id;
      trash.onclick=()=>{if(trash.disabled)return;handoff=true;dialog.close();reportTrashOpen([file.id],onClose);};card.append(trash);grid.append(card);
    }
  }).catch(error=>{if(!closed)status.textContent=error.message;});
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
