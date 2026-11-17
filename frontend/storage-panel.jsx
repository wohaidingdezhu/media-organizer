import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';

const pageSize = 20;
const views = [
  ['folders', '原文件夹', 'folder'],
  ['roots', '扫描来源', null],
  ['kinds', '照片与视频', 'kind'],
  ['photo_months', '照片年月', 'month'],
  ['largest', '大文件前 100 项', 'path'],
];

function StorageRow({item, largest, field, total, operations, actions}) {
  const value = item.path || item.name;
  const percent = total ? 100 * item.bytes / total : 0;
  const handled = operations.handled.has(item.path);
  const inBasket = operations.basket.has(item.id);
  return <div className="storage-row">
    <div>
      <strong>{item.name}</strong>
      <div className="path">{largest ? item.path : item.count + ' 个媒体文件'}</div>
      <p className="muted">{actions.formatSize(item.bytes)} · 占已扫描媒体逻辑大小 {percent.toFixed(1)}%{item.hardlink ? ' · 硬链接引用' : ''}</p>
      <progress max={100} value={percent} aria-label={item.name + ' 大小比例'} />
    </div>
    <div className="target-actions">
      {field && (field !== 'month' || /^\d{4}\/\d{2}$/.test(item.name)) &&
        <button type="button" aria-label={'查看文件 ' + value} disabled={operations.saving}
          onClick={() => actions.filter(field, value)}>{largest ? '查看此文件' : '查看这些文件'}</button>}
      {largest && <>
        <button type="button" aria-label={'加入候选篮 ' + item.name}
          disabled={!operations.canAdd || handled || inBasket} onClick={() => actions.add(item.id)}>
          {inBasket ? '已在候选篮' : '加入候选篮'}
        </button>
        {handled && <span className="warning">已有清理成功记录</span>}
      </>}
    </div>
  </div>;
}

function StoragePanel({actions, operations}) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);
  const [data, setData] = useState(null);
  const [view, setView] = useState('folders');
  const [page, setPage] = useState(0);
  const loading = useRef(false);
  const select = useRef(null);

  async function load() {
    if (loading.current || data) return;
    loading.current = true;
    setFailed(false);
    setStatus('正在读取扫描时的媒体大小…');
    try {
      setData(await actions.load());
      setStatus('');
    } catch (error) {
      setStatus('空间分析读取失败：' + error.message);
      setFailed(true);
    } finally {
      loading.current = false;
    }
  }
  useEffect(() => {
    if (open) load();
  }, [open]);

  const largest = view === 'largest';
  const field = views.find(([key]) => key === view)[2];
  const rows = data?.[view] || [];
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pages - 1);
  function turnPage(next) {
    setPage(next);
    select.current?.scrollIntoView?.({block: 'start'});
  }

  return <details id="storage-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>空间分布与大文件（按扫描时大小）</summary>
    <p>统计本次扫描的照片和视频逻辑大小。硬链接可能重复计数。日期分类可能来自文件名或修改时间，操作后请重新扫描。</p>
    <div id="storage-summary" className="stats" role="status">
      {status}
      {data && [
        ['扫描媒体逻辑大小', actions.formatSize(data.logical_bytes)],
        ['精确重复副本逻辑大小', actions.formatSize(data.duplicate_logical_bytes)],
        ['报告标记的硬链接引用', data.hardlink_references],
      ].map(([label, value]) => <div className="stat" key={label}><span>{label}</span><strong>{value}</strong></div>)}
    </div>
    {data && <div id="storage-warnings">{data.warnings.map((warning, i) => <p className="warning" key={i}>{warning}</p>)}</div>}
    <div className="manage-controls">
      <select id="storage-view" ref={select} aria-label="空间统计维度" value={view}
        onChange={event => { setView(event.target.value); setPage(0); }}>
        {views.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      {failed && <button id="storage-retry" type="button" onClick={load}>重新读取空间分析</button>}
    </div>
    {data && <>
      <div id="storage-rows">
        {open && rows.slice(current * pageSize, (current + 1) * pageSize).map(item =>
          <StorageRow key={item.id || item.path || item.name} item={item} largest={largest}
            field={field} total={data.logical_bytes} operations={operations} actions={actions} />)}
        {!rows.length && <p className="empty">没有此维度的媒体记录。</p>}
      </div>
      <div className="pager">
        <button id="storage-previous" type="button" disabled={current === 0} onClick={() => turnPage(current - 1)}>上一页空间统计</button>
        <span id="storage-page">{largest && `最多展示前 ${data.largest_limit} 个大文件；`}共 {rows.length} 项 · 第 {current + 1}/{pages} 页 · 按逻辑大小排序</span>
        <button id="storage-next" type="button" disabled={current >= pages - 1} onClick={() => turnPage(current + 1)}>下一页空间统计</button>
      </div>
    </>}
  </details>;
}

export function mountStoragePanel(container, actions) {
  const root = createRoot(container);
  return operations => root.render(<StoragePanel actions={actions} operations={operations} />);
}
