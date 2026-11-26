const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');

const {organize, photos} = JSON.parse(fs.readFileSync(0, 'utf8'));
const identifier = value => value.toString(16).padStart(64, '0');
const items = Array.from({length: 201}, (_, index) => ({
  id: identifier(index + 1), name: `样例-${index + 1}.jpg`,
  path: index % 2 ? `G:\\测试\\样例-${index + 1}.jpg` : `/generated/照片/样例-${index + 1}.jpg`,
  bytes: 12, kind: '照片', state: 'pending', selectable: true, mtime: null,
  sidecars: [], duplicate_group: 1, suggested_path: `照片/样例-${index + 1}.jpg`,
}));
const groups = [
  {number: 1, sha256: 'a'.repeat(64), bytes_each: 12, redundant_logical_bytes: 2400, items},
  ...Array.from({length: 10}, (_, index) => {
    const other = items.slice(0, 2).map((item, position) => ({...item, id: identifier(300 + index * 2 + position),
      path: `/synth/video-${index}-${position}.mp4`, name: `Video ${index} ${position}`, kind: '视频'}));
    return {number: index + 2, sha256: identifier(index + 1), bytes_each: 12, redundant_logical_bytes: 12, items: other};
  }),
];
const get = (document, id) => document.getElementById(id);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, reason) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (fn()) return;
    await pause(10);
  }
  assert.fail(reason);
}

function fixture(html, route, initialJobs = []) {
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const calls = [];
  const basket = new Set(), resultRequests = [], copyRequests = [], remainingRequests = [], mediaRequests = [], basketRequests = [], userRequests = [];
  let delayResults = false, delayBasket = false, delayUsers = false, userSnapshot = {items:[],users:[]};
  let dupeLoads = 0;
  let completePreview, rejectPreview, completeStop, completeHistory, failStart;
  let operationJobs = initialJobs, delayHistory = false;
  const history = () => ({jobs:operationJobs, record_count:operationJobs.length,
    warnings:[], handled:{copy:[],trash:[]}});
  const dom = new JSDOM(html, {
    url: 'http://127.0.0.1:43210/generated/' + route, runScripts: 'dangerously', virtualConsole: console,
    beforeParse(window) {
      window.Element.prototype.scrollIntoView = () => {};
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
        this.dispatchEvent(new window.Event('close'));
      };
      window.fetch = async (path, options) => {
        const payload = options?.body ? JSON.parse(options.body) : null;
        calls.push({path, payload});
        const reply = value => ({ok: true, json: async () => value});
        if (/^api\/operations\/[a-f0-9]+\/remaining$/.test(path)) {
          return new Promise((resolve, reject) => remainingRequests.push({
            resolve:advice => resolve(reply(advice)), reject,
          }));
        }
        switch (path) {
          case 'api/media':
            return reply({available: true, ids_by_path: Object.fromEntries(items.map(item => [item.path, item.id]))});
          case 'api/organization':
            return reply({items, counts: {total: 201, pending: 201, include: 0, hold: 0}, folder_count: 1, review: {blocked: 0}});
          case 'api/photos':
            return reply({items: items.map(item => ({...item, month: '2026/11', preview: 'previews/sample.png'}))});
          case 'api/users':
            if(delayUsers)return new Promise((resolve,reject)=>userRequests.push({payload,resolve:value=>resolve(reply(value)),reject}));
            return reply(userSnapshot);
          case 'api/changes':
            return reply({message: '没有前次扫描', items: []});
          case 'api/roots':
            return reply({roots: []});
          case 'api/operations':
            if (delayHistory) return new Promise(resolve => {
              completeHistory = () => {delayHistory = false; resolve(reply(history()));};
            });
            return reply(history());
          case 'api/operations/start':
            return new Promise((resolve, reject) => {failStart = () => reject(Error('模拟提交连接中断'));});
          case 'api/operations/stop':
            delayHistory = true;
            return new Promise((resolve, reject) => {
              completeStop = succeeds => {
                if (!succeeds) return reject(Error('停止请求连接中断'));
                operationJobs = operationJobs.map(job => ({...job, stop_requested:true}));
                resolve(reply({stopped:true}));
              };
            });
          case 'api/media/action':
            return new Promise((resolve, reject) => mediaRequests.push({
              payload, resolve:() => resolve(reply({message:'已请求系统打开'})), reject,
            }));
          case 'api/organized-results/action':
            return new Promise((resolve, reject) => copyRequests.push({
              payload, resolve:() => resolve(reply({opened:true})), reject,
            }));
          case 'api/organized-results':
            if (delayResults) return new Promise((resolve, reject) => resultRequests.push({
              resolve:data => resolve(reply(data)), reject,
            }));
            return reply({items:[], message:'生成样例记录'});
          case 'api/organize-settings':
            return reply({destination: '', all_batches: false});
          case 'api/duplicates':
            if (++dupeLoads === 1) return {ok: false, json: async () => ({error: '临时读取失败'})};
            return reply({groups, warnings: [], counts: {groups: 11, files: 221, redundant_logical_bytes: 2520}});
          case 'api/storage':
            return reply({logical_bytes: 2412, duplicate_logical_bytes: 2400, hardlink_references: 0, warnings: [],
              roots: [], kinds: [], photo_months: [], folders: [], largest: items.slice(0, 100), largest_limit: 100});
          case 'api/basket':
            if (delayBasket) return new Promise((resolve,reject)=>basketRequests.push({payload,resolve:value=>resolve(reply(value)),reject}));
            if (payload?.action === 'add') for (const id of payload.ids) basket.add(id);
            return reply({ids: [...basket], limit: 200});
          case 'api/operations/preview':
          case 'api/operations/quick-preview':
            return new Promise((resolve, reject) => {
              rejectPreview = reject;
              completePreview = value => resolve(reply(value || {mode: 'trash', token: 'test-only', bytes: payload.ids.length * 12,
                items: items.filter(item => payload.ids.includes(item.id)), skipped: []}));
            });
          default:
            throw Error('Unexpected API ' + path);
        }
      };
    },
  });
  return {dom, errors, calls, userRequests, setUsers:value=>{userSnapshot=value;}, deferUsers:()=>{delayUsers=true;}, basketRequests, deferBasket:()=>{delayBasket=true;}, resultRequests, copyRequests, remainingRequests, mediaRequests, failPreview:message=>rejectPreview(Error(message)), deferResults:() => {delayResults = true;}, finish: preview => completePreview(preview),
    failStart:() => failStart(), stop:succeeds => completeStop(succeeds), updateJobs:jobs => {operationJobs = jobs;},
    finishHistory:() => {assert(completeHistory); completeHistory(); completeHistory = null;},
    waitingHistory:() => Boolean(completeHistory)};
}
function change(window, element, value) {
  const input = element.tagName === 'INPUT';
  const prototype = input ? window.HTMLInputElement.prototype : window.HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
  element.dispatchEvent(new window.Event(input ? 'input' : 'change', {bubbles: true}));
}

