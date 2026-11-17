import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {CenterArtwork, PreviewRepair} from './center-artwork.jsx';

const noteStyle = {display: 'block', width: '100%', margin: '10px 0'};
const playerStyle = {display: 'block', width: '100%', maxHeight: '50vh', margin: '12px 0', background: '#15151c'};

function FileDetails({entry, photo, actions, manage}) {
  const {file, showing, inlineAvailable, openAvailable, revealAvailable, trashAvailable} = entry;
  return <div className="file">
    <strong>{file.name}</strong><p>{file.path}</p>
    {!photo && <p className="video-facts">{entry.videoText} · 编码尺寸</p>}
    <div className="actions">
      {!photo && <button type="button" disabled={!showing && !inlineAvailable} onClick={entry.toggleVideo}>
        {showing ? '关闭预览' : '页内预览'}
      </button>}
      <button type="button" disabled={!openAvailable} onClick={() => actions.open(file, 'open')}>{photo ? '打开原图' : '播放'}</button>
      <button type="button" disabled={!revealAvailable} onClick={() => actions.open(file, 'reveal')}>打开所在文件夹</button>
      {entry.folderAvailable && <button type="button" aria-label={'在资料库中浏览 ' + file.folder}
        onClick={() => actions.browse(file.folder)}>在资料库中浏览目录</button>}
      {(manage || !photo) && <button type="button" className="danger" disabled={!trashAvailable}
        onClick={() => actions.trash(file.id)}>删除此文件…</button>}
    </div>
    {showing && <video ref={entry.attachVideo} controls preload="metadata" aria-label={'预览 ' + file.name}
      style={playerStyle} src={'api/video/' + file.id} onError={entry.videoError} />}
  </div>;
}

export function CenterDetailContent({state, actions, refs}) {
  const {item, photo, busy, facts, preview, hasNotes, files, manage} = state;
  return <div className={'detail-grid' + (photo ? ' photo-detail' : '')}>
    <div className={'detail-art' + (photo ? ' photo' : '')} ref={preview.attachCover}>
      <CenterArtwork artwork={preview.artwork} title={item.title} photo={photo} loading="eager" />
    </div>
    <div>
      <dl className="facts">{facts.map(([label, value]) => <React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>)}</dl>
      {state.unknownVideo && <p className="muted">部分视频信息未知。启用视频解析后重新扫描可补充；解码器不支持的格式仍可浏览和查重。</p>}
      {item.metadata?.plot && <p>{item.metadata.plot}</p>}
      <div className="chips">{item.tags.map(tag => <span className="chip" key={tag}>{tag}</span>)}</div>
      <div className="actions detail-controls">
        <input ref={refs.tags} aria-label="编辑本地标签" placeholder="本地标签，用逗号分隔" defaultValue={state.initialTags} />
        <button type="button" disabled={busy} onClick={actions.saveTags}>保存标签</button>
      </div>
      {hasNotes && <>
        <select ref={refs.rating} aria-label="电影评分" defaultValue={state.initialRating}>
          {Array.from({length: 6}, (_, value) => <option value={value} key={value}>{value ? value + ' 星' : '未评分'}</option>)}
        </select>
        <textarea ref={refs.note} aria-label="电影观看备注" maxLength={2000} style={noteStyle} defaultValue={state.initialNote} />
        <button type="button" disabled={busy} onClick={actions.saveNotes}>保存评分与备注</button>
      </>}
      {files.map(entry => <FileDetails key={entry.file.id} entry={entry} photo={photo} actions={actions} manage={manage} />)}
      {!photo && <button type="button" disabled={busy} onClick={actions.classify}>
        {item.kind === 'video' ? '标为电影' : '改为普通视频'}
      </button>}
      {item.manual_kind && <button type="button" disabled={busy} onClick={actions.resetKind}>恢复目录 / 自动分类</button>}
      <div className="actions detail-controls"><PreviewRepair repair={preview.repair} onClick={preview.repairPreview} /></div>
    </div>
  </div>;
}

export function CenterDetailActions({state, actions}) {
  return <>
    <button type="button" className="primary" disabled={state.busy} onClick={actions.favorite}>
      {state.item.favorite ? '★ 已收藏' : '☆ 收藏'}
    </button>
    {state.photo && <>
      <button type="button" disabled={state.index === 0} onClick={() => actions.move(-1)}>上一张</button>
      <span className="muted">{state.index + 1} / {state.total}</span>
      <button type="button" disabled={state.index >= state.total - 1} onClick={() => actions.move(1)}>下一张</button>
    </>}
    {state.restored && <span className="muted">备份快照：重新扫描后才能打开原文件。</span>}
  </>;
}

export function mountCenterDetail(content, buttons) {
  const contentRoot = createRoot(content);
  const actionsRoot = createRoot(buttons);
  return {
    render(props) {
      flushSync(() => {
        contentRoot.render(<CenterDetailContent {...props} />);
        actionsRoot.render(<CenterDetailActions {...props} />);
      });
    },
    dispose() {
      contentRoot.unmount();
      actionsRoot.unmount();
    },
  };
}
