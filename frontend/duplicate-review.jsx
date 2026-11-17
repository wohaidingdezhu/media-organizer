import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

const groupsPerPage = 10;
const membersPerPage = 50;

function DuplicateMember({item, kept, disabled, handled, mediaId, onKeep, formatSize, openMedia, trash, photo, comparePhoto}) {
  const [opening, setOpening] = useState(false);
  const openLock = useRef(false);
  const [brokenPhoto, setBrokenPhoto] = useState('');
  const photoSource = /^previews\/[A-Za-z0-9_-]+\.png$/.test(photo?.preview) ? photo.preview : '';
  async function open(action) {
    if (openLock.current || disabled || handled || !mediaId) return;
    openLock.current = true;
    setOpening(true);
    try {
      await openMedia(item, action);
    } finally {
      openLock.current = false;
      setOpening(false);
    }
  }
  return <div className={'duplicate-member' + (kept ? ' kept' : '')}>
    {item.kind === '照片' && <div className="duplicate-thumb">
      {photoSource && brokenPhoto !== photoSource
        ? <button type="button" className="photo-preview-button" aria-label={'预览照片 ' + item.name} onClick={comparePhoto}>
          <img src={photoSource} alt={item.name} loading="lazy" onError={() => setBrokenPhoto(photoSource)} />
        </button>
        : <div className="photo-placeholder">预览未生成 · 可打开原图</div>}
    </div>}
    <div>
      <strong>{item.name}</strong>
      <div className="path">{item.path}</div>
      <p className="muted">{item.kind} · {formatSize(item.bytes)} · 修改时间：{item.mtime === null ? '报告未提供' : new Date(item.mtime * 1000).toLocaleString('zh-CN')}</p>
      {item.sidecars.length > 0 && <details>
        <summary>关联附属文件 {item.sidecars.length} 项（保留原位置）</summary>
        {item.sidecars.map(sidecar => <div className="path" key={sidecar.path}>{sidecar.status}：{sidecar.path}</div>)}
      </details>}
      {handled && <p className="warning">已移到废纸篓 / 回收站，请重新扫描更新分组。</p>}
      <div className="target-actions">
        <button type="button" className={kept ? 'primary' : undefined} aria-pressed={kept}
          aria-label={'选作本组保留项 ' + item.name} disabled={disabled || handled} onClick={onKeep}>
          {kept ? '已选本组保留项' : '选作本组保留项'}
        </button>
        {[['open', item.kind === '视频' ? '播放影片' : '打开原图'], ['reveal', '在 文件管理器 定位']].map(([action, label]) =>
          <button key={action} type="button" aria-label={label + ' ' + item.name}
            disabled={disabled || handled || !mediaId || opening} onClick={() => open(action)}>{label}</button>)}
        <button type="button" className="danger" aria-label={'删除 ' + item.name} data-trash-id={item.id}
          data-picture-path={item.path} disabled={disabled || handled || mediaId !== item.id} onClick={() => trash(item)}>
          删除…
        </button>
      </div>
    </div>
  </div>;
}

