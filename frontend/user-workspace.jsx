import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';

const pageSize = 50;
const actionLimit = 200;

async function request(route = 'api/users', payload) {
  const options = payload ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {};
  const response = await fetch(route, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '请求失败');
  return result;
}

function StatusError({id, message}) {
  return <p className="error" id={id} role="alert" hidden={!message}>{message}</p>;
}

function Dialog({id, title, error, active, locked, onClose, children}) {
  const dialog = useRef(null);
  useLayoutEffect(() => {
    if (active && !dialog.current.open) dialog.current.showModal();
    else if (!active && dialog.current.open) dialog.current.close();
  }, [active]);
  return <dialog ref={dialog} id={id} aria-labelledby={id + '-title'}
    onCancel={event => { event.preventDefault(); if (!locked) onClose(); }}>
    <h2 id={id + '-title'}>{title}</h2>
    <StatusError id={id.replace(/-dialog$/, '') + '-error'} message={error} />
    {children}
  </dialog>;
}

function UserOptions({users, filter = false}) {
  return <>
    <option value="">{filter ? '全部用户名' : '未分配'}</option>
    {filter && <><option value="unassigned">未分配</option><option value="mixed">归属不一致</option></>}
    {users.map(user => <option value={user.id} key={user.id}>{user.name}</option>)}
  </>;
}

function MediaCard({item, checked, busy, locked, access, select, notify, reportError}) {
  const [opening, setOpening] = useState(null);
  const [failedArt, setFailedArt] = useState('');
  const art = /^(previews|covers)\/[A-Za-z0-9_-]+\.png$/.test(item.preview) && failedArt !== item.preview ? item.preview : '';
  async function open(file, action) {
    if (locked || opening) return;
    setOpening(file.id);
    try {
      const result = await request('api/media/action', {id: file.id, action});
      notify(result.message);
    } catch (error) {
      reportError(error.message);
    } finally {
      setOpening(null);
    }
  }
  return <article className="card">
    {art ? <img src={art} alt={item.title} loading="lazy" onError={() => setFailedArt(art)} /> :
      <div className="placeholder">预览未提供</div>}
    <label><input type="checkbox" aria-label={'选择 ' + item.title} checked={checked} disabled={busy}
      onChange={event => select(item.id, event.target.checked)} /><span>{item.title}</span></label>
    <span className="badge">{item.username}</span>
    <p className="muted">{item.kind} · {item.files.length} 个媒体文件</p>
    <details>
      <summary>查看原位置与打开文件</summary>
      {item.files.map(file => <React.Fragment key={file.path}>
        <p className="path">{file.path}</p>
        {file.id && access?.available && access.ids_by_path?.[file.path] === file.id &&
          <div className="controls">
            <button type="button" disabled={locked || opening !== null} onClick={() => open(file, 'open')}>
              {item.kind === '照片' ? '打开原图' : '播放影片'}
            </button>
            <button type="button" disabled={locked || opening !== null} onClick={() => open(file, 'reveal')}>定位文件</button>
          </div>}
      </React.Fragment>)}
    </details>
  </article>;
}

