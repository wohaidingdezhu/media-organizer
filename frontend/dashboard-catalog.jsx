import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';

function CatalogCard({item, open}) {
  const [ready, setReady] = useState(false);
  const preview = typeof item.id === 'string' && /^[0-9a-f]{64}$/.test(item.id)
    && typeof item.report_id === 'string' && /^scan-[A-Za-z0-9_-]{1,120}$/.test(item.report_id)
    ? 'api/catalog/preview?id=' + encodeURIComponent(item.id) + '&report_id=' + encodeURIComponent(item.report_id)
    : '';
  return <article className="catalog-card">
    <button type="button" className="catalog-cover" aria-label={'查看媒体详情 ' + item.name} onClick={() => open(item)}>
      <span className="catalog-placeholder" hidden={ready}>{item.kind === '视频' ? '视频' : '照片'} · 暂无预览图</span>
      {preview && <img src={preview} alt="" loading="lazy" hidden={!ready} onLoad={() => setReady(true)} onError={() => setReady(false)} />}
    </button>
    <div className="catalog-info">
      <strong>{item.name}</strong>
      <div className="sub">{item.path}</div>
      <div className="sub">{item.statusText}</div>
      <button type="button" className="small" onClick={() => open(item)}>查看详情</button>
    </div>
  </article>;
}

export function mountDashboardCatalog(folders, cards, onFolder, onOpen) {
  const folderRoot = createRoot(folders);
  const cardRoot = createRoot(cards);
  return ({items, folders, loading}) => {
    folderRoot.render(folders.map(folder => <button type="button" className="ghost" key={folder.path}
      title={folder.path} onClick={() => onFolder(folder.path)}>{folder.name} · {folder.count}</button>));
    cardRoot.render(loading ? null : items.length ?
      items.map(item => <CatalogCard key={item.report_id + ':' + item.id} item={item} open={onOpen} />) :
      <p className="empty">没有符合条件的媒体；先完成扫描或调整筛选。</p>);
  };
}
