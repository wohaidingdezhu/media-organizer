import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {managementFolderContains, managementFolderRows, managementFolderTree} from '../management_browse.js';

const pageSize = 30;
const selectionLimit = 200;
const startingLocation = {key: '', descendants: true, page: 0, query: '', order: 'name'};

function FolderBrowser({compact, active, location, mode, busy, candidateCount, total, pages, rows, page, searching, actions, formatSize}) {
  const contents = <>
    <h2>文件夹管理</h2>
    <details className="folder-help"><summary>目录说明</summary>
      <p>这里只显示已扫描的媒体目录。进入文件夹后，下方会显示其中的照片和电影。分类目录是整理建议，原文件位置不变。</p>
    </details>
    <div className="manage-controls">
      <select aria-label="文件夹浏览目录" disabled={busy} value={mode} onChange={event => actions.changeMode(event.target.value)}>
        <option value="source">磁盘原有文件夹</option><option value="target">整理分类目录（计划）</option>
      </select>
      <label><input type="checkbox" aria-label="包含子文件夹" checked={location.descendants} disabled={busy || !active.key}
        onChange={event => actions.changeDescendants(event.target.checked)} /> 包含子文件夹</label>
      <button type="button" disabled={busy || !active.key && !searching} onClick={() => actions.navigate('')}>全部文件夹</button>
      <button type="button" disabled={busy || !active.key} onClick={() => actions.navigate(active.parent || '')}>返回上级</button>
      <button type="button" disabled={busy || !candidateCount || candidateCount > selectionLimit} onClick={actions.pick}>
        {candidateCount > selectionLimit ? '筛选结果超过 200 项，请缩小范围' : `勾选目录内筛选结果（${candidateCount} 项）`}
      </button>
      <input type="search" placeholder="搜索当前目录下的文件夹名称或路径…" aria-label="搜索文件夹"
        disabled={busy} value={location.query} onChange={event => actions.changeQuery(event.target.value)} />
      <select aria-label="文件夹排序" value={location.order} disabled={busy} onChange={event => actions.changeOrder(event.target.value)}>
        <option value="name">文件夹名称顺序</option><option value="size">文件夹逻辑大小从大到小</option>
        <option value="count">媒体数量从多到少</option>
      </select>
    </div>
    <p role="status">{active.path || active.name} · 照片 {active.photos} · 视频 {active.videos} · {formatSize(active.bytes)}（含子目录） · 当前目录直接存放 {active.direct} 项</p>
    <div className="folder-grid">
      {rows.map(node => <button key={node.key} className="folder-card" type="button" aria-label={'进入文件夹 ' + node.path}
        disabled={busy} onClick={() => actions.navigate(node.key)}>
        <strong>📁 {node.name}</strong><span>{node.path}</span>
        <span>照片 {node.photos} · 视频 {node.videos} · {formatSize(node.bytes)} · 子目录 {node.children.length}</span>
      </button>)}
      {!rows.length && <p>{searching ? '没有匹配的文件夹，可清空目录搜索或返回上级。' :
        active.key ? '此目录没有已扫描媒体子目录，媒体内容在下方。' : '没有符合媒体类型的已扫描目录。'}</p>}
    </div>
    <div className="pager">
      <button type="button" disabled={busy || page === 0} onClick={() => actions.turnPage(page - 1)}>上一页文件夹</button>
      <span>{searching ? '匹配目录（含下级）' : '子目录'} {total} 个 · 第 {page + 1}/{pages} 页</span>
      <button type="button" disabled={busy || page >= pages - 1} onClick={() => actions.turnPage(page + 1)}>下一页文件夹</button>
    </div>
  </>;
  return compact ? <details id="photo-folders"><summary>按文件夹浏览</summary>{contents}</details> : contents;
}

// The workbench uses matches() to filter its file list using the same tree.
export function createManagementFolderBrowser({host, items, locked, changed, selectable, select, formatSize, compact}) {
  const root = createRoot(host);
  let mode = 'source';
  const locations = new Map();
  let location = {...startingLocation};
  let nodes, active, prepared = false;

  function prepare() {
    nodes = managementFolderTree(items(), mode);
    active = nodes.get(location.key) || nodes.get('');
    location.key = active.key;
    prepared = true;
  }

  function renderRows() {
    const rows = managementFolderRows(nodes, active, mode, location.query, location.order);
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    location.page = Math.min(location.page, pages - 1);
    const visible = rows.slice(location.page * pageSize, (location.page + 1) * pageSize);
    flushSync(() => root.render(<FolderBrowser active={active} busy={locked()} candidateCount={selectable().length}
      compact={compact} location={{...location}} mode={mode} rows={visible} total={rows.length} pages={pages}
      page={location.page} searching={Boolean(location.query.trim())} actions={actions} formatSize={formatSize} />));
  }

  const actions = {
    navigate(key) {
      if (locked()) return;
      Object.assign(location, {key, page: 0, query: ''});
      changed();
    },
    changeMode(value) {
      if (locked() || value === mode) return;
      locations.set(mode, location);
      mode = value;
      location = locations.get(mode) || {...startingLocation};
      changed();
    },
    changeDescendants(value) {
      if (locked()) return;
      location.descendants = value;
      changed();
    },
    changeQuery(query) {
      if (locked()) return;
      Object.assign(location, {query, page: 0});
      renderRows();
    },
    changeOrder(order) {
      if (locked()) return;
      Object.assign(location, {order, page: 0});
      renderRows();
    },
    turnPage(page) {
      if (locked()) return;
      location.page = page;
      renderRows();
    },
    pick() {
      if (locked()) return;
      const candidates = selectable();
      if (candidates.length > 0 && candidates.length <= selectionLimit) select(candidates);
    },
  };

  return {
    prepare,
    render() {
      if (!prepared) prepare();
      prepared = false;
      renderRows();
    },
    matches(item) {
      return managementFolderContains(item, active, mode, location.descendants);
    },
    reset() {
      location = {...startingLocation};
      locations.clear();
      prepared = false;
      active = null;
    },
  };
}
