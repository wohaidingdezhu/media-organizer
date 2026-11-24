import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function BasketControls({state, actions}) {
  return <>
    <p id="basket-summary" role="status" aria-live="polite" className={state.error ? 'warning' : ''}>
      {state.action ? '正在保存候选篮，请稍候…' : state.loading ? '正在读取候选篮，请稍候…' : state.summary}
    </p>
    <div className="manage-controls" aria-busy={state.loading || !!state.action}>
      <button type="button" id="basket-add" disabled={!state.canAdd} onClick={actions.add}>{state.action === 'add' ? '正在加入…' : '将勾选项加入候选篮'}</button>
      <button type="button" id="basket-view" disabled={!state.canView} onClick={actions.view}>查看候选篮</button>
      <button type="button" id="basket-select" disabled={!state.canSelect} onClick={actions.select}>勾选候选篮可处理项</button>
      <button type="button" id="basket-remove" disabled={!state.canRemove} onClick={actions.remove}>{state.action === 'remove' ? '正在移出…' : '将勾选项移出候选篮'}</button>
      <button type="button" id="basket-clear" disabled={!state.canClear} onClick={actions.clear}>{state.action === 'clear' ? '正在清空…' : '清空候选篮'}</button>
      <button type="button" id="basket-refresh" disabled={!state.canRefresh} onClick={actions.refresh}>{state.loading ? '正在读取…' : '刷新候选篮'}</button>
    </div>
  </>;
}

export function mountBasketControls(host, actions) {
  const root = createRoot(host);
  return state => flushSync(() => root.render(<BasketControls state={state} actions={actions} />));
}
