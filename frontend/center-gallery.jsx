import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {CenterArtwork, PreviewRepair} from './center-artwork.jsx';

const MediaCard = React.memo(function MediaCard({entry, artwork, repair, manage, checked, busy, allowedToOpen, allowedToTrash, ui}) {
  const {item, coverLabel, meta, search, videoInfo} = entry;
  const video = item.kind !== 'photo';
  return <article className={'card' + (video ? '' : ' photo-card')} data-media-id={item.id}>
    <button ref={entry.attachCover} type="button" className="cover" aria-label={coverLabel}
      onClick={() => ui.openDetail(item)}><CenterArtwork artwork={artwork} title={item.title} photo={!video} /></button>
    {manage && <label className="pick">
      <input type="checkbox" checked={checked} disabled={busy} aria-label={'选择 ' + item.title}
        onChange={event => ui.select(item, event.target.checked)} />
    </label>}
    {item.favorite && <span className="favorite-mark">★</span>}
    <h3>{item.title}</h3>
    <div className="meta">{meta}</div>
    {item.preview_status && !artwork.repaired && <div className="meta preview-status">{item.preview_status}</div>}
    {video && <div className="meta video-facts">{videoInfo}</div>}
    {search && <div className="meta search-path" title={search.title}>{search.text}</div>}
    <div className="card-actions">
      <PreviewRepair repair={repair} onClick={entry.repairPreview} />
      <button type="button" disabled={busy} onClick={() => ui.favorite(item)}>{item.favorite ? '★ 已收藏' : '☆ 收藏'}</button>
      {video && item.files.length === 1 &&
        <button type="button" disabled={!allowedToOpen}
          onClick={() => ui.openOriginal(item, item.files[0])}>播放</button>}
      {video && item.files.length > 1 &&
        <button type="button" title="选择要播放的版本或分段" onClick={() => ui.openDetail(item)}>选择播放</button>}
      {(manage || video) && <button type="button" className="danger" disabled={!allowedToTrash}
        onClick={() => ui.trash(entry.ids)}>删除…</button>}
    </div>
  </article>;
});

export function CenterGallery({entries, ui}) {
  return entries.map(entry => <MediaCard key={entry.item.id} entry={entry} ui={ui} manage={ui.manage} busy={ui.busy}
    checked={ui.selected.has(entry.item.id)} artwork={entry.artwork} repair={entry.repair}
    allowedToOpen={entry.item.files.length === 1 && ui.canOpenFile(entry.item.files[0])}
    allowedToTrash={ui.canTrash(entry.ids)} />);
}

export function mountCenterGallery(container) {
  const root = createRoot(container);
  return {
    render(entries, ui) {
      flushSync(() => root.render(<CenterGallery entries={entries} ui={ui} />));
    },
    dispose() {
      root.unmount();
    },
  };
}
