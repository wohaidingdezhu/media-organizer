import React from 'react';
import {createPortal, flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {CenterArtwork, PreviewRepair} from './center-artwork.jsx';

const ReviewFile = React.memo(function ReviewFile({entry, artwork, repair, group, first, exact, checked, canTrash, canPlay, canKeep, actions}) {
  const {file, owner, facts} = entry;
  return <article className="review-file">
    <label className="review-pick">
      <input type="checkbox" checked={checked} disabled={!canTrash}
        aria-label={'选择清理 ' + file.path} onChange={event => actions.select(file.id, event.target.checked)} />
      <span>选择清理</span>
    </label>
    <h3>{file.name}</h3>
    <p className="muted review-facts">{facts}</p>
    <div className="actions">
      <PreviewRepair repair={repair} onClick={entry.repairPreview} />
      {file.kind === 'video' && <button type="button" aria-label={'播放 ' + file.path}
        disabled={!canPlay} onClick={() => actions.play(owner, file)}>播放</button>}
      {group.files.length > 2 && file.kind === 'photo' && first.kind === 'photo' && file.id !== first.id &&
        <button type="button" onClick={() => actions.compare([first.id, file.id])}>与第一张对比</button>}
      {exact && group.files.length > 1 &&
        <button type="button" aria-label={'保留 ' + file.path + '，勾选其余副本'} disabled={!canKeep}
          title={group.files.length > 201 ? '其余副本超过200个文件，请使用逐项删除分批清理。' : undefined}
          onClick={() => actions.keep(group, file.id)}>保留这份，勾选其余</button>}
      <button type="button" className="danger" disabled={!canTrash} aria-label={'删除 ' + file.path}
        onClick={() => actions.trash([file.id])}>删除…</button>
    </div>
    <button type="button" className="cover" aria-label={'预览 ' + file.name} ref={entry.attachCover}
      onClick={() => { if (owner) actions.openDetail(owner); }}>
      <CenterArtwork artwork={artwork} title={file.name} photo={file.kind === 'photo'} />
    </button>
    <p className="muted">{file.path}</p>
  </article>;
});

function ReviewGroup({group, state, actions}) {
  const {exact, dismissed, busy} = state;
  const first = group.files[0].file;
  return <section className="review-group">
    <h2>{exact ? '精确重复' : '相似照片'} · 第 {group.number} 组</h2>
    {exact && <p className="muted">{group.sizeSummary}</p>}
    {exact && group.files.length > 201 &&
      <p className="muted">其余副本超过 200 个文件，请分批勾选清理，每次都会保留至少一份。</p>}
    {!exact && dismissed && <p className="muted">{group.decision === 'keep_both' ? '相似，保留两张' : '已确认不相似'}</p>}
    <div className="actions review-group-actions">
      {!exact && (dismissed
        ? <button type="button" disabled={busy} onClick={group.restore}>恢复到待核对</button>
        : <>
          <button type="button" disabled={busy} onClick={() => group.decide('not_similar')}>确认不相似</button>
          <button type="button" disabled={busy} onClick={() => group.decide('keep_both')}>相似，保留两张</button>
        </>)}
      {group.files.length === 2 && group.files.every(entry => entry.file.kind === 'photo') &&
        <button type="button" onClick={() => actions.compare(group.files.map(entry => entry.file.id), group.decide)}>对比这两张</button>}
    </div>
    <div className="review-files">
      {group.files.map(entry => <ReviewFile key={entry.file.id} entry={entry} artwork={entry.artwork} repair={entry.repair}
        group={group} first={first} exact={exact} checked={state.selected.has(entry.file.id)} canKeep={group.allowKeep}
        canTrash={actions.canTrash(entry.file.id)} canPlay={Boolean(entry.owner && actions.canOpenFile(entry.file))} actions={actions} />)}
    </div>
  </section>;
}

export function CenterReview({state, groups, actions}) {
  const {mode, exact, dismissed, notes, busy, available, spaceSort, selected, feedback} = state;
  return <>
    <div className="actions">
      {[['exact', '精确重复'], ['similar', '相似照片']].map(([key, label]) =>
        <button type="button" key={key} className={mode === key ? 'primary' : undefined} aria-pressed={mode === key}
          onClick={() => actions.setMode(key)}>{label} · {state.counts[key]}</button>)}
      {!exact && <button type="button" onClick={actions.toggleDismissed}>{dismissed ? '返回待核对' : '已核对 · ' + state.dismissedCount}</button>}
    </div>
    <p className="muted">{exact
      ? '这些文件经完整 SHA-256 确认内容相同。自动勾选会保留所有已收藏项；未收藏的组会保留最后一份。删除前请核对。'
      : '这些只是视觉相似候选，可能是不同照片。先并排对比，再决定保留或删除。'}</p>
    {notes.map((note, index) => <p className="notice" key={index}>{note}</p>)}
    {exact && <>
      <button type="button" aria-pressed={spaceSort} onClick={actions.sortSpace}>
        {spaceSort ? '恢复原排序' : '优先查看可清理空间大的组'}
      </button>
      <p className="muted">空间按保留一份估算，不代表实际释放；文件移到废纸篓 / 回收站后，通常需清空才会释放空间。收藏项仍按选择规则保留。</p>
    </>}
    <div className="actions review-selection-tools">
      <button type="button" disabled={!available} onClick={actions.selectAll}>
        {exact ? '自动选择副本（保留收藏）' : '全选当前结果'}
      </button>
      <button type="button" disabled={!available} onClick={actions.invert}>反选当前结果</button>
    </div>
    {groups.map(group => <ReviewGroup key={group.key} group={group} state={state} actions={actions} />)}
    <div className="actions review-batch" hidden={!selected.size && !feedback}>
      <strong>已选 {selected.size} 个文件（可跨页选择） · {state.chosenSize}</strong>
      <button type="button" disabled={busy || !selected.size} onClick={actions.clear}>清空选择</button>
      <button type="button" className="danger" disabled={!state.allowBatch} onClick={actions.trashSelected}>删除所选…</button>
      <p className="review-selection-feedback" role="status" hidden={!feedback}>{feedback}</p>
    </div>
    {state.empty && <p className="empty">{state.empty}</p>}
  </>;
}

export function mountCenterReview(container) {
  // A portal keeps the toolbar, review groups and sticky batch bar alongside
  // the current scan notices, with React owning their complete lifetime.
  const root = createRoot(container.ownerDocument.createElement('div'));
  return {
    render(props) { flushSync(() => root.render(createPortal(<CenterReview {...props} />, container))); },
    dispose() { root.unmount(); },
  };
}
