import React, {useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';

const archiveLimit = 64 * 1024 * 1024;

async function readReply(response) {
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '备份请求未完成');
  return data;
}

async function downloadBackup() {
  const response = await fetch('api/backup');
  if (!response.ok) await readReply(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = 'media-library-backup.zip';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function DashboardBackup({restore}) {
  const [action, setAction] = useState('');
  const [status, setStatus] = useState('');
  const [preview, setPreview] = useState(null);
  const inFlight = useRef(false);
  const chooser = useRef(null);

  async function run(name, waiting, task, failurePrefix='') {
    if (inFlight.current) return;
    inFlight.current = true;
    setAction(name);
    setStatus(waiting);
    try {
      await task();
    } catch (error) {
      setStatus(failurePrefix + error.message);
    } finally {
      inFlight.current = false;
      setAction('');
    }
  }

  function choose(file) {
    if (!file || inFlight.current) return;
    setPreview(null);
    if (!file.size || file.size > archiveLimit) {
      setStatus('请选择有效且不超过 64 MB 的 ZIP 备份。');
      return;
    }
    return run('check', '正在校验 ' + file.name + '，尚未恢复…', async () => {
      const response = await fetch('api/restore/preview', {
        method:'POST', headers:{'Content-Type':'application/zip'}, body:file,
      });
      const data = await readReply(response);
      if (typeof data.token !== 'string' || !data.token) throw Error('未收到有效的恢复确认，请重新选择备份。');
      setPreview({name:file.name, token:data.token, reports:data.reports, files:data.files, message:data.message});
      setStatus('校验通过，请确认要恢复的备份。');
    });
  }

  function confirm() {
    if (!preview || inFlight.current) return;
    const {name, token} = preview;
    return run('restore', '正在恢复 ' + name + '，请稍候…', async () => {
      setPreview(null);
      const data = await restore(token);
      setStatus(data.status);
    }, '恢复未确认，请核对当前资料库后再选择备份。');
  }

  return <section className="panel" aria-busy={Boolean(action)}>
    <h2>资料库备份与恢复</h2>
    <p>包含报告、预览、标签、收藏、评分和整理记录。原始照片与视频须另行备份。</p>
    <div className="row">
      <button id="backup-download" type="button" disabled={Boolean(action)} onClick={() =>
        run('backup', '正在核对并生成备份…', async () => {
          await downloadBackup();
          setStatus('备份已生成，请保存 ZIP 文件。');
        })}>下载备份 ZIP</button>
      <button type="button" className="ghost" disabled={Boolean(action)} onClick={() => chooser.current.click()}>
        选择备份…
      </button>
      <input ref={chooser} id="restore-file" type="file" accept=".zip,application/zip" hidden disabled={Boolean(action)}
        onChange={event => {
          const file = event.currentTarget.files[0];
          event.currentTarget.value = '';
          choose(file);
        }} />
    </div>
    <p id="backup-status" className="sub" role="status">{status}</p>
    {preview && <p id="restore-summary" className="sub">
      <strong>{preview.name}</strong> · {preview.reports} 次扫描、{preview.files} 个资料文件。{preview.message}
    </p>}
    <button id="restore-confirm" type="button" className="primary" hidden={!preview}
      disabled={Boolean(action) || !preview} aria-describedby={preview ? "restore-summary" : undefined} onClick={confirm}>确认恢复为独立副本</button>
  </section>;
}

export function mountDashboardBackup(host, restore) {
  createRoot(host).render(<DashboardBackup restore={restore} />);
}
