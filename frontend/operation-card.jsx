import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {OperationFilters, OperationPager} from './operation-controls.jsx';
import {OperationRows} from './operation-progress.jsx';

export function OperationRecovery({state, actions}) {
  const result = state.recovery;
  return <div role="status" hidden={!state.canReview}>
    {state.recovering && <p>正在核对未处理文件，请稍候…</p>}
    {result?.error && <p className="warning">{result.error}</p>}
    {result?.advice && <>
      <p>已勾选 {result.advice.ids.length} 项，跳过 {result.advice.skipped.length} 项。{result.advice.message}</p>
      {!!result.advice.skipped.length && <details>
        <summary>查看跳过原因</summary>
        {result.advice.skipped.slice(state.recoveryPage * 50, (state.recoveryPage + 1) * 50).map((item, index) =>
          <p className="path" key={state.recoveryPage * 50 + index}>{item.path}：{item.reason}</p>)}
        <div className="pager">
          <button type="button" disabled={state.recoveryPage === 0 || state.recovering} onClick={actions.previousRecovery}>上一页跳过原因</button>
          <span>第 {state.recoveryPage + 1}/{Math.ceil(result.advice.skipped.length / 50)} 页 · 共 {result.advice.skipped.length} 项</span>
          <button type="button" disabled={(state.recoveryPage + 1) * 50 >= result.advice.skipped.length || state.recovering} onClick={actions.nextRecovery}>下一页跳过原因</button>
        </div>
      </details>}
    </>}
  </div>;
}

export function OperationCard({state, actions}) {
  const {job, counts, active} = state;
  const status = {running:'执行中', external_running:'另一服务执行中', complete:'已完成',
    stopped:'遇到问题已停止', cancelled:'已安全停止', interrupted:'服务曾中断'};
  const title = job.quick ? (job.queue ? `省心整理自动分批 ${job.completed_batches}/${job.batch_count}` : '省心整理')
    : job.bundle ? (job.bundle_kind === 'photo' ? '照片和配套文件整组复制' : '影片和附件整组复制')
    : job.mode === 'copy' ? '复制分类' : '废纸篓 / 回收站清理';
  return <>
    <strong>{title} · {status[job.status] || job.status} · {counts.success}/{job.total}</strong>
    <p className="muted">{job.created_at || ''}</p>
    <p>成功 {counts.success} · 需核对 {counts.attention} · 处理中 {counts.processing} · 未开始 {counts.pending} · 已取消 {counts.cancelled}</p>
    <pre hidden={!state.current}>{state.current}</pre>
    <p className="warning" hidden={!job.error}>{job.error || ''}</p>
    <button type="button" hidden={!active} disabled={!!job.stop_requested || state.stopping} onClick={actions.stop}>
      {job.stop_requested ? '正在安全停止…' : state.stopping ? '正在提交停止请求…'
        : job.queue ? '安全停止整个整理任务' : '安全停止此批次'}
    </button>
    <p className="muted" hidden={!active}>{job.mode === 'copy'
      ? '停止会清理当前未完成临时副本，已完成副本保留；磁盘正在响应时可能需要等待。'
      : '当前文件的系统废纸篓 / 回收站操作会完成，再停止后续项；不会自动恢复已成功项。'}</p>
    <button type="button" hidden={!counts.attention} onClick={actions.attention}>查看需核对的 {counts.attention} 项</button>
    <div className="manage-controls"><OperationFilters state={state} actions={actions} /></div>
    <div><OperationRows rows={state.rows} /></div>
    <div className="pager"><OperationPager state={state} actions={actions} /></div>
    <p className="warning" hidden={!!job.planned_items || job.total <= job.items.length}>
      还有 {job.total - job.items.length} 项未记录处理结果。旧记录没有完整批次清单，请对照报告核对。
    </p>
    <button type="button" hidden={!state.canReview} disabled={state.busy || state.recovering} onClick={actions.review}>
      {state.recovering ? '正在核对未处理项…' : '核对此批次未处理项'}
    </button>
    <OperationRecovery state={state} actions={actions} />
    <p className="warning" hidden={!(job.bundle || job.quick) || active || job.status === 'complete'}>
      整组未全部完成，请按媒体和配套文件逐项核对。已成功副本保留；重新整组预览遇到已有目标会拒绝覆盖，不会自动重试。
    </p>
    <p className="muted">记录保存在本次报告的 operations 文件夹。已成功项不会自动撤销；结果未确认时先在 文件管理器 核对。</p>
  </>;
}

export function mountOperationCard(host, actions) {
  const root = createRoot(host);
  return {
    render(state) { flushSync(() => root.render(<OperationCard state={state} actions={actions} />)); },
    dispose() { root.unmount(); },
  };
}
