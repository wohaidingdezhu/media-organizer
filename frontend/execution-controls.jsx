import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function ExecutionControls({state, actions}) {
  const locked = state.busy || state.submitting;
  const feedback = state.submitting ? '正在提交文件操作，请稍候…'
    : state.hasPreview && state.busy ? '文件操作状态正在更新，当前清单暂不能提交。可以取消并稍后重新预览。' : '';
  return <>
    <div className="pager">
      <button id="execution-previous" type="button" disabled={locked || state.page === 0} onClick={actions.previous}>上一页确认清单</button>
      <span id="execution-page">{state.pageLabel}</span>
      <button id="execution-next" type="button" disabled={locked || state.page + 1 >= state.pages} onClick={actions.next}>下一页确认清单</button>
    </div>
    <p id="execution-error" className="minor" role="status" hidden={!feedback}>{feedback}</p>
    <label htmlFor="execution-confirm">
      <input id="execution-confirm" type="checkbox" checked={state.confirmed}
        disabled={locked || !state.available} onChange={event => actions.confirm(event.target.checked)} />
      我已核对文件与目标位置
    </label>
    <div className="actions">
      <button id="execution-cancel" type="button" disabled={state.submitting} onClick={actions.cancel}>取消</button>
      <button id="execution-start" type="button" className="primary"
        disabled={locked || !state.available || !state.confirmed} onClick={actions.submit}>
        {state.submitting ? '正在提交…' : '确认执行'}
      </button>
    </div>
  </>;
}

export function mountExecutionControls(host, actions) {
  const root = createRoot(host);
  return state => flushSync(() => root.render(<ExecutionControls state={state} actions={actions} />));
}
