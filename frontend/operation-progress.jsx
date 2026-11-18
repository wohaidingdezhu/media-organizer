import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function OperationRows({rows}) {
  if (!rows.length) return <p className="muted">当前筛选没有文件。</p>;
  return rows.map(row => <pre key={row.path} className={row.attention ? 'warning' : ''}>{row.text}</pre>);
}

export function mountOperationRows(host) {
  const root = createRoot(host);
  return {
    render(rows) { flushSync(() => root.render(<OperationRows rows={rows} />)); },
    dispose() { root.unmount(); },
  };
}
