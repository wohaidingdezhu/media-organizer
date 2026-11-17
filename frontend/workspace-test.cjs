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

function fixture(html, route) {
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const calls = [];
  const basket = new Set();
  let dupeLoads = 0;
  let completePreview;
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
        switch (path) {
          case 'api/media':
            return reply({available: true, ids_by_path: Object.fromEntries(items.map(item => [item.path, item.id]))});
          case 'api/organization':
            return reply({items, counts: {total: 201, pending: 201, include: 0, hold: 0}, folder_count: 1, review: {blocked: 0}});
          case 'api/photos':
            return reply({items: items.map(item => ({...item, month: '2026/11', preview: 'previews/sample.png'}))});
          case 'api/users':
            return reply({items: [], users: []});
          case 'api/changes':
            return reply({message: '没有前次扫描', items: []});
          case 'api/roots':
            return reply({roots: []});
          case 'api/operations':
            return reply({jobs: [], warnings: [], handled: {copy: [], trash: []}});
          case 'api/organize-settings':
            return reply({destination: '', all_batches: false});
          case 'api/duplicates':
            if (++dupeLoads === 1) return {ok: false, json: async () => ({error: '临时读取失败'})};
            return reply({groups, warnings: [], counts: {groups: 11, files: 221, redundant_logical_bytes: 2520}});
          case 'api/storage':
            return reply({logical_bytes: 2412, duplicate_logical_bytes: 2400, hardlink_references: 0, warnings: [],
              roots: [], kinds: [], photo_months: [], folders: [], largest: items.slice(0, 100), largest_limit: 100});
          case 'api/basket':
            if (payload?.action === 'add') for (const id of payload.ids) basket.add(id);
            return reply({ids: [...basket], limit: 200});
          case 'api/operations/preview':
            return new Promise(resolve => {
              completePreview = () => resolve(reply({mode: 'trash', token: 'test-only', bytes: payload.ids.length * 12,
                items: items.filter(item => payload.ids.includes(item.id)), skipped: []}));
            });
          default:
            throw Error('Unexpected API ' + path);
        }
      };
    },
  });
  return {dom, errors, calls, finish: () => completePreview()};
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

(async () => { await verifyDuplicates(); await verifyWorkspace(); })().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
