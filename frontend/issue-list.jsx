import React, {forwardRef, useImperativeHandle, useMemo, useState} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {
  buildIssues, categories, filterIssues, issueCsv, issueReason,
  pageSize, scopedIssues, selectionLimit,
} from './issue-model.js';

const initialFilters = {status: 'review', kind: '', search: '', reason: ''};

function keepApplicableReason(issues, filters) {
  if (!filters.reason || scopedIssues(issues, filters).some(item => issueReason(item) === filters.reason)) {
    return filters;
  }
  return {...filters, reason: ''};
}

function IssueRow({item, checked, disabled, onToggle}) {
  return (
    <div className="operation">
      <label>
        <input
          type="checkbox"
          aria-label={'选择问题项 ' + item.path}
          checked={checked}
          disabled={disabled}
          onChange={event => onToggle(item.path, event.target.checked)}
        />
        <strong>{categories[item.category]} · {item.reason}</strong>
      </label>
      <div className="path">{item.path}</div>
    </div>
  );
}

function exportIssues(issues) {
  const url = URL.createObjectURL(new Blob([issueCsv(issues)], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = '整理检查清单.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const IssueList = forwardRef(function IssueList({onError}, ref) {
  const [issues, setIssues] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [filters, setFilters] = useState(initialFilters);
  const [selected, setSelected] = useState(new Set());
  const [page, setPage] = useState(0);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [mediaIds, setMediaIds] = useState({});

  const scope = useMemo(() => scopedIssues(issues, filters), [issues, filters]);
  const reasons = useMemo(() => [...new Set(scope.map(issueReason))], [scope]);
  const rows = useMemo(() => filterIssues(scope, filters), [scope, filters]);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const canCorrect = item => item.category === 'review' && Boolean(mediaIds[item.path]);

  function resetSelection(nextNote) {
    setSelected(new Set());
    setNote(nextNote);
  }

  useImperativeHandle(ref, () => ({
    replace(preview, media) {
      const next = buildIssues(preview, media);
      setIssues(next);
      setLoaded(true);
      setFilters(current => keepApplicableReason(next, current));
      setPage(0);
      resetSelection('');
    },
    updateControls(locked, ids) {
      setBusy(locked);
      setMediaIds(ids);
    },
    selectedPaths: () => [...selected],
    clearSelection: resetSelection,
  }));

  function changeFilter(key, value) {
    if (busy) return;
    const next = {...filters, [key]: value};
    setFilters(key === 'status' || key === 'kind' ? keepApplicableReason(issues, next) : next);
    setPage(0);
    resetSelection('筛选已更新，问题勾选已清空。');
  }

  function toggle(path, checked) {
    if (busy) return;
    if (checked && selected.size >= selectionLimit) {
      onError('一次最多修正 200 个问题媒体。');
      return;
    }
    const next = new Set(selected);
    if (checked) next.add(path);
    else next.delete(path);
    setSelected(next);
  }

  function selectResults() {
    const eligible = rows.filter(canCorrect);
    const picked = eligible.slice(0, selectionLimit);
    setSelected(new Set(picked.map(item => item.path)));
    const extra = eligible.length - picked.length;
    setNote(`已替换问题勾选，选中当前筛选的 ${picked.length} 项（跨页）` +
      (extra ? `；还有 ${extra} 项未勾选，请分次修正。` : '。'));
  }

  const counts = useMemo(() => {
    const totals = {review: 0, handled: 0, held: 0};
    for (const item of issues) totals[item.category]++;
    return Object.entries(totals).map(([key, count]) => `${categories[key]} ${count} 项`).join(' · ');
  }, [issues]);

  let emptyMessage = '生成整理预览后，这里会显示需要处理的问题和未加入原因。';
  if (issues.length) emptyMessage = '当前筛选没有待查看项，可切换状态查看已有成功记录或暂缓项。';
  else if (loaded) emptyMessage = '本次预览没有需要处理的问题或跳过项；请核对复制清单。';

  return (
    <>
      <p id="quick-issues-counts" role="status">{counts}</p>
      <div className="manage-controls">
        <select id="quick-issues-status" aria-label="筛选整理检查状态" value={filters.status}
          disabled={busy} onChange={event => changeFilter('status', event.target.value)}>
          {Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          <option value="all">全部状态</option>
        </select>
        <select id="quick-issues-kind" aria-label="筛选问题媒体类型" value={filters.kind}
          disabled={busy} onChange={event => changeFilter('kind', event.target.value)}>
          <option value="">全部类型</option>
          {['照片', '视频', '附件'].map(kind => <option key={kind}>{kind}</option>)}
        </select>
        <input id="quick-issues-search" type="search" aria-label="搜索整理问题"
          placeholder="搜索路径或原因…" value={filters.search} disabled={busy}
          onChange={event => changeFilter('search', event.target.value)} />
        <select id="quick-issues-reason" aria-label="筛选整理问题原因" value={filters.reason}
          disabled={busy} onChange={event => changeFilter('reason', event.target.value)}>
          <option value="">全部原因</option>
          {reasons.map(reason => <option key={reason} value={reason}>{reason}</option>)}
        </select>
        <button id="quick-issues-export" type="button" disabled={!issues.length} onClick={() => exportIssues(issues)}>
          导出全部检查记录 CSV
        </button>
      </div>
      <div className="manage-controls">
        <button id="quick-issues-select" type="button" disabled={busy || !rows.some(canCorrect)} onClick={selectResults}>
          勾选筛选结果（最多 200 项）
        </button>
        <button id="quick-issues-clear" type="button" disabled={busy || !selected.size}
          onClick={() => resetSelection('已清空问题勾选。')}>清空问题勾选</button>
      </div>
      <p className="muted">批量勾选跨页，只选择可修正的媒体。切换筛选会清空问题勾选。修正地区或视频类型时请先筛选视频。</p>
      <p id="quick-issues-selection-note" role="status">{note}</p>
      <p id="quick-issues-summary">当前 {rows.length}/{issues.length} 项 · 第 {page + 1}/{pages} 页 · 已勾选 {selected.size} 项</p>
      <div id="quick-issues-list">
        {rows.slice(page * pageSize, (page + 1) * pageSize).map(item => (
          <IssueRow key={item.path} item={item} checked={selected.has(item.path)}
            disabled={busy || !canCorrect(item)} onToggle={toggle} />
        ))}
        {!rows.length && <p className="empty">{emptyMessage}</p>}
      </div>
      <div className="pager">
        <button id="quick-issues-previous" type="button" disabled={busy || page === 0}
          onClick={() => setPage(page - 1)}>上一页问题</button>
        <button id="quick-issues-next" type="button" disabled={busy || page >= pages - 1}
          onClick={() => setPage(page + 1)}>下一页问题</button>
      </div>
    </>
  );
});

// The existing copy preview and correction workflow calls this interface.
globalThis.mountIssueList = function mountIssueList(container, onError) {
  const ref = React.createRef();
  const root = createRoot(container);
  flushSync(() => root.render(<IssueList ref={ref} onError={onError} />));
  return {
    replace: (preview, media) => flushSync(() => ref.current.replace(preview, media)),
    updateControls: (busy, ids) => flushSync(() => ref.current.updateControls(busy, ids)),
    selectedPaths: () => ref.current.selectedPaths(),
    clearSelection: note => flushSync(() => ref.current.clearSelection(note)),
  };
};