async function verifyDuplicates() {
  const {dom, errors, calls, finish} = fixture(organize, 'organize.html#duplicates');
  const {document} = dom.window;
  const node = id => get(document, id);
  const textButton = label => [...document.querySelectorAll('#duplicate-groups button')].find(button => button.textContent === label);
  try {
    assert(node('duplicate-panel').open, 'Direct duplicate link opens the React panel immediately');
    await until(() => node('duplicate-retry'), 'retry shown on temporary failure');
    assert.match(node('duplicate-summary').textContent, /临时读取失败/);
    node('duplicate-retry').click();
    await until(() => document.querySelectorAll('.duplicate-group').length === 10, 'first duplicate page');
    node('duplicate-next').click();
    await until(() => document.querySelectorAll('.duplicate-group').length === 1, 'second duplicate page');
    change(dom.window, node('duplicate-search'), 'G:\\测试');
    await until(() => node('duplicate-page').textContent.includes('第 1/1'), 'search resets duplicate page');
    const preview = () => textButton('预览清理其余副本');
    assert(preview().disabled, 'Require an explicit keeper');
    document.querySelector('[aria-label="选作本组保留项 样例-1.jpg"]').click();
    assert(preview().disabled, 'Require expanding all members');
    for (const expected of [100, 150, 200, 201]) {
      [...document.querySelectorAll('#duplicate-groups button')].find(button => button.textContent.startsWith('再显示')).click();
      await until(() => document.querySelectorAll('.duplicate-member').length === expected, 'expand each chunk');
    }
    await until(() => !preview().disabled, 'keeper and all members visible');
    preview().click();
    preview().click();
    const requests = calls.filter(call => call.path === 'api/operations/preview');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].payload.ids.length, 200);
    assert(!requests[0].payload.ids.includes(items[0].id));
    finish();
    await until(() => node('execution').open, 'existing confirmation opens once');
    assert(node('execution-start').disabled, 'the operation still requires confirmation');
    assert(node('execution-items').textContent.includes(items[0].path));
    node('execution-next').click();
    assert(node('execution-items').textContent.includes(items[0].path), 'retained file stays visible on the next confirmation page');
    node('execution-cancel').click();
    assert(!node('execution').open);
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyWorkspace() {
  const {dom, errors, calls, finish} = fixture(photos, 'photos.html');
  const {document} = dom.window;
  const node = id => get(document, id);
  try {
    await until(() => document.querySelectorAll('#photo-wall .photo-card').length === 50 &&
      !document.querySelector('#photo-wall input').disabled, 'photo route shows interactive cards');
    assert.equal(node('rows').children.length, 0, 'the hidden table is not rendered');
    const card = document.querySelector('#photo-wall .photo-card');
    card.querySelector('input').click();
    await until(() => node('selection').textContent.includes('已勾选 1'), 'React selection is reflected in the operation toolbar');
    assert(card.querySelector('input').checked, 'selection remains checked after the toolbar updates');
    card.querySelector('.photo-preview-button').click();
    assert(node('picture-viewer').open, 'preview enters the photo viewer');
    node('picture-viewer').close();
    card.querySelector('[data-trash-id]').click();
    const preview = calls.filter(call => call.path === 'api/operations/preview');
    assert.deepEqual(preview.map(call => call.payload.ids), [[items[0].id]]);
    finish();
    await until(() => node('execution').open, 'single photo goes through confirmation');
    node('execution-cancel').click();
    node('advanced-options').open = true;
    node('storage-panel').open = true;
    await until(() => node('storage-page'), 'storage data loads on opening');
    change(dom.window, node('storage-view'), 'largest');
    await until(() => document.querySelectorAll('.storage-row').length === 20, 'largest list is paginated');
    node('storage-next').click();
    await until(() => node('storage-page').textContent.includes('第 2/5'), 'next storage page');
    const add = document.querySelector('.storage-row [aria-label^="加入候选篮"]');
    add.click();
    await until(() => add.textContent === '已在候选篮', 'adding from the storage list reflects the persisted candidate');
    assert.equal(calls.filter(call => call.path === 'api/basket' && call.payload?.action === 'add').length, 1);
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    change(dom.window, node('view-mode'), 'list');
    await until(() => node('rows').children.length === 50, 'switching to table renders React rows');
    assert.equal(node('photo-wall').children.length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyResultNavigation() {
  const ui = fixture(organize, 'organize.html');
  const {dom, calls, errors, resultRequests} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const copied = items.slice(0, 65).map(item => ({...item, target:'/copies/旅行/' + item.name,
    folder:'/copies/旅行', result_parts:['/copies','旅行'], can_open:true}));
  const result = message => ({items:copied, message});
  try {
    await until(() => !node('organized-refresh').disabled &&
      node('organized-message').textContent.includes('生成样例记录'), 'initial result load');
    ui.deferResults();
    node('organized-refresh').click();
    await until(() => resultRequests.length === 1, 'manual result refresh');
    assert(node('organized-refresh').disabled);
    assert(node('organized-message').textContent.includes('正在读取'));
    resultRequests[0].resolve(result('已读取样例'));
    await until(() => document.querySelectorAll('#organized-items article').length === 50, 'copied photo cards');
    document.querySelector('#organized-folders button').click();
    document.querySelector('#organized-folders button').click();
    node('organized-next').click();
    assert(node('organized-page').textContent.includes('2/2'));
    const ancestor = document.querySelector('#organized-breadcrumbs button');
    ancestor.focus();
    const stale = dom.window.eval('refreshOrganizedResults()');
    const fresh = dom.window.eval('refreshOrganizedResults()');
    await until(() => resultRequests.length === 3, 'overlapping background refreshes');
    resultRequests[2].resolve(result('最新记录'));
    await fresh;
    assert.equal(document.querySelector('#organized-breadcrumbs button'), ancestor);
    assert.equal(document.activeElement, ancestor);
    assert(node('organized-page').textContent.includes('2/2'));
    assert.equal(document.querySelectorAll('#organized-items article').length, 15);
    resultRequests[1].resolve({items:[], message:'旧空结果'});
    await stale;
    assert(node('organized-message').textContent.includes('最新记录'));
    assert.equal(document.querySelectorAll('#organized-items article').length, 15);
    const failed = dom.window.eval('refreshOrganizedResults()');
    resultRequests[3].reject(Error('模拟断连'));
    await failed;
    assert(node('organized-message').textContent.includes('模拟断连'));
    assert(!node('organized-refresh').disabled);
    assert(node('organized-page').textContent.includes('2/2'));
    assert.equal(document.querySelectorAll('#organized-items article').length, 15);
    assert.equal(calls.filter(call => call.path === 'api/organized-results/action').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyOrganizedCopyCards() {
  const ui = fixture(organize, 'organize.html');
  const {dom, errors, resultRequests, copyRequests} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const copied = items.slice(0, 65).map((item, index) => ({...item, id:'copy-' + index,
    target:'/copies/旅行/' + item.name, folder:'/copies/旅行', result_parts:['/copies','旅行'], can_open:true}));
  copied[1] = {...copied[1], kind:'视频', name:'样例-2.mp4', path:'/generated/video.mp4',
    target:'/copies/旅行/样例-2.mp4', bytes:100 * 1024 ** 3};
  const result = {items:copied, message:'生成的照片和视频副本'};
  const card = id => document.querySelector('[data-organized-id="' + id + '"]');
  const action = (id, name) => card(id).querySelector('[data-copy-action="' + name + '"]');
  try {
    await until(() => !node('organized-refresh').disabled &&
      node('organized-message').textContent.includes('生成样例记录'), 'result browser ready');
    ui.deferResults();
    node('organized-refresh').click();
    resultRequests[0].resolve(result);
    await until(() => card('copy-0') && card('copy-1'), 'React copy cards ready');
    assert(card('copy-1').textContent.includes('100.00 GB'));
    const photo = card('copy-0'), image = photo.querySelector('img');
    assert(image && image.getAttribute('src') === 'previews/sample.png');
    image.dispatchEvent(new dom.window.Event('error'));
    await until(() => photo.querySelector('.photo-placeholder'), 'broken preview has a visible fallback');
    assert(photo.textContent.includes('预览读取失败'));
    const play = action('copy-1', 'open');
    play.focus();
    play.click();
    play.click();
    assert(play.disabled && play.textContent.includes('正在打开'));
    assert.equal(copyRequests.length, 1);
    assert.deepEqual(copyRequests[0].payload, {id:'copy-1', action:'open'});
    const refreshing = dom.window.eval('refreshOrganizedResults()');
    resultRequests[1].resolve(result);
    await refreshing;
    assert.equal(action('copy-1', 'open'), play);
    assert.equal(document.activeElement, play);
    assert.equal(card('copy-0'), photo);
    assert(photo.querySelector('.photo-placeholder'));
    node('organized-next').click();
    node('organized-previous').click();
    assert(action('copy-1', 'open').disabled);
    action('copy-1', 'open').click();
    assert.equal(copyRequests.length, 1);
    copyRequests[0].resolve();
    await until(() => !action('copy-1', 'open').disabled, 'video launch unlocks after completion');

    action('copy-0', 'open').click();
    card('copy-0').querySelector('.photo-preview-button').click();
    assert(node('picture-viewer').open);
    const viewerActions = [...document.querySelectorAll('#picture-body [data-copy-id]')];
    assert(viewerActions.length === 2 && viewerActions.every(button => button.disabled));
    viewerActions[0].click();
    viewerActions[1].click();
    assert.equal(copyRequests.length, 2);
    assert.deepEqual(copyRequests[1].payload, {id:'copy-0', action:'open'});
    node('picture-close').click();
    copyRequests[1].reject(Error('模拟副本已移走'));
    await until(() => !action('copy-0', 'open').disabled, 'failed photo launch can be retried');
    assert(node('error').textContent.includes('模拟副本已移走'));

    dom.window.eval('error()');
    action('copy-0', 'open').click();
    node('organized-next').click();
    copyRequests[2].reject(Error('旧页面的错误'));
    await pause(20);
    assert(node('error').hidden, 'off-page action errors do not replace the current feedback');
    assert(node('organized-page').textContent.includes('2/2'));
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyPendingSubmission() {
  const ui = fixture(photos, 'photos.html');
  const {dom, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  try {
    await until(() => document.querySelector('#photo-wall input')?.disabled === false, 'photo selection ready');
    document.querySelector('#photo-wall input').click();
    document.querySelector('#photo-wall [data-trash-id]').click();
    ui.finish();
    await until(() => node('execution').open, 'single-photo confirmation');
    node('execution-confirm').click();
    assert(!node('execution-start').disabled);
    node('execution-start').click();
    node('execution-start').click();
    assert(node('execution-start').disabled);
    assert(node('execution-start').textContent.includes('正在提交'));
    await dom.window.eval('refreshOperations()');
    dom.window.eval('saving=false; syncBrowseControls()');
    node('execution-confirm').dispatchEvent(new dom.window.Event('change'));
    await node('execution-start').onclick();
    assert(node('execution-start').disabled);
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 1);
    assert(node('execution-cancel').disabled);
    for (let index = 0; index < 3; index++) {
      const key = new dom.window.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true, repeat:index > 0});
      document.body.dispatchEvent(key);
      assert(key.defaultPrevented, 'pending submission blocks repeated native Escape');
    }
    const cancel = new dom.window.Event('cancel', {cancelable:true});
    node('execution').dispatchEvent(cancel);
    assert(cancel.defaultPrevented && node('execution').open);
    ui.failStart();
    await until(() => !node('execution').open && !dom.window.eval('saving'), 'uncertain submission releases the dialog');
    assert.equal(dom.window.eval('selected.size'), 1);
    assert(node('error').textContent.includes('启动请求未确认'));
    assert.equal(node('execution-start').textContent, '确认执行');
    await node('execution-start').onclick();
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 1);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyExternalBatchConfirmation() {
  const ui = fixture(photos, 'photos.html');
  const {dom, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const job = {id:'b'.repeat(24), mode:'copy', status:'external_running', total:1,
    items:[], planned_items:[{path:'/generated/other.mp4', target:'/copies/other.mp4', bytes:12}]};
  async function openPreview() {
    document.querySelector('#photo-wall [data-trash-id]').click();
    ui.finish();
    await until(() => node('execution').open, 'React confirmation ready');
  }
  try {
    await until(() => document.querySelector('#photo-wall input')?.disabled === false, 'photo row ready');
    document.querySelector('#photo-wall input').click();
    await openPreview();
    node('execution-confirm').click();
    assert(!node('execution-start').disabled);
    ui.updateJobs([job]);
    await dom.window.eval('refreshOperations()');
    assert(node('execution-start').disabled && node('execution-confirm').disabled);
    assert(node('execution-error').textContent.includes('当前清单暂不能提交'));
    assert(!node('execution-cancel').disabled);
    node('execution-cancel').click();
    assert(!node('execution').open);
    assert.equal(dom.window.eval('selected.size'), 1);

    ui.updateJobs([]);
    await dom.window.eval('refreshOperations()');
    await openPreview();
    assert(!node('execution-confirm').checked);
    ui.updateJobs([job]);
    await dom.window.eval('refreshOperations()');
    const key = new dom.window.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true});
    document.body.dispatchEvent(key);
    assert(!key.defaultPrevented, 'an external batch allows native Escape');
    const escape = new dom.window.Event('cancel', {cancelable:true});
    node('execution').dispatchEvent(escape);
    assert(escape.defaultPrevented && !node('execution').open);
    assert.equal(dom.window.eval('selected.size'), 1);
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    assert.equal(calls.filter(call => call.path === 'api/operations/stop').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyLargeConfirmation() {
  const ui = fixture(organize, 'organize.html');
  const {dom, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const files = Array.from({length:5000}, (_, index) => ({
    path:'/generated/' + index + (index % 2 ? '.mp4' : '.jpg'), target:'/copies/' + index,
    relative:(index % 2 ? '电影/样例/' : '照片/2026/11/') + index,
    role:index % 2 ? '视频' : '照片',
  }));
  let walks = 0;
  Object.defineProperty(files, Symbol.iterator, {value:function* () {
    walks++;
    yield* Array.prototype.values.call(this);
  }});
  files.map = () => {throw Error('a confirmation page traversed every file');};
  const skipped = Array.from({length:151}, (_, index) => ({path:'/skipped/' + index, reason:'模拟磁盘断连'}));
  try {
    await until(() => !node('quick-preview').disabled, 'copy preview can be requested');
    node('quick-preview').click();
    await until(() => calls.some(call => call.path === 'api/operations/quick-preview'), 'quick preview request');
    ui.finish({mode:'copy', quick:true, queue:true, batch_count:25, items:files, skipped,
      groups:[{notes:['请核对样例日期']}], bytes:100 * 1024 ** 3, token:'generated-only'});
    await until(() => node('execution').open, 'large confirmation opens');
    const initialWalks = walks;
    assert(initialWalks > 0);
    assert(node('execution-summary').textContent.includes('100.00 GB'));
    assert(node('execution-items').textContent.includes('自动执行 25 批'));
    assert(node('execution-items').textContent.includes('视频'));
    assert(node('execution-items').textContent.includes('照片'));
    assert(node('execution-start').disabled);
    assert.equal(node('execution-items').querySelectorAll('div.path').length, 200);
    node('execution-items').scrollTop = 300;
    node('execution-next').click();
    assert.equal(node('execution-items').scrollTop, 0);
    assert.equal(walks, initialWalks, 'paging reuses the summary');
    for (let page = 2; page < 52; page++) node('execution-next').click();
    assert(node('execution-page').textContent.includes('52/52'));
    assert(node('execution-items').textContent.includes('/skipped/150'));
    assert(node('execution-items').textContent.includes('模拟磁盘断连'));
    assert.equal(node('execution-items').querySelectorAll('div.path').length, 51);
    assert.equal(walks, initialWalks);
    assert(node('execution-next').disabled);
    node('execution-cancel').click();
    assert(!node('execution').open);
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyOperationProgress() {
  const planned = items.map(item => ({...item, target:item.path + '.copy'}));
  planned[0] = {...planned[0], bytes:100 * 1024 ** 3};
  const job = {id:'a'.repeat(24), mode:'copy', quick:true, queue:true, completed_batches:0,
    batch_count:2, total:201, status:'running', planned_items:planned,
    items:[{...planned[0], status:'processing', phase:'copying', processed_bytes:1024}]};
  const ui = fixture(organize, 'organize.html', [job]);
  const {dom, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const button = label => [...document.querySelectorAll('#operations button')].find(b => b.textContent === label);
  const refresh = async () => {
    const count = calls.filter(call => call.path === 'api/operations').length;
    node('refresh-operations').click();
    await until(() => calls.filter(call => call.path === 'api/operations').length > count, 'operation history request');
    await pause(20);
  };
  try {
    await until(() => button('安全停止整个整理任务'), 'operation card ready');
    assert(node('operations').textContent.includes('100.00 GB'), 'large-file size is retained');
    assert(node('operations').textContent.includes('正在复制'), 'active large-file transfer is visible');
    const rows = [...document.querySelectorAll('#operations pre')];
    const first = rows.find(row => row.textContent.includes(planned[1].path));
    const selection = dom.window.getSelection(), range = document.createRange();
    range.selectNodeContents(first);
    selection.addRange(range);
    const selectedText = selection.toString();
    await refresh();
    assert(first.isConnected, 'poll keeps the file row attached');
    assert.equal(selection.toString(), selectedText, 'poll retains selected path text');
    assert.equal(document.querySelectorAll('#operations pre').length, 51);
    const search = document.querySelector('#operations input[type="search"]');
    const filter = document.querySelector('#operations select');
    search.focus();
    change(dom.window, search, '样例-150');
    await until(() => node('operations').textContent.includes('明细 1/201 项'), 'off-page file search');
    await refresh();
    assert.equal(document.querySelector('#operations input[type="search"]'), search);
    assert.equal(document.activeElement, search);
    assert.equal(search.value, '样例-150');
    change(dom.window, search, '');
    change(dom.window, filter, 'pending');
    await until(() => node('operations').textContent.includes('明细 200/201 项'), 'pending filter');
    await refresh();
    assert.equal(filter.value, 'pending');
    assert(node('operations').textContent.includes('明细 200/201 项'));
    change(dom.window, filter, '');
    await until(() => node('operations').textContent.includes('明细 201/201 项'), 'all-file filter');
    button('下一页明细').click();
    assert(node('operations').textContent.includes('第 2/5 页'));
    await refresh();
    assert(node('operations').textContent.includes('第 2/5 页'));
    assert.equal(document.querySelectorAll('#operations pre').length, 51);

    const stop = button('安全停止整个整理任务');
    stop.click();
    stop.click();
    assert.equal(stop.textContent, '正在提交停止请求…');
    assert(stop.disabled);
    assert.equal(calls.filter(call => call.path === 'api/operations/stop').length, 1);
    ui.updateJobs([job]);
    dom.window.eval('renderOperationCards(' + JSON.stringify({jobs:[job],warnings:[]}) + ')');
    assert(stop.disabled, 'poll cannot unlock an in-flight stop');
    ui.stop(false);
    await until(ui.waitingHistory, 'failed stop reconciles history');
    assert(stop.disabled);
    ui.finishHistory();
    await until(() => !stop.disabled, 'failed stop can be retried after reconciliation');
    assert(node('error').textContent.includes('停止请求连接中断'));

    stop.click();
    ui.stop(true);
    await until(ui.waitingHistory, 'accepted stop reconciles history');
    assert(stop.disabled);
    ui.finishHistory();
    await until(() => stop.textContent === '正在安全停止…', 'server acknowledges stop');
    assert(stop.disabled);
    assert.equal(calls.filter(call => call.path === 'api/operations/stop').length, 2);
    ui.updateJobs([{...job, status:'cancelled', stop_requested:true}]);
    await refresh();
    assert(stop.hidden);
    assert(node('operations').textContent.includes('需核对 1'));
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    ui.updateJobs([]);
    await refresh();
    assert.equal(document.querySelectorAll('#operations pre').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyWallUserControls() {
  const ui=fixture(photos,'photos.html');
  const {dom,errors,userRequests}=ui,{document}=dom.window;
  const host=()=>get(document,'wall-user-controls');
  const select=label=>host().querySelector(`[aria-label="${label}"]`);
  const button=prefix=>[...host().querySelectorAll('button')].find(node=>node.textContent.startsWith(prefix));
  const snapshot={revision:'r',users:[{id:'u',name:'张三'}],items:items.map((item,index)=>({
    id:'photo:'+index,user_id:'',username:'未分配',files:[{path:item.path}]}))};
  try {
    await until(()=>button('刷新用户名')&&!button('刷新用户名').disabled,'username toolbar ready');
    ui.setUsers(snapshot);await dom.window.eval('wallUsers.load()');
    dom.window.eval('selected=new Set(data.items.slice(0,200).map(item=>item.id));render()');
    const assign=select('选择归属用户名'),filter=select('按用户名筛选');
    change(dom.window,assign,'u');filter.focus();dom.window.eval('wallUsers.update()');
    assert.equal(document.activeElement,filter);assert.equal(assign.value,'u');
    ui.deferUsers();const save=button('保存勾选项归属');save.click();save.click();
    assert.equal(userRequests.length,1);assert.equal(userRequests[0].payload.ids.length,200);
    assert.equal(userRequests[0].payload.user_id,'u');assert.equal(userRequests[0].payload.revision,'r');
    assert(save.disabled&&save.textContent==='正在保存归属…');
    dom.window.eval('wallUsers.update()');assert.equal(button('正在保存归属'),save);
    userRequests[0].reject(Error('归属版本冲突'));
    await until(()=>!save.disabled,'failed save allows recovery');
    assert(host().textContent.includes('归属版本冲突'));assert.equal(dom.window.eval('selected.size'),200);
    button('刷新用户名').click();button('正在读取用户名').click();assert.equal(userRequests.length,2);
    userRequests[1].resolve({...snapshot,revision:'r2'});
    await until(()=>!save.disabled,'refresh recovers current revision');assert.equal(assign.value,'u');
    save.click();assert.equal(userRequests[2].payload.revision,'r2');
    userRequests[2].resolve({...snapshot,revision:'r3',items:snapshot.items.map((unit,index)=>index<200?{...unit,user_id:'u',username:'张三'}:unit)});
    await until(()=>!save.disabled,'assignment completes');
    assert(host().textContent.includes('已保存 200 项归属'));
    dom.window.eval('selected.add(data.items[200].id);render()');assert(save.disabled&&save.textContent.includes('201'));
    dom.window.eval('selected=new Set([data.items[0].id]);render()');
    button('刷新用户名').click();
    userRequests[3].resolve({...snapshot,revision:'r4',users:[]});
    await until(()=>button('刷新用户名')&&!button('刷新用户名').disabled,'removed user refresh');
    assert.equal(assign.value,'','removed owner cannot remain selected');
    assert.deepEqual(errors,[]);
  } finally {dom.window.close();}
}

async function verifyBasketControls() {
  const ui = fixture(photos, 'photos.html');
  const {dom, basketRequests, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document,id);
  try {
    await until(()=>node('basket-refresh')&&!node('basket-refresh').disabled,'React basket ready');
    dom.window.eval('data.items=data.items.map((item,index)=>({...item,kind:index%2?"视频":"照片"}));selected=new Set(data.items.slice(0,200).map(item=>item.id));render()');
    ui.deferBasket();
    const add=node('basket-add');add.click();add.click();
    assert.equal(basketRequests.length,1);
    assert.equal(basketRequests[0].payload.ids.length,200);
    assert(add.disabled&&add.textContent==='正在加入…');
    assert(node('basket-summary').textContent.includes('正在保存'));
    dom.window.eval('updateBasketControls()');
    assert.equal(node('basket-add'),add);
    assert(add.disabled);
    basketRequests[0].reject(Error('模拟候选保存失败'));
    await until(()=>!add.disabled,'basket failure allows retry');
    assert.equal(dom.window.eval('selected.size'),200);
    assert(node('basket-summary').textContent.includes('模拟候选保存失败'));
    add.click();basketRequests[1].resolve({ids:items.slice(0,200).map(item=>item.id),limit:200});
    await until(()=>!node('basket-clear').disabled,'saved candidates ready');
    node('basket-refresh').click();node('basket-refresh').click();
    assert.equal(basketRequests.length,3);
    assert(node('basket-select').disabled&&node('basket-view').disabled);
    dom.window.eval('showBasket(true)');
    assert.equal(dom.window.eval('selected.size'),200,'pending refresh preserves current picks');
    basketRequests[2].resolve({ids:items.slice(0,200).map(item=>item.id),limit:200});
    await until(()=>!node('basket-select').disabled,'refresh completes');
    const enterFolder=()=>document.querySelector('#folder-browser [aria-label^="进入文件夹 "]').click();
    const ownerFilter=()=>document.querySelector('[aria-label="按用户名筛选"]');
    enterFolder();
    change(dom.window,ownerFilter(),'unassigned');
    assert.equal(dom.window.eval('visible.length'),0,'owner filter actually restricts the view');
    node('basket-view').click();
    assert.equal(dom.window.eval('visible.length'),200,'basket view clears stale folder and owner filters');
    assert.equal(ownerFilter().value,'');
    enterFolder();change(dom.window,ownerFilter(),'unassigned');
    dom.window.eval('mediaIds[data.items[0].path]="changed-identity"');
    node('basket-select').click();
    assert.equal(dom.window.eval('visible.length'),200,'all candidates remain visible for review');
    assert.equal(dom.window.eval('selected.size'),199);
    assert(!dom.window.eval('selected.has(data.items[0].id)'));
    enterFolder();change(dom.window,ownerFilter(),'unassigned');
    dom.window.eval('storageFilter("kind","视频")');
    assert.equal(dom.window.eval('visible.length'),100,'storage jump clears stale folder and owner filters');
    node('basket-select').click();
    node('basket-clear').click();node('basket-clear').click();
    assert.equal(basketRequests.length,4);
    assert.equal(basketRequests[3].payload.action,'clear');
    basketRequests[3].resolve({ids:[],limit:200});
    await until(()=>node('basket-summary').textContent.includes('0/200'),'candidate clear finishes');
    assert.equal(dom.window.eval('selected.size'),0);
    assert.equal(calls.filter(call=>call.path.startsWith('api/operations/')&&['preview','start'].some(name=>call.path.endsWith(name))).length,0);
    assert.deepEqual(errors,[]);
  } finally {dom.window.close();}
}

async function verifyPhotoViewerActions() {
  const ui = fixture(photos, 'photos.html');
  const {dom, mediaRequests, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const open = () => [...node('picture-body').querySelectorAll('button')].find(button => !button.hasAttribute('data-trash-id'));
  const trash = () => node('picture-body').querySelector('[data-trash-id]');
  try {
    await until(() => document.querySelector('#photo-wall .photo-card .photo-preview-button'), 'photo cards ready');
    const card = document.querySelector('#photo-wall .photo-card');
    card.querySelector('input').click();
    card.querySelector('.photo-preview-button').click();
    const image = node('picture-body').querySelector('img'), first = open();
    first.click(); first.click();
    assert.equal(mediaRequests.length, 1);
    assert(first.disabled && first.textContent === '正在打开…');
    const next = node('picture-next');
    next.focus();
    dom.window.eval('syncBrowseControls()');
    assert.equal(document.activeElement, next, 'refresh retains keyboard navigation focus');
    assert.equal(open(), first, 'refresh retains the original-file action');
    assert.equal(node('picture-body').querySelector('img'), image, 'refresh retains the loaded photo');
    assert(open().disabled);
    node('picture-next').click();
    assert(node('picture-body').textContent.includes(items[1].path));
    mediaRequests[0].reject(Error('第一张照片已移走'));
    await pause(20);
    assert(node('picture-error').hidden, 'late original-file error cannot pollute another photo');
    node('picture-previous').click();
    open().click();
    assert.equal(mediaRequests.length, 2);
    node('picture-close').click();
    card.querySelector('.photo-preview-button').click();
    assert(open().disabled, 'reopening cannot duplicate the same pending request');
    mediaRequests[1].reject(Error('关闭前的打开错误'));
    await until(() => !open().disabled, 'failed original open allows retry');
    assert(node('picture-error').hidden, 'closed session error cannot pollute reopened photo');
    open().click();
    mediaRequests[2].resolve();
    await until(() => !open().disabled, 'original open completes');
    assert.deepEqual(mediaRequests[2].payload, {id:items[0].id, action:'open'});

    trash().click(); trash().click();
    assert.equal(calls.filter(call => call.path === 'api/operations/preview').length, 1);
    ui.failPreview('至少保留一份');
    await until(() => !node('picture-error').hidden, 'delete refusal is visible inside the viewer');
    assert(node('picture-error').textContent.includes('至少保留一份'));
    assert.deepEqual([...dom.window.eval('selected')], [items[0].id]);
    trash().click();
    node('picture-next').click();
    ui.failPreview('上一张照片的核对失败');
    await pause(20);
    assert(node('picture-error').hidden, 'late delete refusal cannot pollute another photo');
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyPhotoViewerFallback() {
  const ui = fixture(photos, 'photos.html');
  const {dom, calls, errors} = ui, {document} = dom.window;
  const node = id => get(document, id);
  const visibleFallbacks = () => [...node('picture-body').querySelectorAll('.photo-placeholder')].filter(element => !element.hidden);
  try {
    await until(() => document.querySelector('#photo-wall .photo-card img'), 'photo previews ready');
    dom.window.eval('viewPictures(data.items.slice(0,2))');
    assert(node('picture-viewer').open);
    const images = [...node('picture-body').querySelectorAll('img')];
    assert.equal(images.length, 2);
    const requestCount = calls.length;
    images[0].dispatchEvent(new dom.window.Event('error'));
    images[0].dispatchEvent(new dom.window.Event('error'));
    assert(images[0].hidden);
    assert(!images[1].hidden, 'one failed preview does not hide the other photo');
    assert.equal(visibleFallbacks().length, 1, 'repeated errors create one fallback');
    assert(visibleFallbacks()[0].textContent.includes('可打开原图'));
    assert.equal(images[0].previousElementSibling, visibleFallbacks()[0], 'fallback stays in the preview position');
    images[1].dispatchEvent(new dom.window.Event('error'));
    assert.equal(visibleFallbacks().length, 2);
    assert.equal([...node('picture-body').querySelectorAll('button')].filter(button => button.textContent === '打开原图' && !button.disabled).length, 2);
    assert.equal(calls.length, requestCount, 'preview failure does not open files or start tasks');
    node('picture-close').click();
    document.querySelector('#photo-wall .photo-card img').click();
    assert(!node('picture-body').querySelector('img').hidden, 'reopening retries the thumbnail');
    assert.equal(visibleFallbacks().length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

async function verifyOperationRecovery() {
  const planned = items.map(item => ({...item, target:item.path + '.copy'}));
  const job = {id:'c'.repeat(24), mode:'copy', total:201, status:'cancelled', planned_items:planned,
    items:[{...planned[0], status:'cancelled'}]};
  const ui = fixture(organize, 'organize.html', [job]);
  const {dom, calls, errors, remainingRequests} = ui, {document} = dom.window;
  const button = label => [...document.querySelectorAll('#operations button')].find(b => b.textContent === label);
  const recoveryButton = () => button('核对此批次未处理项');
  const refresh = async jobs => {
    if (jobs) ui.updateJobs(jobs);
    const count = calls.filter(call => call.path === 'api/operations').length;
    get(document, 'refresh-operations').click();
    await until(() => calls.filter(call => call.path === 'api/operations').length > count, 'recovery history refresh');
    await pause(20);
  };
  const selectedIds = () => JSON.stringify(dom.window.eval('[...selected]'));
  try {
    await until(() => recoveryButton() && !recoveryButton().disabled, 'recoverable stopped batch');
    const recover = recoveryButton();
    recover.click(); recover.click();
    assert.equal(remainingRequests.length, 1, 'repeat recovery sends one request');
    assert.equal(recover.textContent, '正在核对未处理项…');
    assert(document.querySelector('#operations [role="status"]').textContent.includes('请稍候'));
    await refresh();
    assert(recover.disabled, 'poll does not unlock pending recovery');
    remainingRequests[0].reject(Error('模拟核对连接中断'));
    await until(() => !recover.disabled, 'failed recovery allows retry');
    assert(document.querySelector('#operations [role="status"]').textContent.includes('模拟核对连接中断'));

    recover.click();
    const skipped = Array.from({length:201}, (_, index) => ({path:'/generated/跳过-' + index + '.mp4', reason:'请核对磁盘状态'}));
    const advice = {ids:items.slice(1,3).map(item => item.id), skipped, message:'已完成项保留。'};
    remainingRequests[1].resolve(advice);
    await until(() => !recover.disabled, 'recovery result returns');
    assert.equal(selectedIds(), JSON.stringify(advice.ids));
    const result = document.querySelector('#operations [role="status"]');
    assert(result.textContent.includes('已勾选 2 项，跳过 201 项'));
    assert.equal(result.querySelectorAll('p.path').length, 50, 'skip reasons render one page');
    const details = result.querySelector('details'); details.open = true;
    for (let page = 1; page < 5; page++) button('下一页跳过原因').click();
    assert(result.textContent.includes('/generated/跳过-200.mp4'));
    assert.equal(result.querySelectorAll('p.path').length, 1);
    assert(button('下一页跳过原因').disabled);
    await refresh();
    assert.equal(result.querySelector('details'), details);
    assert(details.open, 'refresh retains expanded reasons');
    assert(result.textContent.includes('第 5/5 页'));

    const before = selectedIds();
    recover.click();
    await refresh([]);
    remainingRequests[2].resolve({...advice, ids:[items[10].id]});
    await pause(20);
    assert.equal(selectedIds(), before, 'removed history response cannot replace selection');
    assert.equal(document.querySelectorAll('#operations .operation').length, 0);

    await refresh([job]);
    recoveryButton().click();
    const other = {...job, id:'d'.repeat(24), status:'running'};
    await refresh([job, other]);
    remainingRequests[3].resolve({...advice, ids:[items[11].id]});
    await pause(20);
    assert.equal(selectedIds(), before, 'new active operation blocks stale recovery selection');
    assert.equal(calls.filter(call => call.path === 'api/operations/start').length, 0);
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}

(async () => { await verifyDuplicates(); await verifyWorkspace(); await verifyOperationProgress(); await verifyOperationRecovery(); await verifyPhotoViewerFallback(); await verifyPhotoViewerActions(); await verifyBasketControls(); await verifyWallUserControls(); await verifyLargeConfirmation(); await verifyExternalBatchConfirmation(); await verifyPendingSubmission(); await verifyResultNavigation(); await verifyOrganizedCopyCards(); })().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
