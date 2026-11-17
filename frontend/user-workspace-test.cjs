const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');

const source = fs.readFileSync(0, 'utf8');
const photo = {id: 'photo:1', title: '旅行.png', kind: '照片', username: '张三',
  user_id: 'u', files: [{path: 'C:\\pics\\旅行.png'}]};
const movie = {id: 'movie:2', title: '电影', kind: '电影', username: '未分配',
  user_id: '', files: [{path: '/films/movie.mp4'}]};
const data = {revision: 'r', users: [{id: 'u', name: '张三', file_count: 1, media_count: 1}], items: [photo, movie]};
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const calls = [];
let failApply = false;
let finishAssign;
const dom = new JSDOM(source, {
  url: 'http://127.0.0.1:43210/generated/users.html', runScripts: 'dangerously', virtualConsole,
  beforeParse(window) {
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    window.fetch = async (route, options) => {
      const reply = (data, ok = true) => ({ok, json: async () => data});
      if (route === 'api/media') return reply({ids_by_path: {}, available: false});
      if (route === 'api/operations') return reply({jobs: [], warnings: []});
      assert.equal(route, 'api/users');
      if (!options?.body) return reply(data);
      const payload = JSON.parse(options.body);
      calls.push(payload);
      if (payload.action === 'assign') return new Promise(resolve => { finishAssign = () => resolve(reply(data)); });
      if (payload.action === 'plan-preview') return reply({revision: 'plan-r', changed_count: 1, targets_digest: 'digest',
        items: [{path: photo.files[0].path, before: 'old', after: '用户/张三/photo.png'}]});
      if (payload.action === 'plan-apply') return failApply ? reply({error: '用户名已变化'}, false) : reply({ids: ['a'.repeat(64)]});
      return reply(data);
    };
  },
});
const {window} = dom;
const {document} = window;
const node = id => document.getElementById(id);

async function until(condition, reason) {
  for (let attempt = 0; attempt < 150; attempt++) {
    if (condition()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(reason);
}
function change(id, value) {
  const element = node(id);
  const input = element.tagName === 'INPUT';
  const prototype = input ? window.HTMLInputElement.prototype : window.HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
  element.dispatchEvent(new window.Event(input ? 'input' : 'change', {bubbles: true}));
}

async function verify() {
  const cards = () => [...document.querySelectorAll('#user-media article')];
  const button = label => [...document.querySelectorAll('#user-list button')].find(button => button.textContent === label);
  await until(() => node('user-create') && !node('user-create').disabled, 'user workspace starts');
  assert(node('user-plan').disabled);
  change('media-search', 'C:\\pics');
  await until(() => cards().length === 1 && cards()[0].textContent.includes(photo.title), 'Windows path search');
  change('media-search', '');
  change('user-filter', 'unassigned');
  change('media-kind', '电影');
  await until(() => cards().length === 1 && cards()[0].textContent.includes(movie.title), 'owner and kind filters');
  node('user-reset').click();
  await until(() => cards().length === 2, 'reset filters');
  button('改名').click();
  await until(() => node('user-editor').open, 'rename opens the dialog');
  change('user-name', '张三改名');
  node('user-editor-save').click();
  await until(() => !node('user-editor').open, 'save closes rename');
  assert.deepEqual(calls[0], {action: 'rename', user_id: 'u', name: '张三改名', revision: 'r'});
  node('user-select-page').click();
  await until(() => node('user-selected').textContent.includes('已勾选 2'), 'batch selection');
  change('assign-user', 'u');
  node('user-assign').click();
  await until(() => finishAssign && node('user-assign').disabled, 'assignment shows the waiting state');
  finishAssign();
  await until(() => !node('user-assign').disabled, 'assign without clearing selection');
  assert.deepEqual(calls[1].ids, [photo.id, movie.id]);
  change('photo-layout', 'year');
  await Promise.resolve();
  node('user-plan').click();
  await until(() => node('user-plan-dialog').open || node('user-error').textContent, 'preview opens the plan');
  assert(node('user-plan-dialog').open, node('user-error').textContent || errors.join(' '));
  assert.equal(calls[2].photo_layout, 'year');
  assert(node('user-plan-items').textContent.includes(photo.files[0].path));
  node('user-plan-save').click();
  node('user-plan-save').click();
  await until(() => !node('user-plan-dialog').open, 'saved plan closes the preview');
  assert.equal(calls.filter(call => call.action === 'plan-apply').length, 1);
  assert.equal(calls[3].plan_revision, 'plan-r');
  assert.equal(calls[3].targets_digest, 'digest');
  assert(node('user-notice').querySelector('a').getAttribute('href').startsWith('organize.html#user-plan='));
  failApply = true;
  node('user-plan').click();
  await until(() => node('user-plan-dialog').open, 'preview again');
  node('user-plan-save').click();
  await until(() => node('user-plan-error').textContent.includes('已变化'), 'stale plan keeps its error');
  assert(node('user-plan-save').disabled);
  assert(node('user-plan-dialog').open);
  node('user-plan-cancel').click();
  await until(() => !node('user-plan-dialog').open, 'cancel on error');
  button('删除').click();
  await until(() => node('user-delete-dialog').open, 'delete username opens confirmation');
  node('user-delete-cancel').click();
  await until(() => !node('user-delete-dialog').open, 'cancel delete');
  assert(!calls.some(call => call.action === 'delete'));
  assert.deepEqual(errors, []);
}

verify().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => window.close());
