import React, {useMemo} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function executionSummary(preview) {
  const totals = new Map();
  if (preview.quick) for (const item of preview.items) {
    const folder = item.relative
      ? item.relative.split('/').slice(0, item.relative.startsWith('电影/') ? 2 : 3).join('/')
      : '分类目录';
    totals.set(folder, (totals.get(folder) || 0) + 1);
  }
  return {
    folders: [...totals].slice(0, 30),
    notes: [...new Set((preview.groups || []).flatMap(group => group.notes || []))],
  };
}

export function ExecutionItems({preview, keeper, page, summary}) {
  const photoBundle = preview.bundle_kind === 'photo';
  const start = page * 100, end = start + 100;
  const copies = preview.items.slice(start, end);
  const skipped = (preview.skipped || []).slice(Math.max(0, start - preview.items.length),
    Math.max(0, end - preview.items.length));
  return <>
    {keeper && <li><strong>本组保留项</strong><div className="path">{keeper}</div></li>}
    {preview.quick && <>
      {!preview.items.length && <li>{(preview.skipped || []).some(item => !['handled', 'held'].includes(item.category))
        ? '没有可执行文件，请查看需要处理的问题。' : '本次无需复制；已有成功记录和暂缓项可在整理检查清单查看。'}</li>}
      <li>{preview.queue ? '一次确认后自动执行' : '本次执行'} {preview.batch_count ?? 1} 批 · 未加入 {(preview.skipped || []).length} 项。每组配套文件不拆开。</li>
      {preview.remaining_count > 0 && <li className="warning">还有 {preview.remaining_count} 个文件留待下一批检查；超过单次确认范围的文件须再次预览。</li>}
      {summary.folders.map(([folder, count]) => <li key={folder}>{folder} · {count} 项</li>)}
      {summary.notes.map(note => <li key={note} className="warning">{note}</li>)}
    </>}
    {photoBundle && <li className="warning">同名视频仅为配对候选，请人工核对。配套文件使用照片目录和原文件名；展开项也须确认。</li>}
    {(preview.bundles || []).slice(0, 30).map((group, index) => <li key={'group-' + index}>
      <strong>{group.title}{group.edition ? ' · ' + group.edition : ''}</strong>
      <p>{photoBundle
        ? Object.entries(group.counts).map(([label, count]) => label + ' ' + count).join(' · ')
        : `${group.videos} 视频 · 字幕 ${group.counts.subtitle} · NFO ${group.counts.nfo} · 封面 ${group.counts.cover} · 剧照 ${group.counts.still}`}</p>
    </li>)}
    {copies.map(item => <li key={'copy-' + item.path}>
      <strong>{item.role || '媒体'}</strong><div className="path">{item.path}</div><div className="path">→ {item.target}</div>
    </li>)}
    {skipped.map((item, index) => <li key={'skip-' + (start + index)} className="warning">
      <strong>未加入复制：{item.reason}</strong><div className="path">{item.path}</div>
    </li>)}
  </>;
}

function ExecutionPreview(props) {
  const summary = useMemo(() => executionSummary(props.preview), [props.preview]);
  return <ExecutionItems {...props} summary={summary} />;
}

export function mountExecutionPreview(host) {
  const root = createRoot(host);
  return props => flushSync(() => root.render(<ExecutionPreview {...props} />));
}
