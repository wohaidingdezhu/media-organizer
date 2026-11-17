import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function DashboardFolderList({paths, selection, locked, onChange, onRemove}) {
  if (!paths.length) return <li className="empty">尚未选择文件夹</li>;
  return paths.map((path, index) => <li key={path}>
    <label className="path" style={{display:'flex', alignItems:'center', gap:10}}>
      <input type="checkbox" checked={Boolean(selection.get(path))} disabled={locked}
        aria-label={'本次扫描 ' + path} onChange={event => onChange(path, event.target.checked)} />
      <span>{path}</span>
    </label>
    <button type="button" className="small ghost" disabled={locked} onClick={() => onRemove(index, path)}>移除</button>
  </li>);
}

export function mountDashboardFolders(host) {
  const root = createRoot(host);
  return props => flushSync(() => root.render(<DashboardFolderList {...props} />));
}
