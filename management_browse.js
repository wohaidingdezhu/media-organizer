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
if(typeof module!=='undefined'&&module.exports)Object.assign(module.exports,{managementFolderParts,managementFolderTree,managementFolderContains,managementFolderRows});

// Successful copy records only; filtering never reads the destination disk.
function organizedResultView(items, {key='',kind='',query='',order='name',descendants=true}={}) {
  const tree=managementFolderTree(items,'result'),active=tree.get(key)||tree.get('');
  const typed=items.filter(item=>!kind||(kind==='attachment'?!['照片','视频'].includes(item.kind):item.kind===kind));
  const filteredTree=managementFolderTree(typed,'result'),node=filteredTree.get(active.key);
  const folders=(node?.children||[]).map(child=>filteredTree.get(child));
  const needle=query.trim().normalize('NFC').toLocaleLowerCase('zh-CN');
  const rows=typed.filter(item=>managementFolderContains(item,active,'result',!!needle||descendants)&&
    (!needle||(item.target+' '+item.name).normalize('NFC').toLocaleLowerCase('zh-CN').includes(needle)));
  const size=item=>Number.isSafeInteger(item.bytes)&&item.bytes>=0?item.bytes:0;
  const compare=(a,b)=>a.localeCompare(b,'zh-CN',{numeric:true})||(a<b?-1:a>b?1:0);
  rows.sort((a,b)=>(order==='size'?size(b)-size(a):0)||compare(a.name||'',b.name||'')||compare(a.target,b.target));
  folders.sort((a,b)=>(order==='size'?b.bytes-a.bytes:0)||compare(a.name,b.name)||compare(a.path,b.path));
  const ancestors=[];for(let node=active;node;node=tree.get(node.parent))ancestors.unshift(node);
  return {active,folders,rows,ancestors,bytes:rows.reduce((total,item)=>total+size(item),0),
    photos:rows.filter(item=>item.kind==='照片').length,videos:rows.filter(item=>item.kind==='视频').length};
}
if(typeof module!=='undefined'&&module.exports)Object.assign(module.exports,{organizedResultView});
