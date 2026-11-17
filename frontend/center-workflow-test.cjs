const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const source = fs.readFileSync(0, 'utf8');

const id = n => n.toString(16).padStart(64, '0');
const photos = Array.from({length: 105}, (_, n) => ({
  id: id(n + 501), kind: 'photo', title: '照片 ' + (n + 1), favorite: n === 104, bytes: 20,
  mtime: 106 - n, folder: n % 2 ? 'G:\\相册' : '/generated/相册', preview: n ? 'previews/sample.png' : '',
  metadata: {}, personal: {rating: 0, note: ''}, tags: [], local_tags: [],
}));
for (const [n, item] of photos.entries()) {
  item.files = [{id: id(n + 1001), path: item.folder + (n % 2 ? '\\' : '/') + item.title + '.png',
    name: item.title + '.png', folder: item.folder, bytes: 20}];
}
const video = {id: id(749), kind: 'video', title: '同一作品的两个版本', bytes: 450, favorite: false, mtime: 1,
  folder: 'G:\\电影', preview: 'covers/movie.png', metadata: {}, tags: [], local_tags: [],
  files: [{id: id(1249), name: '1.mp4', path: 'G:\\电影\\1.mp4', bytes: 220},
    {id: id(1250), name: '2.mp4', path: 'G:\\电影\\2.mp4', bytes: 230}]};
const items = [...photos, video];
const available = {available: true, ids_by_path: Object.fromEntries(items.flatMap(item => item.files.map(file => [file.path, file.id])))};
const exact = [{sha256: 'a'.repeat(64), files: photos.map(item => ({...item.files[0], kind: 'photo', owner: item.id, preview: item.preview}))}];
const similarId = id(1100);
const similar = [{id: similarId, distance: 3, files: exact[0].files.slice(0, 2)}];
const errors = [], calls = [];
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
let finishCover;
let playerReleases = 0;
let dismissed = false;
const dom = new JSDOM(source, {
  url: 'http://127.0.0.1:43210/generated/center.html#photos', runScripts: 'dangerously', virtualConsole: console,
  beforeParse(window) {
    window.scrollTo = () => {};
    window.HTMLMediaElement.prototype.pause = () => { playerReleases++; };
    window.HTMLMediaElement.prototype.load = () => {};
    window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () {this.open = false; this.dispatchEvent(new window.Event('close'));};
    window.fetch = async (route, options) => {
      const payload = options?.body ? JSON.parse(options.body) : null;
      calls.push({route, payload});
      const reply = data => ({ok: true, json: async () => data});
      switch (route) {
        case 'api/media': return reply(available);
        case 'api/center':
          if (payload?.action === 'review_similarity') dismissed = payload.dismissed;
          return reply({items, folders: {}, directories: {}, media_access: available, recent: [],
            state: {collections: {}}, review: {exact, similar: dismissed ? [] : similar,
              dismissed_similar: dismissed ? similar.map(group => ({...group, decision: 'not_similar'})) : [], warnings: []}});
        case 'api/media/cover':
          return new Promise(resolve => { finishCover = () => resolve(reply({preview: 'previews/repaired.png'})); });
        case 'api/operations/preview':
          return reply({token: 'sample-only', items: payload.ids.map(id => ({path: exact[0].files.find(file => file.id === id).path}))});
        default: throw Error('Unexpected endpoint ' + route);
      }
    };
  },
});
const {window} = dom;
const {document} = window;
const element = id => document.getElementById(id);
const find = selector => document.querySelector(selector);
const button = (scope, label) => [...scope.querySelectorAll('button')].find(value => value.textContent.trim() === label);
const route = view => { window.location.hash = view; };

async function until(condition, message) {
  for (let attempt = 0; attempt < 250; attempt++) {
    if (condition()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

async function run() {
  await until(() => find('.photo-wall .card'), 'photo gallery mounts');
  const firstCard = find('.photo-wall .card');
  const repair = firstCard.querySelector('[data-preview-repair]');
  assert(!repair.hidden && !repair.disabled);
  repair.click();
  repair.click();
  assert.equal(calls.filter(call => call.route === 'api/media/cover').length, 1);
  assert(repair.disabled);
  finishCover();
  await until(() => firstCard.querySelector('img[src="previews/repaired.png"]'), 'repaired preview is React-rendered');
  assert(repair.hidden);
  firstCard.querySelector('.cover').click();
  assert(element('media-detail').open);
  element('detail-close').click();

  element('manage-toggle').click();
  element('select-page').click();
  const firstCount = document.querySelectorAll('.photo-wall input:checked').length;
  assert(firstCount > 1);
  element('center-next').click();
  element('select-page').click();
  assert(Number(element('selected-count').textContent.match(/已选 (\d+)/)[1]) > firstCount);
  element('center-prev').click();
  assert.equal(document.querySelectorAll('.photo-wall input:checked').length, firstCount);

  route('videos');
  await until(() => find('.video-wall .card'), 'video gallery mounts');
  button(find('.video-wall'), '选择播放').click();
  assert.equal(document.querySelectorAll('#detail-content .file').length, 2);
  const file = find('#detail-content .file');
  const draft = find('[aria-label="编辑本地标签"]');
  draft.value = '还没保存的标签';
  button(file, '页内预览').click();
  const player = file.querySelector('video');
  assert(player);
  player.dispatchEvent(new window.Event('error'));
  assert(element('detail-status').textContent.includes('页内预览无法读取'));
  const reads = calls.filter(call => call.route === 'api/center').length;
  element('center-refresh').click();
  await until(() => calls.filter(call => call.route === 'api/center').length > reads, 'detail refresh requested');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(find('#detail-content video'), player);
  assert.equal(draft.value, '还没保存的标签');
  button(file, '关闭预览').click();
  assert(!file.querySelector('video'));
  assert.equal(playerReleases, 1);
  element('detail-close').click();

  route('review');
  await until(() => find('.review-group .review-file'), 'review view mounts');
  const group = find('.review-group');
  const batch = find('.review-batch');
  assert(batch.textContent.includes('已选 104'));
  const keep = group.querySelector('[aria-label^="保留 "]');
  keep.click();
  assert.equal(find('.review-group'), group, 'selected state keeps the current review DOM');
  assert(!group.querySelector('input').checked);
  button(batch, '删除所选…').click();
  await until(() => find('dialog[aria-label="确认移到废纸篓 / 回收站"]')?.open, 'batch uses existing safe confirmation');
  const confirmation = find('dialog[aria-label="确认移到废纸篓 / 回收站"]');
  const preview = calls.find(call => call.route === 'api/operations/preview');
  assert.equal(preview.payload.ids.length, 104);
  assert(!preview.payload.ids.includes(photos[0].files[0].id));
  button(confirmation, '取消').click();
  assert(!calls.some(call => call.route === 'api/operations/start'));
  await until(() => button(element('center-content'), '相似照片 · 1'), 'review restored');
  button(element('center-content'), '相似照片 · 1').click();
  button(find('.review-group'), '确认不相似').click();
  await until(() => button(element('center-content'), '已核对 · 1'), 'saved similarity decision');
  assert(!find('.review-group'));
  button(element('center-content'), '已核对 · 1').click();
  await until(() => find('.review-group'), 'dismissed group can still be reviewed');
  button(find('.review-group'), '恢复到待核对').click();
  await until(() => button(element('center-content'), '相似照片 · 1'), 'restoration saved');
  assert.deepEqual(errors, []);
}

run().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }).finally(() => window.close());
