const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(0, 'utf8'), {
  url: 'file:///generated/report.html', runScripts: 'dangerously', virtualConsole,
  beforeParse(window) { window.fetch = () => { throw Error('离线分类建议不需要网络'); }; },
});
const {window} = dom;
const find = id => window.document.getElementById(id);
const sleep = () => new Promise(resolve => setTimeout(resolve, 10));
async function until(condition, label) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (condition()) return;
    await sleep();
  }
  assert.fail(label);
}
function change(id, value) {
  const element = find(id);
  const input = element.tagName === 'INPUT';
  const prototype = input ? window.HTMLInputElement.prototype : window.HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
  element.dispatchEvent(new window.Event(input ? 'input' : 'change', {bubbles: true}));
}

async function run() {
  await until(() => find('plan-rows'), 'report mounts while offline');
  assert.equal(find('plan-rows').children.length, 100);
  assert(find('plan-count').textContent.includes('第 1/11'));
  find('next').click();
  await until(() => find('plan-count').textContent.includes('第 2/11'), 'next page');
  assert(find('plan-rows').children[0].textContent.includes('photo-100.jpg'));
  change('search', '最后一条');
  await until(() => find('plan-rows').textContent.includes('视频/last.mp4'), 'search beyond initial page');
  assert(find('plan-count').textContent.includes('第 1/1'));
  assert(find('plan-rows').textContent.includes('</script><script>alert(1)</script>'));
  assert(!find('plan-rows').querySelector('script'));
  assert(find('next').disabled);
  change('kind-filter', '照片');
  await until(() => find('plan-rows').textContent.includes('没有匹配'), 'filtered empty state');
  assert.deepEqual(errors, []);
}

run().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }).finally(() => window.close());
