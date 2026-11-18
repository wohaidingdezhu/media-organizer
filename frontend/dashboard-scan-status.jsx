import React, {useLayoutEffect, useRef} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';

export function scanProgressState(data, pending) {
  const stage = data.progress;
  const known = Number.isFinite(stage?.percent) && stage.total > 0;
  const amount = number => stage.stage === 'hash_file'
    ? (number / 1048576).toFixed(1) + ' MB' : number.toLocaleString('zh-CN');
  let label = stage?.label || '正在准备扫描…';
  if (known) label += ` · ${stage.percent}%（${amount(stage.completed)}/${amount(stage.total)}）`;
  else if (stage?.completed) label += ` · 已发现 ${stage.completed} 个文件`;
  let status = data.running && stage ? label : data.status;
  let value = known ? stage.percent : undefined;
  if (data.running && data.cancel_requested) {
    label = '正在取消扫描…';
    status = '正在取消扫描，等待当前读取或媒体组件退出；原文件不会修改。';
    value = undefined;
  } else if (pending === 'scan/cancel') {
    label = status = '正在提交取消请求…';
    value = undefined;
  } else if (pending) {
    status = pending === 'folders/add' ? '请在系统窗口选择文件夹，完成或取消后返回。' : '正在提交请求，请稍候…';
  }
  return {running:data.running, label, status, value, current:stage?.current || '',
    log:data.logs.join('\n') || '扫描记录会显示在这里。'};
}

export function ScanBanner({state}) {
  return <div id="scan-banner" className="scan-banner" hidden={!state.running}>
    <strong id="scan-progress-label">{state.label}</strong>
    <progress id="scan-banner-progress" aria-label="当前扫描阶段进度" max={100} value={state.value} />
    <small id="scan-progress-current">{state.current}</small>
    <span>可以继续浏览已有结果。进度按当前阶段显示。</span>
  </div>;
}

export function ScanPanel({state, logRef, onScroll, onToggle}) {
  return <>
    <div id="status" role="status" className={'status' + (state.running ? ' busy' : '')}>
      <span className="dot" /><span id="status-text">{state.status}</span>
    </div>
    <div id="bar" hidden={!state.running}>
      <progress id="scan-progress" aria-label="当前扫描阶段进度" max={100} value={state.value} />
    </div>
    <details id="scan-details" style={{marginTop:16}} onToggle={onToggle}>
      <summary>查看扫描记录</summary>
      <pre id="log" className="log" aria-label="扫描记录" ref={logRef} onScroll={onScroll}>{state.log}</pre>
    </details>
  </>;
}

function ScanPanelWithLog({state}) {
  const log = useRef(null);
  const follow = useRef(true);
  function scrollToLatest() {
    if (follow.current) log.current.scrollTop = log.current.scrollHeight;
  }
  useLayoutEffect(scrollToLatest, [state.log]);
  return <ScanPanel state={state} logRef={log} onToggle={event => {
    if (event.currentTarget.open) scrollToLatest();
  }} onScroll={event => {
    const {scrollHeight, scrollTop, clientHeight} = event.currentTarget;
    follow.current = scrollHeight - scrollTop - clientHeight <= 8;
  }} />;
}

export function mountDashboardScanStatus(banner, panel) {
  const bannerRoot = createRoot(banner);
  const panelRoot = createRoot(panel);
  return (data, pending) => {
    const state = scanProgressState(data, pending);
    flushSync(() => {
      bannerRoot.render(<ScanBanner state={state} />);
      panelRoot.render(<ScanPanelWithLog state={state} />);
    });
  };
}
