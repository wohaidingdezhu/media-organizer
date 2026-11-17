import React, {useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';

function MediaActions({item, state, actions, wall}) {
  const [opening, setOpening] = useState(false);
  const openLock = useRef(false);
  const handled = state.handled.has(item.path);
  const available = state.available && state.ids[item.path] === item.id;
  async function open(action) {
    if (openLock.current || state.busy || handled || !available) return;
    openLock.current = true;
    setOpening(true);
    try {
      await actions.open(item, action);
    } finally {
      openLock.current = false;
      setOpening(false);
    }
  }

  const verbs = [['open', wall ? '打开原图' : item.kind === '视频' ? '播放影片' : '打开照片'], ['reveal', '在 文件管理器 定位']];
  const systemActions = (available || wall && state.ids[item.path]) && verbs.map(([action, label]) =>
    <button type="button" key={action} aria-label={label + ' ' + item.name}
      disabled={!available || state.busy || handled || opening} onClick={() => open(action)}>{label}</button>);

  return <>
    {!wall && systemActions && <div className="target-actions">{systemActions}</div>}
    <span className="badge cleanup-extra">{actions.owner(item)}</span>
    {handled
      ? <p className="warning">已移到废纸篓 / 回收站，请重新扫描更新清单</p>
      : state.copied.has(item.path) && <span className="badge status-include">已有复制成功记录 · 原件保留</span>}
    {state.basket.has(item.id) && <div className="target-actions">
      <span className="badge warning">清理候选</span>
      <button type="button" aria-label={'移出候选篮 ' + item.name}
        disabled={!state.basketWritable} onClick={() => actions.removeCandidate(item.id)}>移出候选篮</button>
    </div>}
    {item.duplicate_group && <span className="badge warning">精确重复第 {item.duplicate_group} 组</span>}
    {!wall && item.hardlink_to && <span className="badge warning">硬链接</span>}
    {wall && systemActions}
    {['照片', '视频'].includes(item.kind) && <button type="button" className="cleanup-extra" disabled={state.saving}
      aria-label={(item.kind === '视频' ? '影片整组核对 ' : '配套文件核对 ') + item.name} onClick={() => actions.bundle(item)}>
      {item.kind === '视频' ? '影片整组核对' : '配套文件核对'}
    </button>}
    <button type="button" className="danger" aria-label={'删除 ' + item.name}
      data-picture-path={item.path} data-trash-id={item.id}
      disabled={state.busy || handled || !available} onClick={() => actions.trash(item)}>删除…</button>
  </>;
}

function MediaSelection({item, state, actions, wall}) {
  return <input type="checkbox" checked={state.selected.has(item.id)} disabled={state.busy || state.handled.has(item.path)}
    aria-label={(wall ? '选择照片 ' : '选择 ') + item.name}
    onChange={event => actions.select(item, event.target.checked)} />;
}

function PhotoCard({item, state, actions}) {
  const [broken, setBroken] = useState('');
  const photo = state.photos.get(item.id);
  const art = /^previews\/[A-Za-z0-9_-]+\.png$/.test(photo?.preview) && broken !== photo.preview ? photo.preview : '';
  return <article className="photo-card">
    {art ? <button type="button" className="photo-preview-button" aria-label={'预览照片 ' + item.name}
      onClick={() => actions.preview(item)}>
      <img src={art} alt={item.name} loading="lazy" onError={() => setBroken(art)} />
    </button> : <div className="photo-placeholder">预览未生成 · 可打开原图</div>}
    <label><MediaSelection item={item} state={state} actions={actions} wall /><strong>{item.name}</strong></label>
    <div className="path">{item.path}</div>
    <div className="media-facts">{actions.formatSize(item.bytes)} · {photo?.month || '日期未提供'}</div>
    <span className="badge photo-plan-state">{state.names[item.state]}</span>
    <div className="reason">{item.reason}</div>
    <MediaActions item={item} state={state} actions={actions} wall />
    <button type="button" className="cleanup-extra" disabled={state.busy} onClick={() => actions.edit(item)}>调整分类位置</button>
  </article>;
}

function MediaRow({item, state, actions}) {
  return <tr>
    <td><MediaSelection item={item} state={state} actions={actions} /></td>
    <td className="source">
      <div className="name">{item.name}</div>
      <div className="muted">{item.kind} · {actions.formatSize(item.bytes)}</div>
      <div className="path">{item.path}</div>
      <MediaActions item={item} state={state} actions={actions} />
    </td>
    <td className="target">
      <div className="target-path">{item.suggested_path || '暂无分类建议'}</div>
      <div className="reason">{item.reason}</div>
      {!item.selectable && <div className="warning reason">{item.blocked_reason || '分类位置无效，暂不能纳入计划'}</div>}
      {item.target_edited && <>
        <span className="badge status-include">手动调整</span>
        <div className="path">扫描原建议：{item.original_suggested_path || '无'}</div>
      </>}
      <div className="target-actions">
        <button type="button" disabled={state.busy} aria-label={'调整分类位置 ' + item.name} onClick={() => actions.edit(item)}>
          调整分类位置
        </button>
        {item.target_edited && <button type="button" disabled={state.busy} aria-label={'恢复扫描建议 ' + item.name}
          onClick={() => actions.reset(item.id)}>恢复扫描建议</button>}
      </div>
    </td>
    <td><span className={'badge status-' + item.state}>{state.names[item.state] || item.state}</span></td>
  </tr>;
}

export function mountWorkspaceMedia({table, wall}, actions) {
  const tableRoot = createRoot(table);
  const wallRoot = createRoot(wall);
  return (items, state, showWall) => {
    tableRoot.render(showWall ? null : items.length
      ? items.map(item => <MediaRow key={item.id} item={item} state={state} actions={actions} />)
      : <tr><td className="empty" colSpan={4}>没有符合条件的文件。可切换核对状态或调整筛选。</td></tr>);
    wallRoot.render(!showWall ? null : items.length
      ? items.map(item => <PhotoCard key={item.id} item={item} state={state} actions={actions} />)
      : <p className="empty">没有符合条件的照片。</p>);
  };
}
