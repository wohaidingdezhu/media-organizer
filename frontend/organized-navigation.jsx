import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function OrganizedBreadcrumbs({ancestors, currentKey, onNavigate}) {
  return ancestors.map(ancestor => <button key={ancestor.key} type="button"
    disabled={ancestor.key === currentKey} aria-current={ancestor.key === currentKey ? 'location' : undefined}
    aria-label={'前往整理目录 ' + (ancestor.path || '全部整理结果')}
    onClick={() => onNavigate(ancestor.key)}>{ancestor.key ? ancestor.name : '全部整理结果'}</button>);
}

export function OrganizedFolders({folders, formatSize, onNavigate}) {
  return folders.map(folder => {
    const label = folder.parts.length === 1 ? folder.name.split(/[\\/]/).filter(Boolean).pop() || folder.name : folder.name;
    return <button key={folder.key} type="button" className="folder-card"
      aria-label={'进入整理目录 ' + folder.path} onClick={() => onNavigate(folder.key)}>
      <strong>📁 {label}</strong><span>{folder.path}</span>
      <span>{folder.count} 项 · {formatSize(folder.bytes)}（含子目录）</span>
    </button>;
  });
}

export function mountOrganizedNavigation(breadcrumbs, folders, onNavigate) {
  const breadcrumbRoot = createRoot(breadcrumbs);
  const folderRoot = createRoot(folders);
  return state => flushSync(() => {
    breadcrumbRoot.render(<OrganizedBreadcrumbs {...state} onNavigate={onNavigate} />);
    folderRoot.render(<OrganizedFolders {...state} onNavigate={onNavigate} />);
  });
}
