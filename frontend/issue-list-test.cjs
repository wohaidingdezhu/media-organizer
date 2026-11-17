const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

function createIssueListTest(onCsv) {
  const dom = new JSDOM('<div id="quick-issue-panel"></div>', {runScripts: 'outside-only'});
  const {window} = dom;
  window.Blob = Blob;
  window.URL.createObjectURL = blob => {
    onCsv(blob);
    return 'blob:generated';
  };
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = () => {};
  window.eval(fs.readFileSync(path.join(__dirname, '../issue_list_bundle.js'), 'utf8'));
  return {
    find: id => window.document.getElementById(id),
    mount: window.mountIssueList,
    async click(element) {
      element.click();
      await Promise.resolve();
    },
    async change(element, value) {
      const input = element.tagName === 'INPUT';
      const prototype = input ? window.HTMLInputElement.prototype : window.HTMLSelectElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
      element.dispatchEvent(new window.Event(input ? 'input' : 'change', {bubbles: true}));
      await Promise.resolve();
    },
  };
}

module.exports = {createIssueListTest};