function DuplicateGroup({group, keeper, limit, disabled, media, actions, error, onKeep, onExpand, onPreview}) {
  const {formatSize, compare} = actions;
  const active = group.items.filter(item => !media.handled.has(item.path));
  const kept = active.find(item => item.id === keeper);
  const expanded = group.items.length <= limit;
  const canPreview = !disabled && kept && active.length >= 2 && active.length <= 201
    && expanded && media.available && active.every(item => media.ids[item.path] === item.id);
  let note = '请选择一份保留项，再预览其余副本。';
  if (!expanded) note = '请先展开完整分组核对。';
  else if (active.length < 2) note = '本组已处理后仅剩一份可选，请重新扫描更新分组。';
  else if (active.length > 201) note = '本组副本超过单批 200 项，请在主清单分批勾选，并至少保留一份。';
  else if (kept) note = '预览只选本组其余副本，会清空其他勾选。保留选择在刷新后需要重新选择。';
  return <article className="duplicate-group">
    <h3>精确重复第 {group.number} 组 · {group.items.length} 个文件 · 每个 {formatSize(group.bytes_each)}</h3>
    <p className="muted">重复副本逻辑大小 {formatSize(group.redundant_logical_bytes)} · 操作前会重新检查文件状态。</p>
    <details><summary>查看完整 SHA-256</summary><pre>{group.sha256}</pre></details>
    {group.items.slice(0, limit).map(item => <DuplicateMember key={item.id} item={item} kept={keeper === item.id}
      disabled={disabled} handled={media.handled.has(item.path)} mediaId={media.available ? media.ids[item.path] : null}
      photo={media.photos.get(item.id)} comparePhoto={() => compare([item])} onKeep={() => onKeep(item.id)}
      formatSize={formatSize} openMedia={actions.openMedia} trash={actions.trash} />)}
    {!expanded && <button type="button" onClick={onExpand}>再显示 50 个成员（尚有 {group.items.length - limit} 项）</button>}
    <div className="duplicate-toolbar">
      <button type="button" className="danger" disabled={!canPreview}
        onClick={() => onPreview(kept, active.filter(item => item.id !== keeper))}>预览清理其余副本</button>
      {group.items.length === 2 && group.items.every(item => item.kind === '照片') &&
        <button type="button" disabled={disabled} onClick={() => compare(group.items)}>对比这两张</button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <p className="muted">{note}</p>
  </article>;
}

function DuplicateReview({actions, media}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState(null);
  const [loadStatus, setLoadStatus] = useState('');
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(0);
  const [choices, setChoices] = useState({});
  const [limits, setLimits] = useState({});
  const [errors, setErrors] = useState({});
  const [previewing, setPreviewing] = useState(false);
  const loading = useRef(false);
  const previewLock = useRef(false);
  const details = useRef(null);

  useLayoutEffect(() => {
    actions.openPanel.current = () => {
      if (!details.current) return;
      details.current.open = true;
      setOpen(true);
      details.current.scrollIntoView?.({behavior: 'smooth', block: 'start'});
    };
    return () => { actions.openPanel.current = null; };
  }, [actions]);

  async function load() {
    if (result || loading.current) return;
    loading.current = true;
    setFailed(false);
    setLoadStatus('正在读取完整 SHA-256 精确重复分组…');
    try {
      setResult(await actions.load());
    } catch (error) {
      setLoadStatus('分组读取失败：' + error.message);
      setFailed(true);
    } finally {
      loading.current = false;
    }
  }
  useEffect(() => {
    if (open) load();
  }, [open]);

  const match = query.trim().toLocaleLowerCase();
  const groups = useMemo(() => (result?.groups || []).filter(group =>
    (!kind || group.items.some(item => item.kind === kind)) &&
    (!match || group.sha256.includes(match) || group.items.some(item => item.path.toLocaleLowerCase().includes(match)))
  ), [result, kind, match]);
  const pages = Math.max(1, Math.ceil(groups.length / groupsPerPage));
  const current = Math.min(page, pages - 1);

  async function preview(number, kept, copies) {
    if (previewLock.current || media.busy) return;
    previewLock.current = true;
    setPreviewing(true);
    setErrors(previous => ({...previous, [number]: ''}));
    try {
      await actions.preview(kept, copies);
    } catch (error) {
      setErrors(previous => ({...previous, [number]: error.message}));
    } finally {
      previewLock.current = false;
      setPreviewing(false);
    }
  }
  return <details id="duplicate-panel" ref={details} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary id="duplicate-heading">精确重复分组核对{result ? ' · ' + result.counts.groups + ' 组' : ''}</summary>
    <p>只展示完整 SHA-256 一致的文件。搜索匹配任一成员时展示整组，请先明确选择保留项。</p>
    <p>重复副本按扫描时的逻辑大小计算。废纸篓 / 回收站仍占空间，硬链接不计入精确重复，附属文件保留在原位置。</p>
    <div className="duplicate-toolbar">
      <input id="duplicate-search" type="search" aria-label="搜索精确重复组" placeholder="按文件名、来源路径或 SHA-256 查找整组…"
        value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} />
      <select id="duplicate-kind" aria-label="筛选重复组媒体类型" value={kind}
        onChange={event => { setKind(event.target.value); setPage(0); }}>
        <option value="">全部类型</option><option value="照片">包含照片</option><option value="视频">包含视频</option>
      </select>
      {failed && <button id="duplicate-retry" type="button" onClick={load}>重新读取重复分组</button>}
    </div>
    <p id="duplicate-summary" role="status">{previewing ? '正在检查所选副本…' : result
      ? `共 ${result.counts.groups} 组 · ${result.counts.files} 个文件 · 重复副本逻辑大小 ${actions.formatSize(result.counts.redundant_logical_bytes)}（至少保留每组一份）`
      : loadStatus}</p>
    {result && <>
      <div id="duplicate-warnings">{result.warnings.map((warning, i) => <p className="warning" key={i}>{warning}</p>)}</div>
      <div id="duplicate-groups">
        {open && groups.slice(current * groupsPerPage, (current + 1) * groupsPerPage).map(group =>
          <DuplicateGroup key={group.number} group={group} media={media} actions={actions}
            keeper={choices[group.number]} limit={limits[group.number] || membersPerPage}
            error={errors[group.number]} disabled={media.busy || previewing}
            onKeep={id => {
              setChoices(previous => ({...previous, [group.number]: id}));
              setErrors(previous => ({...previous, [group.number]: ''}));
            }}
            onExpand={() => setLimits(previous => ({...previous, [group.number]: (previous[group.number] || membersPerPage) + membersPerPage}))}
            onPreview={(kept, copies) => preview(group.number, kept, copies)} />)}
        {!groups.length && <p className="empty">没有匹配的完整 SHA-256 精确重复组。</p>}
      </div>
      <div className="pager">
        <button id="duplicate-previous" type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>上一页重复组</button>
        <span id="duplicate-page">匹配 {groups.length} 组 · 第 {current + 1}/{pages} 页 · 按重复副本逻辑大小排序</span>
        <button id="duplicate-next" type="button" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>下一页重复组</button>
      </div>
    </>}
  </details>;
}

export function mountDuplicateReview(container, actions) {
  const root = createRoot(container);
  return media => flushSync(() => root.render(<DuplicateReview actions={actions} media={media} />));
}
