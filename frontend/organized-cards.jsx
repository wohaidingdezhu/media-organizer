import React, {useState} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

const pendingLabels = {open:'正在打开…', reveal:'正在定位…'};

export function OrganizedCopyCard({item, state, actions, failedPreview, onPreviewError}) {
  const pending = state.pending.get(item.id);
  return <article className="photo-card" data-organized-id={item.id}>
    {item.kind === '照片' && <button type="button" className="photo-preview-button"
      aria-label={'预览整理照片 ' + item.name} disabled={state.busy} onClick={() => actions.preview(item)}>
      {item.preview && !failedPreview
        ? <img src={item.preview} alt={item.name} loading="lazy" onError={onPreviewError} />
        : <div className="photo-placeholder">{failedPreview
          ? '预览读取失败 · 查看照片记录' : '扫描预览未生成 · 查看照片记录'}</div>}
    </button>}
    <strong>{item.name}</strong><div className="path">{item.target}</div>
    <p className="muted">{item.kind} · {state.formatSize(item.bytes)}</p>
    {item.can_open ? [['open', item.kind === '照片' ? '查看整理照片' : '播放整理视频'],
      ['reveal', '打开所在文件夹']].map(([action, label]) => <button key={action} type="button"
        style={{marginRight:8, marginTop:8}} data-copy-id={item.id} data-copy-action={action} disabled={state.busy || Boolean(pending)}
        aria-label={label + ' ' + item.name}
        onClick={event => actions.open(item, action, event.currentTarget)}>
      {pending === action ? pendingLabels[action] : label}
    </button>) : <p className="muted">附件或旧副本记录，请通过主媒体定位文件夹。</p>}
  </article>;
}

function CopyCardWithPreview(props) {
  const [failed, setFailed] = useState('');
  const source = props.item.preview;
  return <OrganizedCopyCard {...props} failedPreview={Boolean(source) && failed === source}
    onPreviewError={() => setFailed(source)} />;
}

export function mountOrganizedCopies(host, actions) {
  const root = createRoot(host);
  return state => flushSync(() => root.render(state.items.length
    ? state.items.map(item => <CopyCardWithPreview key={item.id} item={item} state={state} actions={actions} />)
    : <p className="empty">{state.emptyMessage}</p>));
}
