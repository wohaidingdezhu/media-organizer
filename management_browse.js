// Report-only sorting shared by the file list and photo wall.
function sortManagementItems(items, order) {
  const nameOrder = (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN', {numeric: true}) ||
    String(a.path || '').localeCompare(String(b.path || ''), 'zh-CN', {numeric: true});
  const bytes = item => Number.isSafeInteger(item.bytes) && item.bytes >= 0 ? item.bytes : 0;
  const time = item => typeof item.mtime === 'number' && Number.isFinite(item.mtime) ? item.mtime : null;
  return [...items].sort((a, b) => {
    if (order === 'name') return nameOrder(a, b);
    if (order === 'size') return bytes(b) - bytes(a) || nameOrder(a, b);
    if (order === 'newest' || order === 'oldest') {
      const left = time(a), right = time(b);
      if (left === null || right === null) return (left === null) - (right === null) || nameOrder(a, b);
      return (order === 'newest' ? right - left : left - right) || nameOrder(a, b);
    }
    return 0;
  });
}

function photoBrowseSequence(visible, start) {
  const photos = visible.filter(item => item.kind === '照片');
  return photos.some(item => item.id === start.id) ? photos : [start];
}

if (typeof module !== 'undefined' && module.exports)
  module.exports = {sortManagementItems, photoBrowseSequence};

// Folder keys keep full ancestry, case and Unicode; equal leaf names never merge.
function managementFolderParts(item, mode) {
  if(mode==='result')return item.result_parts||[item.folder];
  if (mode === 'target') return String(item.suggested_path || '').split('/').slice(0, -1).filter(Boolean);
  const folder = String(item.source_folder || '');
  if (/^[A-Za-z]:[\\/]/.test(folder)) return [folder.slice(0, 2) + '\\', ...folder.slice(3).split(/[\\/]/).filter(Boolean)];
  if (folder.startsWith('\\\\')) {const parts=folder.slice(2).split('\\').filter(Boolean);return ['\\\\'+parts.slice(0,2).join('\\')+'\\', ...parts.slice(2)];}
  return folder.startsWith('/') ? ['/', ...folder.slice(1).split('/').filter(Boolean)] : ['来源未提供', ...(folder?[folder]:[])];
}
function managementFolderTree(items, mode) {
  const nodes=new Map(), root={key:'',name:'全部文件夹',path:'',parts:[],parent:null,count:0,bytes:0,photos:0,videos:0,direct:0,children:[]};nodes.set('',root);
  for(const item of items){const parts=managementFolderParts(item,mode);let parent=root;
    for(let depth=0;depth<=parts.length;depth++){
      if(depth){const prefix=parts.slice(0,depth),key=JSON.stringify(prefix);let node=nodes.get(key);
        if(!node){const windows=(mode==='result'?/^[A-Za-z]:[\\/]/:/^[A-Za-z]:\\$/).test(prefix[0])||prefix[0].startsWith('\\\\');const path=mode==='result'?prefix[0]+(prefix.length>1?(windows?'\\':'/')+prefix.slice(1).join(windows?'\\':'/'):''):mode==='target'?prefix.join('/'):(prefix[0]==='/'?'/'+prefix.slice(1).join('/'):windows?prefix[0]+prefix.slice(1).join('\\'):prefix.join('/'));node={key,name:prefix[depth-1],path,parts:prefix,parent:parent.key,count:0,bytes:0,photos:0,videos:0,direct:0,children:[]};nodes.set(key,node);parent.children.push(key);}parent=node;}
      parent.count++;parent.bytes+=Number.isSafeInteger(item.bytes)&&item.bytes>=0?item.bytes:0;parent.photos+=item.kind==='照片';parent.videos+=item.kind==='视频';if(depth===parts.length)parent.direct++;
    }
  }
  for(const node of nodes.values())node.children.sort((a,b)=>nodes.get(a).name.localeCompare(nodes.get(b).name,'zh-CN',{numeric:true}));
  return nodes;
}
function managementFolderContains(item, node, mode, descendants=true) {
  if(!node||!node.parts.length)return true;const parts=managementFolderParts(item,mode);
  return (descendants?parts.length>=node.parts.length:parts.length===node.parts.length)&&node.parts.every((part,index)=>part===parts[index]);
}
function managementFolderRows(nodes, active, mode, query='', order='name') {
  const needle=query.trim().normalize('NFC').toLocaleLowerCase('zh-CN');let rows;
  if(needle)rows=[...nodes.values()].filter(node=>node.key&&node.key!==active.key&&node.parts.length>=active.parts.length&&active.parts.every((part,index)=>part===node.parts[index])&&node.path.normalize('NFC').toLocaleLowerCase('zh-CN').includes(needle));
  else {let children=active.children;while(mode==='source'&&!active.key&&children.length===1&&!nodes.get(children[0]).direct&&nodes.get(children[0]).children.length===1)children=nodes.get(children[0]).children;rows=children.map(key=>nodes.get(key));}
  const byName=(a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true})||a.path.localeCompare(b.path,'zh-CN',{numeric:true})||(a.key<b.key?-1:a.key>b.key?1:0);
  return rows.sort((a,b)=>(order==='size'?b.bytes-a.bytes:order==='count'?b.count-a.count:0)||byName(a,b));
}
function createManagementFolderBrowser({host,items,locked,changed,selectable,select,formatSize}) {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  let mode='source',key='',descendants=true,folderPage=0,nodes=new Map(),active=null,prepared=false;
  const locations=new Map(),folderSearch=make('input'),folderSort=make('select');folderSearch.type='search';folderSearch.placeholder='搜索当前目录下的文件夹名称或路径…';folderSearch.setAttribute('aria-label','搜索文件夹');folderSort.setAttribute('aria-label','文件夹排序');
  for(const [value,text] of [['name','文件夹名称顺序'],['size','文件夹逻辑大小从大到小'],['count','媒体数量从多到少']]){const option=make('option',text);option.value=value;folderSort.append(option);}folderSort.value='name';
  const title=make('h2','文件夹管理'),controls=make('div'),modeSelect=make('select'),within=make('input'),withinLabel=make('label',' 包含子文件夹'),top=make('button','全部文件夹'),up=make('button','返回上级'),pick=make('button','勾选目录内筛选结果'),summary=make('p'),cards=make('div'),pager=make('div'),previous=make('button','上一页文件夹'),next=make('button','下一页文件夹'),position=make('span');
  controls.className='manage-controls';cards.className='folder-grid';pager.className='pager';summary.setAttribute('role','status');modeSelect.setAttribute('aria-label','文件夹浏览目录');within.type='checkbox';within.checked=true;within.setAttribute('aria-label','包含子文件夹');withinLabel.prepend(within);
  for(const [value,text] of [['source','磁盘原有文件夹'],['target','整理分类目录（计划）']]){const option=make('option',text);option.value=value;modeSelect.append(option);}
  controls.append(modeSelect,withinLabel,top,up,pick,folderSearch,folderSort);pager.append(previous,position,next);host.append(title,make('p','仅展示本次扫描记录中的媒体目录、数量和逻辑大小，不含其他文件、附件或空目录；分类目录是整理计划，原文件位置请看磁盘目录。搜索只查当前目录下的文件夹，不改变下方媒体筛选和勾选；点击结果进入目录。'),controls,summary,cards,pager);
  const navigate=value=>{if(locked())return;key=value;folderPage=0;folderSearch.value='';changed();};
  modeSelect.onchange=()=>{if(locked())return;locations.set(mode,{key,descendants,folderPage,query:folderSearch.value,order:folderSort.value});mode=modeSelect.value;const last=locations.get(mode);key=last?.key||'';descendants=last?.descendants??true;folderPage=last?.folderPage||0;folderSearch.value=last?.query||'';folderSort.value=last?.order||'name';within.checked=descendants;changed();};within.onchange=()=>{if(locked())return;descendants=within.checked;changed();};top.onclick=()=>navigate('');up.onclick=()=>navigate(active?.parent||'');
  folderSearch.oninput=()=>{if(locked())return;folderPage=0;render();};folderSort.onchange=()=>{if(locked())return;folderPage=0;render();};
  previous.onclick=()=>{if(locked())return;folderPage=Math.max(0,folderPage-1);render();};next.onclick=()=>{if(locked())return;folderPage++;render();};pick.onclick=()=>{if(locked())return;const chosen=selectable();if(!chosen.length||chosen.length>200)return;select(chosen);};
  function prepare(){nodes=managementFolderTree(items(),mode);active=nodes.get(key)||nodes.get('');key=active.key;prepared=true;}
  function render(){if(!prepared)prepare();prepared=false;
    const rows=managementFolderRows(nodes,active,mode,folderSearch.value,folderSort.value),searching=!!folderSearch.value.trim();
    const pages=Math.max(1,Math.ceil(rows.length/30));folderPage=Math.min(folderPage,pages-1);cards.replaceChildren();
    summary.textContent=(key?active.path||active.name:'全部文件夹')+` · 照片 ${active.photos} · 视频 ${active.videos} · ${formatSize(active.bytes)}（含子目录） · 当前目录直接存放 ${active.direct} 项`;
    for(const node of rows.slice(folderPage*30,folderPage*30+30)){const button=make('button');button.className='folder-card';button.setAttribute('aria-label','进入文件夹 '+node.path);button.append(make('strong','📁 '+node.name),make('span',node.path),make('span',`照片 ${node.photos} · 视频 ${node.videos} · ${formatSize(node.bytes)} · 子目录 ${node.children.length}`));button.disabled=locked();button.onclick=()=>navigate(node.key);cards.append(button);}
    if(!rows.length)cards.append(make('p',searching?'没有匹配的文件夹，可清空目录搜索或返回上级。':key?'此目录没有已扫描媒体子目录，媒体内容在下方。':'没有符合媒体类型的已扫描目录。'));
    const chosen=selectable();pick.disabled=locked()||!chosen.length||chosen.length>200;pick.textContent=chosen.length>200?'筛选结果超过 200 项，请缩小范围':`勾选目录内筛选结果（${chosen.length} 项）`;
    up.disabled=locked()||!key;top.disabled=locked()||(!key&&!searching);modeSelect.disabled=folderSearch.disabled=folderSort.disabled=locked();within.disabled=locked()||!key;previous.disabled=locked()||folderPage===0;next.disabled=locked()||folderPage>=pages-1;position.textContent=`${searching?'匹配目录（含下级）':'子目录'} ${rows.length} 个 · 第 ${folderPage+1}/${pages} 页`;
  }
  return {prepare,render,matches:item=>managementFolderContains(item,active,mode,descendants),reset(){key='';folderPage=0;active=null;prepared=false;descendants=true;within.checked=true;folderSearch.value='';folderSort.value='name';locations.clear();},};
}
if(typeof module!=='undefined'&&module.exports)Object.assign(module.exports,{managementFolderParts,managementFolderTree,managementFolderContains,managementFolderRows,createManagementFolderBrowser});