function UserWorkspace() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [operating, setOperating] = useState(true);
  const [access, setAccess] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('正在读取用户分类…');
  const [planLink, setPlanLink] = useState('');
  const [dialog, setDialog] = useState(null);
  const [dialogError, setDialogError] = useState('');
  const [name, setName] = useState('');
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [owner, setOwner] = useState('');
  const [assign, setAssign] = useState('');
  const [userQuery, setUserQuery] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(0);
  const [photoLayout, setPhotoLayout] = useState('month');
  const [namePolicy, setNamePolicy] = useState('suggest');
  const inFlight = useRef(false);

  const users = data?.users || [];
  const foundUsers = users.filter(user => user.name.toLocaleLowerCase().includes(userQuery.trim().toLocaleLowerCase()));
  const items = useMemo(() => {
    const search = query.trim().toLocaleLowerCase();
    return (data?.items || []).filter(item =>
      (!kind || item.kind === kind) && (!owner || item.user_id === (owner === 'unassigned' ? '' : owner)) &&
      (!search || [item.title, item.username, ...item.files.map(file => file.path)].some(value => String(value).toLocaleLowerCase().includes(search))));
  }, [data, kind, owner, query]);
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = items.slice(current * pageSize, (current + 1) * pageSize);
  const locked = busy || operating || !data;
  const canUseSelection = !locked && selected.size > 0 && selected.size <= actionLimit;

  function accept(result) {
    setData(result);
    const ids = new Set(result.users.map(user => user.id));
    if (owner && !['unassigned', 'mixed'].includes(owner) && !ids.has(owner)) setOwner('');
    if (assign && !ids.has(assign)) setAssign('');
  }

  function notify(message) {
    setNotice(message);
    setPlanLink('');
  }

  async function run(task, onError = setError) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    onError('');
    try {
      await task();
    } catch (failure) {
      onError(failure.message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function refresh() {
    setPreview(null);
    return run(async () => {
      const [result, operations, media] = await Promise.all([request(), request('api/operations'), request('api/media')]);
      const active = operations.jobs.some(job => ['running', 'external_running'].includes(job.status)) || Boolean(operations.warnings.length);
      accept(result);
      setOperating(active);
      setAccess(media);
      setSelected(new Set());
      notify(active ? '当前文件操作未完成或记录需核对，暂缓修改用户分类。' : '已读取最新用户名与归属。');
    }, message => { if (message) setOperating(true); setError(message); });
  }
  useEffect(() => { refresh(); }, []);

  function startDialog(type, user) {
    setDialogError('');
    setName(user?.name || '');
    setDialog({type, user});
  }

  function closeDialog() {
    setDialog(null);
    setDialogError('');
    setPreview(null);
  }

  function change(payload, close = false) {
    if (locked) return;
    const setFailure = close ? setDialogError : setError;
    return run(async () => {
      accept(await request('api/users', {...payload, revision: data.revision}));
      notify('用户分类已保存；重新打开和同路径重新扫描后仍保留。');
      if (close) closeDialog();
    }, message => setFailure(message && message + '；请取消当前操作后刷新分类。'));
  }

  function previewPlan() {
    if (!canUseSelection) return;
    return run(async () => {
      const params = {ids: [...selected], revision: data.revision, photo_layout: photoLayout, resolve_names: namePolicy !== 'reject'};
      const result = await request('api/users', {action: 'plan-preview', ...params});
      setPreview({result, params});
      startDialog('plan');
    });
  }

  function applyPlan() {
    if (locked || !preview) return;
    return run(async () => {
      const {result, params} = preview;
      try {
        const saved = await request('api/users', {...params, action: 'plan-apply', plan_revision: result.revision, targets_digest: result.targets_digest});
        closeDialog();
        setNotice('分类计划已保存。请在整理页核对、纳入计划，再预览确认复制。');
        setPlanLink('organize.html#user-plan=' + saved.ids.join(','));
      } catch (failure) {
        setPreview({...preview, invalid: true});
        throw failure;
      }
    }, message => setDialogError(message && message + '；请取消后刷新分类并重新预览。'));
  }

  return <>
    <main>
      <header>
        <nav><a href="photos.html">照片墙</a><a href="library.html">影片海报墙</a><a href="organize.html">文件管理与整理</a></nav>
        <h1>按用户名整理照片和电影</h1><p>先创建用户名，再分配照片或整部电影，最后预览按用户生成的分类目录。</p>
      </header>
      <StatusError id="user-error" message={error} />
      <p id="user-notice" role="status">{notice} {planLink && <a className="button" href={planLink}>打开本批整理计划</a>}</p>
      <section>
        <h2>用户名管理</h2>
        <p className="muted">每张照片或整部电影有一个归属。电影的版本和分段一起分配。改名保留归属，删除用户名会解除关联。</p>
        <div className="controls">
          <input id="user-search" type="search" aria-label="搜索用户名" placeholder="搜索用户名…"
            value={userQuery} onChange={event => setUserQuery(event.target.value)} />
          <button id="user-create" type="button" disabled={locked} onClick={() => startDialog('edit')}>新增用户名</button>
          <button id="user-refresh" type="button" disabled={busy} onClick={refresh}>刷新分类</button>
        </div>
        <div id="user-list" className="users">
          {foundUsers.map(user => <div key={user.id} className="user">
            <strong>{user.name}</strong><p className="muted">本次 {user.media_count} 项 · 历次归属 {user.file_count} 个媒体文件</p>
            <button type="button" onClick={() => {setOwner(user.id); setPage(0);}}>查看媒体</button>
            <button type="button" disabled={locked} onClick={() => startDialog('edit', user)}>改名</button>
            <button type="button" className="danger" disabled={locked} onClick={() => startDialog('delete', user)}>删除</button>
          </div>)}
          {data && !foundUsers.length && <p className="muted">没有匹配的用户名，可以新增。</p>}
        </div>
      </section>
      <section>
        <h2>照片与电影</h2>
        <div className="controls">
          <input id="media-search" type="search" aria-label="搜索照片和电影" placeholder="搜索名称、用户名或原路径…"
            value={query} onChange={event => {setQuery(event.target.value); setPage(0);}} />
          <select id="user-filter" aria-label="按用户名筛选" value={owner} onChange={event => {setOwner(event.target.value); setPage(0);}}>
            <UserOptions users={users} filter />
          </select>
          <select id="media-kind" aria-label="按媒体类型筛选" value={kind} onChange={event => {setKind(event.target.value); setPage(0);}}>
            <option value="">照片和电影</option><option>照片</option><option>电影</option>
          </select>
          <button id="user-reset" type="button" onClick={() => {setQuery(''); setOwner(''); setKind(''); setPage(0);}}>重置筛选</button>
        </div>
        <div className="controls">
          <button id="user-select-page" type="button" disabled={busy || !visible.length}
            onClick={() => setSelected(previous => new Set([...previous, ...visible.map(item => item.id)]))}>全选当前页</button>
          <button id="user-clear" type="button" disabled={busy || !selected.size} onClick={() => setSelected(new Set())}>清空勾选</button>
          <span id="user-selected" role="status">已勾选 {selected.size} 项（跨页保留）{selected.size > actionLimit && ' · 每次最多处理 200 项'}</span>
        </div>
        <div className="controls">
          <select id="assign-user" aria-label="选择归属用户名" value={assign} onChange={event => setAssign(event.target.value)}>
            <UserOptions users={users} />
          </select>
          <button id="user-assign" type="button" className="primary" disabled={!canUseSelection}
            onClick={() => change({action: 'assign', user_id: assign, ids: [...selected]})}>保存勾选项归属</button>
        </div>
        <div className="controls">
          <label htmlFor="photo-layout">照片目录</label>
          <select id="photo-layout" disabled={busy} value={photoLayout} onChange={event => setPhotoLayout(event.target.value)}>
            <option value="month">按年 / 月</option><option value="year">按年份</option><option value="flat">同一照片目录</option>
          </select>
          <label htmlFor="photo-names">同名照片</label>
          <select id="photo-names" disabled={busy} value={namePolicy} onChange={event => setNamePolicy(event.target.value)}>
            <option value="suggest">预览区分后缀建议</option><option value="reject">遇到重名停止</option>
          </select>
          <button id="user-plan" type="button" disabled={!canUseSelection} onClick={previewPlan}>预览按用户名分类</button>
        </div>
        <p className="muted">照片缺少日期时归入“日期未知”。同名后缀用于复制计划，电影保留原文件名。归属跨扫描保留，分类计划仅属于本次扫描。</p>
        <div id="user-media" className="wall">
          {visible.map(item => <MediaCard key={item.id} item={item} checked={selected.has(item.id)} busy={busy} locked={locked}
            access={access} notify={notify} reportError={setError} select={(id, checked) => setSelected(previous => {
              const next = new Set(previous);
              if (checked) next.add(id);
              else next.delete(id);
              return next;
            })} />)}
          {data && !items.length && <p>没有符合条件的照片或电影。</p>}
        </div>
        <div className="pager">
          <button id="user-previous" type="button" disabled={busy || !current} onClick={() => setPage(current - 1)}>上一页</button>
          <span id="user-page">{items.length} 项 · 第 {current + 1}/{pages} 页</span>
          <button id="user-next" type="button" disabled={busy || current >= pages - 1} onClick={() => setPage(current + 1)}>下一页</button>
        </div>
      </section>
    </main>
    <Dialog id="user-editor" title={dialog?.user ? '修改用户名' : '新增用户名'} error={dialog?.type === 'edit' ? dialogError : ''}
      active={dialog?.type === 'edit'} locked={busy} onClose={closeDialog}>
      <form id="user-form" onSubmit={event => {
        event.preventDefault();
        change({action: dialog?.user ? 'rename' : 'create', user_id: dialog?.user?.id, name}, true);
      }}>
        <label htmlFor="user-name">用户名</label>
        <input id="user-name" maxLength={60} required autoComplete="off" value={name}
          disabled={busy} onChange={event => setName(event.target.value)} />
        <p className="muted">1–60 字。名称须能用作 Mac 和 Windows 文件夹名称。大小写和 Unicode 等价名称视为重名。</p>
        <div className="actions">
          <button type="button" id="user-editor-cancel" disabled={busy} onClick={closeDialog}>取消</button>
          <button type="submit" id="user-editor-save" className="primary" disabled={locked}>保存用户名</button>
        </div>
      </form>
    </Dialog>
    <Dialog id="user-delete-dialog" title="删除用户名" error={dialog?.type === 'delete' ? dialogError : ''}
      active={dialog?.type === 'delete'} locked={busy} onClose={closeDialog}>
      <p>删除“{dialog?.user?.name}”？将解除历次记录中 {dialog?.user?.file_count} 个媒体文件的归属，原媒体和旧分类计划保持原样。</p>
      <div className="actions">
        <button type="button" id="user-delete-cancel" disabled={busy} onClick={closeDialog}>取消</button>
        <button type="button" id="user-delete-confirm" className="danger" disabled={locked || !dialog?.user}
          onClick={() => change({action: 'delete', user_id: dialog.user.id}, true)}>确认删除用户名</button>
      </div>
    </Dialog>
    <Dialog id="user-plan-dialog" title="核对用户名分类计划" error={dialog?.type === 'plan' ? dialogError : ''}
      active={dialog?.type === 'plan'} locked={busy} onClose={closeDialog}>
      <p>这里保存分类计划，复制仍需在整理页确认。</p>
      {preview && <>
        <p id="user-plan-summary">{preview.result.items.length} 个媒体文件 · {preview.result.changed_count} 项位置变化 · {preview.result.name_conflict_count || 0} 项同名后缀建议</p>
        <ul id="user-plan-items" className="preview-list">{preview.result.items.map(item => <li key={item.path}>
          <div className="path">{item.path}</div><div className="path">{item.before} → {item.after}</div>
          {item.name_conflict && <p className="muted">同名目标：{item.proposed_before_suffix}。建议添加路径标识后缀。</p>}
          {item.date_source && <p className="muted">日期来源：{item.date_source}</p>}
        </li>)}</ul>
      </>}
      <div className="actions">
        <button type="button" id="user-plan-cancel" disabled={busy} onClick={closeDialog}>取消</button>
        <button type="button" id="user-plan-save" className="primary" disabled={locked || !preview || preview.invalid}
          onClick={applyPlan}>确认保存分类计划</button>
      </div>
    </Dialog>
  </>;
}

export function mountUserWorkspace(container) {
  createRoot(container).render(<UserWorkspace />);
}
