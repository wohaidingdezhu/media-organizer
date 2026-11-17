import React from 'react';
import {createPortal, flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

const surface = {boxSizing: 'border-box', overflowWrap: 'anywhere', padding: 24,
  border: '1px solid #d7d3e3', borderRadius: 16, background: '#fcfcfe', color: '#272a36'};
const trashSurface = {...surface, display: 'flex', flexDirection: 'column', width: 'min(680px,92vw)',
  maxHeight: '90vh', overflow: 'hidden', wordBreak: 'break-word'};
const trashContent = {flex: 1, minHeight: 0, overflowX: 'hidden', overflowY: 'auto',
  overflowWrap: 'anywhere', wordBreak: 'break-word'};
const trashActions = {display: 'flex', gap: 12, justifyContent: 'flex-end', flexWrap: 'wrap',
  flex: 'none', background: '#fcfcfe', padding: '14px 0 0'};
const control = {padding: '10px 16px', border: '1px solid #d7d3e3', borderRadius: 8, cursor: 'pointer'};
const compareSurface = {...surface, width: 'min(1100px,94vw)', maxHeight: '90vh', overflowX: 'hidden', overflowY: 'auto'};
const compareGrid = {display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(280px,100%),1fr))', gap: 20};
const compareImage = {display: 'block', width: '100%', height: '45vh', objectFit: 'contain', background: '#eeebf4'};

function TrashDialog({state, refs, actions}) {
  const {monitoring, status, progress, summary, rows, running, stopAllowed, stopLocked, canConfirm, canResume, retry, returning} = state;
  const title = monitoring ? '回收站处理进度与明细' : '移到废纸篓 / 回收站？';
  return <dialog ref={refs.dialog} data-media-dialog="trash" style={trashSurface}
    aria-label={monitoring ? title : '确认移到废纸篓 / 回收站'}>
    <h2 style={{flex: 'none', margin: '0 0 12px'}}>{title}</h2>
    <div ref={refs.content} style={trashContent}>
      <p role="status">{status}</p>
      <progress max={progress.total || 1} value={progress.completed} hidden={!progress.visible}
        aria-label="移到回收站的文件进度" style={{width: '100%', margin: '12px 0'}} />
      {summary && <details>
        <summary>保留文件与清理说明（展开查看）</summary>
        <p style={{maxWidth: '100%', overflowWrap: 'anywhere', wordBreak: 'break-word'}}>{summary}</p>
      </details>}
      <ul style={{paddingLeft: 24, overflowWrap: 'anywhere', wordBreak: 'break-word'}}>
        {rows.map((row, index) => <li key={index}>{row}</li>)}
      </ul>
    </div>
    <div style={trashActions}>
      <button type="button" style={control} onClick={actions.close} disabled={running && !monitoring}>
        {returning ? '返回列表' : monitoring ? '关闭' : '取消'}
      </button>
      <button type="button" style={control} onClick={actions.stop} hidden={!stopAllowed} disabled={stopLocked}>停止处理</button>
      <button type="button" style={{...control, color: '#a33b4a'}} hidden={monitoring}
        disabled={!canConfirm} onClick={actions.confirm}>确认删除</button>
      <button type="button" style={control} hidden={!canResume} disabled={state.resuming}
        onClick={actions.resume}>继续核对未处理项</button>
      <button type="button" style={control} hidden={!retry} onClick={actions.retry}>重新检查</button>
    </div>
  </dialog>;
}

function CompareDialog({state, refs, actions}) {
  const {photos, saving, status, review} = state;
  return <dialog ref={refs.dialog} data-media-dialog="compare" aria-label="对比照片" style={compareSurface}>
    <h2>对比照片</h2>
    <p role="status">{status}</p>
    <div style={compareGrid}>
      {photos.map(file => <section key={file.id}>
        <h3>{file.name}</h3>
        {file.preview ? <img src={file.preview} alt={file.name} style={compareImage} /> : <p>尚未生成图片预览</p>}
        <p style={{overflowWrap: 'anywhere', fontSize: 12}}>{file.path}</p>
        <button type="button" style={{...control, margin: '8px 0'}} disabled={saving || !file.canTrash}
          onClick={() => actions.trash(file)}>删除这张…</button>
      </section>)}
    </div>
    <div style={{display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16}}>
      <button type="button" style={control} disabled={saving} onClick={actions.close}>返回列表</button>
      {review && photos.length > 0 && <>
        <button type="button" style={control} disabled={saving} onClick={() => actions.decide('not_similar')}>确认不相似</button>
        <button type="button" style={control} disabled={saving} onClick={() => actions.decide('keep_both')}>相似，保留两张</button>
      </>}
    </div>
  </dialog>;
}

export function ReportDialog(props) {
  return props.kind === 'trash' ? <TrashDialog {...props} /> : <CompareDialog {...props} />;
}

export function mountReportDialog(body) {
  const root = createRoot(body.ownerDocument.createElement('div'));
  return {
    render(props) { flushSync(() => root.render(createPortal(<ReportDialog {...props} />, body))); },
    dispose() { root.unmount(); },
  };
}
