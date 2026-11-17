import React, {useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';

const pageSize = 100;

function ReportClassification({rows}) {
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(0);
  const needle = query.trim().toLocaleLowerCase();
  const filtered = useMemo(() => rows.filter(item => (!kind || item.kind === kind) &&
    (!needle || [item.path, item.suggested_path, item.reason].some(value => String(value ?? '').toLocaleLowerCase().includes(needle)))),
  [rows, kind, needle]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(page, pages - 1);
  return <>
    <div className="controls">
      <input id="search" type="search" aria-label="筛选全部分类建议" placeholder="搜索全部文件名、目录或建议"
        value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} />
      <select id="kind-filter" aria-label="按媒体类型筛选" value={kind}
        onChange={event => { setKind(event.target.value); setPage(0); }}>
        <option value="">全部类型</option><option value="照片">照片</option><option value="视频">视频</option>
      </select>
    </div>
    <div className="scroll">
      <table>
        <thead><tr><th>类型</th><th>原文件</th><th>建议路径（仅预览）</th><th>判断依据</th></tr></thead>
        <tbody id="plan-rows">
          {filtered.slice(current * pageSize, (current + 1) * pageSize).map((item, index) =>
            <tr key={current * pageSize + index}><td>{item.kind}</td><td>{item.path}</td>
              <td>{item.suggested_path}</td><td>{item.reason}</td></tr>)}
          {!filtered.length && <tr><td colSpan={4}>没有匹配的分类建议。</td></tr>}
        </tbody>
      </table>
    </div>
    <div className="pager">
      <button id="previous" type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>上一页</button>
      <span id="plan-count" role="status">共 {filtered.length} 条 · 第 {current + 1}/{pages} 页</span>
      <button id="next" type="button" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>下一页</button>
    </div>
  </>;
}

export function mountReportClassification(host, rows) {
  createRoot(host).render(<ReportClassification rows={rows} />);
}
