import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function PictureViewer({state, actions}) {
  return <>
    <h2 id="picture-title">{state.copies ? '整理后的照片预览' : state.compare ? '并排比较照片' : '照片预览'}</h2>
    <p id="picture-position" role="status">{state.compare
      ? '对比两张照片，保留需要的，删除不要的。'
      : `${state.copies ? '当前整理结果' : '当前筛选'}照片 ${state.index + 1}/${state.total} · 使用上一张／下一张或键盘左右键浏览`}</p>
    <p id="picture-error" className="error" role="alert" hidden={!state.error}>{state.error}</p>
    <div id="picture-body"><div className={state.compare ? 'compare-pictures' : ''}>
      {state.items.map(file => <div key={file.key}>
        {(!file.preview || file.failed) && <div className="photo-placeholder">
          {file.failed ? '小预览读取失败 · ' : ''}{state.copies
            ? '扫描预览未生成；可尝试打开整理副本' : '预览未生成 · 可打开原图'}
        </div>}
        {file.preview && <img key={file.preview} src={file.preview} alt={file.item.name} loading="lazy"
          hidden={file.failed} onError={() => actions.previewError(file.key, state.session)} />}
        {state.copies ? <>
          <strong>{file.item.name}</strong><div className="path">整理副本：{file.item.target}</div>
          <p className="muted">{file.size} · 显示扫描时的小预览，不代表副本当前内容；打开前会重新核对副本身份。</p>
          {file.item.can_open ? <div className="manage-controls">{[['open', '打开整理副本'], ['reveal', '定位整理副本']].map(([action, label]) =>
            <button type="button" key={action} data-copy-id={file.item.id} data-copy-action={action} disabled={state.busy || !!file.pending}
              onClick={event => actions.copy(file.item, action, event.currentTarget)}>
              {file.pending === action ? (action === 'open' ? '正在打开…' : '正在定位…') : label}
            </button>)}</div> : <p className="warning">此记录缺少副本身份，只能浏览扫描预览；请重新扫描目标目录后查看副本。</p>}
        </> : <>
          <div className="path">{file.item.path}</div><p className="minor">{file.size} · {file.month}</p>
          <div className="manage-controls"><button type="button" className="danger" aria-label={'删除 ' + file.item.name}
            data-picture-path={file.item.path} data-trash-id={file.item.id} disabled={state.busy || !file.canTrash}
            onClick={() => actions.trash(file.item)}>删除…</button>
          {file.hasOriginal && <button type="button" data-picture-path={file.item.path}
            disabled={state.busy || file.handled || !!file.pending}
            onClick={event => actions.original(file.item, event.currentTarget)}>
            {file.pending ? '正在打开…' : '打开原图'}
          </button>}</div>
          {file.handled && <p className="warning">已有清理成功记录，这是扫描时的预览；请重新扫描更新。</p>}
        </>}
      </div>)}
    </div></div>
    <div className="actions">
      <button type="button" id="picture-previous" hidden={state.compare} disabled={state.compare || state.index <= 0} onClick={actions.previous}>上一张照片</button>
      <button type="button" id="picture-next" hidden={state.compare} disabled={state.compare || state.index >= state.total - 1} onClick={actions.next}>下一张照片</button>
      <button type="button" id="picture-close" onClick={actions.close}>关闭</button>
    </div>
  </>;
}

export function mountPictureViewer(host, actions) {
  const root = createRoot(host);
  return state => flushSync(() => root.render(<PictureViewer state={state} actions={actions} />));
}
