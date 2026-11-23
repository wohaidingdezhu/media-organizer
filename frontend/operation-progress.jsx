import React from 'react';

export function OperationRows({rows}) {
  if (!rows.length) return <p className="muted">当前筛选没有文件。</p>;
  return rows.map(row => <pre key={row.path} className={row.attention ? 'warning' : ''}>{row.text}</pre>);
}
