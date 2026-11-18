const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const paths = [String.raw`\\nas\share\图片`, '/generated/照片'];
const item = {id:'a'.repeat(64), report_id:'scan-sample', name:'样例.jpg', path:paths[1] + '/样例.jpg',
  kind:'照片', state:'new', created_at:'2026-10-01'};
let finishScan, finishCancel, finishPreview, finishRestore;
const requests = [], errors = [];
const ready = {running:false, status:'准备就绪', folders:paths, manual_scan_folders:[paths[0]],
  latest_report:'', monitor:{enabled:false}, logs:[]};
let status = ready;
const running = {...ready, running:true, logs:['读取样例视频'],
  progress:{stage:'video', label:'读取视频信息', total:2, completed:2, percent:100, current:'样例.mp4'}};
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(0, 'utf8'), {
  url: 'http://127.0.0.1:43210/generated/', runScripts: 'dangerously', virtualConsole: console,
  beforeParse(window) {
    window.scrollTo = () => {};
    window.fetch = async (route, options) => {
      const payload = typeof options?.body === 'string' ? JSON.parse(options.body) : null;
      requests.push({route,payload});
      const reply = data => ({ok:true,json:async()=>data});
      switch (route) {
        case 'api/status': return reply(status);
        case 'api/reports': return reply({reports:[]});
        case 'api/scan/start':
          return new Promise(resolve => {finishScan = () => resolve(reply(status = running));});
        case 'api/scan/cancel':
          return new Promise(resolve => {finishCancel = () => resolve(reply(status = {...running,
            cancel_requested:true, logs:[...running.logs, '已请求取消']}));});
        case 'api/restore/preview':
          return new Promise(resolve => {finishPreview = () => resolve(reply({token:options.body.name, reports:1, files:3}));});
        case 'api/restore/confirm':
          return new Promise((resolve, reject) => {
            finishRestore = succeeds => succeeds ? resolve(reply({...ready, folders:[], status:'已恢复为独立资料库副本'}))
              : reject(Error('连接已断开'));
          });
        case 'api/catalog': return reply({items:[item], folders:[{path:paths[1],name:'照片',count:1}], warnings:[],
          total:1, all_count:1, scans:1, page:0, pages:1});
        default: throw Error('Unexpected route: ' + route);
      }
    };
  },
});
const {document} = dom.window;
const get = id => document.getElementById(id);
const picks = () => [...document.querySelectorAll('#folders input')];
async function until(condition, message) {
  for (let i = 0; i < 200; i++) {
    if (condition()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

async function run() {
  await until(() => picks().length === 2, 'dashboard uses the shipped React scope');
  get('nav-scan').click();
  assert(picks()[0].checked && !picks()[1].checked);
  picks()[0].click();
  assert(get('scan').disabled);
  picks()[1].click();
  assert(picks()[1].checked && !get('scan').disabled);
  get('scan').click();
  get('scan').click();
  await until(() => finishScan && picks()[1].disabled, 'directory scope locks while submitting');
  const submissions = requests.filter(call => call.route === 'api/scan/start');
  assert.equal(submissions.length, 1);
  assert.deepEqual(submissions[0].payload.scan_folders, [paths[1]]);
  finishScan();
  await until(() => !get('cancel').disabled, 'scan starts');
  assert.equal(get('scan-banner-progress').getAttribute('value'), '100');
  assert(get('scan-progress-label').textContent.includes('100%'));

  const log = get('log');
  Object.defineProperties(log, {scrollHeight:{value:400,writable:true}, clientHeight:{value:100}});
  log.scrollTop = 70;
  log.dispatchEvent(new dom.window.Event('scroll'));
  get('cancel').click();
  get('cancel').click();
  await until(() => finishCancel, 'cancel request starts');
  assert.equal(requests.filter(call => call.route === 'api/scan/cancel').length, 1);
  assert(get('status-text').textContent.includes('正在提交取消请求'));
  assert(get('scan-progress-label').textContent.includes('正在提交取消请求'));
  for (const id of ['scan-progress', 'scan-banner-progress']) assert(!get(id).hasAttribute('value'));

  log.scrollHeight = 500;
  finishCancel();
  await until(() => log.textContent.includes('已请求取消'), 'accepted cancellation renders its log');
  assert.equal(log.scrollTop, 70);
  assert(get('scan-details').open);
  assert(get('status-text').textContent.includes('正在取消扫描'));
  assert(get('scan-progress-label').textContent.includes('正在取消扫描'));
  for (const id of ['scan-progress', 'scan-banner-progress']) assert(!get(id).hasAttribute('value'));
  get('cancel').click();
  assert.equal(requests.filter(call => call.route === 'api/scan/cancel').length, 1);

  log.scrollTop = 400;
  log.dispatchEvent(new dom.window.Event('scroll'));
  log.scrollHeight = 600;
  status = {...ready, status:'扫描已取消', logs:[...running.logs, '扫描已取消']};
  await until(() => !picks()[1].disabled, 'scope unlocks after cancellation');
  assert(get('scan-banner').hidden && get('bar').hidden);
  assert.equal(get('status-text').textContent, '扫描已取消');
  assert.equal(log.scrollTop, 600);
  assert(picks()[1].checked);

  get('nav-catalog').click();
  await until(() => document.querySelector('.catalog-card'), 'long-term catalog renders media card');
  const card = document.querySelector('.catalog-card'), image = card.querySelector('img'), placeholder = card.querySelector('.catalog-placeholder');
  assert(card.textContent.includes(item.path));
  assert(image.getAttribute('src').includes('report_id=scan-sample'));
  assert(!placeholder.hidden);
  image.dispatchEvent(new dom.window.Event('load'));
  await until(() => placeholder.hidden && !image.hidden, 'loaded preview');
  image.dispatchEvent(new dom.window.Event('error'));
  await until(() => !placeholder.hidden && image.hidden, 'fallback after preview error');
  document.querySelector('#catalog-folders button').click();
  await until(() => requests.some(call => call.route === 'api/catalog' && call.payload.folder === paths[1]), 'folder card preserves its full path');
  await until(() => get('catalog-location').textContent === paths[1] && document.querySelector('.catalog-card'), 'catalog request committed');
  get('nav-settings').click();
  const input = get('restore-file'), confirm = get('restore-confirm');
  function choose(name) {
    Object.defineProperty(input, 'files', {configurable:true, value:[new dom.window.File(['generated'], name)]});
    input.dispatchEvent(new dom.window.Event('change', {bubbles:true}));
  }
  choose('最先.zip');
  await until(() => finishPreview && input.disabled, 'the pending validation locks the picker');
  choose('另一份.zip');
  assert.equal(requests.filter(call => call.route === 'api/restore/preview').length, 1);
  finishPreview();
  await until(() => !confirm.disabled, 'validated archive can be confirmed');
  assert(get('restore-summary').textContent.includes('最先.zip'));

  confirm.click();
  confirm.click();
  await until(() => finishRestore && input.disabled, 'restoring locks the backup controls');
  assert(get('backup-status').textContent.includes('正在恢复'));
  assert.equal(requests.filter(call => call.route === 'api/restore/confirm').length, 1);
  assert.deepEqual(requests.find(call => call.route === 'api/restore/confirm').payload, {token:'最先.zip'});
  finishRestore(false);
  await until(() => !input.disabled && get('backup-status').textContent.includes('恢复未确认'), 'failed submission returns to safe state');
  assert(confirm.hidden && confirm.disabled);
  assert(get('backup-status').textContent.includes('核对当前资料库'));

  finishPreview = finishRestore = null;
  choose('另一份.zip');
  await until(() => finishPreview && input.disabled, 'fresh validation');
  finishPreview();
  await until(() => !confirm.disabled && get('restore-summary')?.textContent.includes('另一份.zip'), 'only the second token is offered');
  confirm.click();
  await until(() => finishRestore, 'submit second token');
  finishRestore(true);
  await until(() => get('platform-main').getAttribute('data-pane') === 'catalog' &&
    get('backup-status').textContent.includes('已恢复'), 'restored workspace is shown');
  assert.deepEqual(requests.filter(call => call.route === 'api/restore/confirm').map(call => call.payload.token), ['最先.zip','另一份.zip']);
  assert.deepEqual(errors, []);
}

run().catch(error => {process.stderr.write(error.stack + '\n'); process.exitCode = 1;}).finally(() => dom.window.close());
