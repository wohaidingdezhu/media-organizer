import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function WallUserControls({state, actions}) {
  return <>
    <select aria-label="按用户名筛选" value={state.filter} disabled={!state.ready || state.busy}
      onChange={event=>actions.filter(event.target.value)}>
      {[['','全部用户名'],['unassigned','未分配'],['mixed','归属不一致'],...state.users.map(user=>[user.id,user.name])].map(([value,label])=>
        <option key={value} value={value}>{label}</option>)}
    </select>
    <select aria-label="选择归属用户名" value={state.assign} disabled={!state.ready || state.busy || state.locked}
      onChange={event=>actions.assign(event.target.value)}>
      <option value="">未分配</option>{state.users.map(user=><option key={user.id} value={user.id}>{user.name}</option>)}
    </select>
    <button type="button" disabled={!state.canSave} onClick={actions.save}>
      {state.phase==='save'?'正在保存归属…':`保存勾选项归属（${state.count} 项）`}
    </button>
    <button type="button" disabled={state.busy} onClick={actions.refresh}>{state.phase==='load'?'正在读取用户名…':'刷新用户名'}</button>
    <span role="status" aria-live="polite">{state.message}</span>
    {state.films && <button type="button" disabled={state.busy || state.locked} onClick={actions.clear}>清空影片勾选</button>}
  </>;
}

export function mountWallUserControls(host, actions) {
  const root=createRoot(host);
  return state=>flushSync(()=>root.render(<WallUserControls state={state} actions={actions}/>));
}
