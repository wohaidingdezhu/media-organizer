import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function OperationFilters({state, actions}) {
  return <>
    <select aria-label={'筛选批次 ' + state.id + ' 的文件状态'} value={state.filter}
      onChange={event => actions.filter(event.target.value)}>
      {[['', '全部明细'], ['attention', '需要核对'], ['processing', '处理中'],
        ['pending', '未开始'], ['success', '成功'], ['cancelled', '已安全取消']].map(([value, label]) =>
        <option key={value} value={value}>{label}</option>)}
    </select>
    <input type="search" aria-label={'搜索批次 ' + state.id + ' 的文件'}
      placeholder="搜索本批次路径或错误…" value={state.query}
      onInput={event => actions.search(event.target.value)} />
  </>;
}

export function OperationPager({state, actions}) {
  return <>
    <button type="button" disabled={state.page === 0} onClick={actions.previous}>上一页明细</button>
    <span>明细 {state.matching}/{state.total} 项 · 第 {state.page + 1}/{state.pages} 页（每页 50 项）</span>
    <button type="button" disabled={state.page + 1 >= state.pages} onClick={actions.next}>下一页明细</button>
  </>;
}

export function mountOperationControls(filters, pager, actions) {
  const filterRoot = createRoot(filters), pagerRoot = createRoot(pager);
  return {
    render(state) { flushSync(() => {
      filterRoot.render(<OperationFilters state={state} actions={actions} />);
      pagerRoot.render(<OperationPager state={state} actions={actions} />);
    }); },
    dispose() { filterRoot.unmount(); pagerRoot.unmount(); },
  };
}
