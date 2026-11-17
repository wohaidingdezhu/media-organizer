const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const paths = [String.raw`\\nas\share\图片`, '/generated/照片'];
const item = {id:'a'.repeat(64), report_id:'scan-sample', name:'样例.jpg', path:paths[1] + '/样例.jpg',
  kind:'照片', state:'new', created_at:'2026-10-01'};
let finishScan;
const requests = [], errors = [];
const ready = {running:false, status:'准备就绪', folders:paths, manual_scan_folders:[paths[0]],
  latest_report:'', monitor:{enabled:false}, logs:[]};
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(0, 'utf8'), {
  url: 'http://127.0.0.1:43210/generated/', runScripts: 'dangerously', virtualConsole: console,
  beforeParse(window) {
    window.scrollTo = () => {};
    window.fetch = async (route, options) => {
      const payload = options?.body ? JSON.parse(options.body) : null;
      requests.push({route,payload});
      const reply = data => ({ok:true,json:async()=>data});
      switch (route) {
        case 'api/status': return reply(ready);
        case 'api/reports': return reply({reports:[]});
        case 'api/scan/start': return new Promise(resolve => {finishScan = () => resolve(reply(ready));});
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
  await until(() => !picks()[1].disabled, 'scope unlocks');
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
  assert.deepEqual(errors, []);
}

run().catch(error => {process.stderr.write(error.stack + '\n'); process.exitCode = 1;}).finally(() => dom.window.close());
