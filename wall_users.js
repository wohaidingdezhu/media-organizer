// One shared username toolbar for the photo list/wall and movie poster wall.
function createWallUsers({host, selectedPaths, changed, locked = () => false}) {
  const element = (tag, text) => {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node;};
  const filter = element('select'), assign = element('select'), save = element('button', '保存勾选项归属');
  const refresh = element('button', '刷新用户名'), message = element('span');
  filter.setAttribute('aria-label', '按用户名筛选'); assign.setAttribute('aria-label', '选择归属用户名');
  message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  host.append(filter, assign, save, refresh, message);
  let data = null, busy = false, byPath = new Map(), byUnit = new Map(), chosen = new Set();
  const paths = () => selectedPaths ? selectedPaths() : [...chosen].flatMap(id => byUnit.get(id)?.files.map(file => file.path) || []);
  const ids = () => [...new Set(paths().map(path => byPath.get(path)?.id).filter(Boolean))];
  function choices() {
    for (const [select, initial] of [[filter, [['', '全部用户名'], ['unassigned', '未分配'], ['mixed', '归属不一致']]], [assign, [['', '未分配']]]]) {
      const previous = select.value; select.replaceChildren();
      for (const [value, text] of [...initial, ...(data?.users || []).map(user => [user.id, user.name])]) {
        const option = element('option', text); option.value = value; select.append(option);
      }
      select.value = [...select.options].some(option => option.value === previous) ? previous : '';
    }
  }
  function update() {
    const count = ids().length;
    filter.disabled = !data || busy; assign.disabled = !data || busy || locked(); refresh.disabled = busy;
    save.disabled = !data || busy || locked() || !count || count > 200 || paths().some(path => !byPath.has(path));
    save.textContent = `保存勾选项归属（${count} 项）`;
  }
  async function api(payload) {
    const response = await fetch('api/users', payload ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)} : {});
    const result = await response.json(); if (!response.ok) throw Error(result.error || '用户分类读取失败'); return result;
  }
  function accept(result) {
    data = result; byPath = new Map(); byUnit = new Map();
    for (const unit of data.items) {byUnit.set(unit.id, unit); for (const file of unit.files) byPath.set(file.path, unit);}
    chosen = new Set([...chosen].filter(id => byUnit.has(id))); choices();
  }
  async function load() {
    if (busy) return; busy = true; update();
    try {accept(await api()); message.textContent = '用户名已刷新；电影全部版本和分段一起归类。';}
    catch (error) {data = null; byPath.clear(); byUnit.clear(); choices(); message.textContent = '用户分类不可用：' + error.message;}
    finally {busy = false; update(); changed();}
  }
  save.onclick = async () => {
    if (save.disabled) return; const selection = ids(); busy = true; update(); changed();
    try {accept(await api({action:'assign', ids:selection, user_id:assign.value, revision:data.revision})); message.textContent = `已保存 ${selection.length} 项归属；旧整理计划需要重新预览。`;}
    catch (error) {message.textContent = '归属未保存：' + error.message + '；请刷新用户名后重试。';}
    finally {busy = false; update(); changed();}
  };
  refresh.onclick = load; filter.onchange = () => changed(true);
  if (!selectedPaths) {const clear = element('button', '清空影片勾选'); clear.onclick = () => {chosen.clear(); update(); changed();}; host.append(clear);}
  choices(); update();
  return {
    load, update, busy: () => busy,
    reset: () => {filter.value = '';},
    label: paths => data ? (byPath.get(paths[0])?.username || '归属未提供') : '用户名未加载',
    matches: paths => !filter.value || (!!data && byPath.get(paths[0])?.user_id === (filter.value === 'unassigned' ? '' : filter.value)),
    checked: paths => chosen.has(byPath.get(paths[0])?.id),
    toggle: (paths, value) => {const unit = byPath.get(paths[0]); if (!unit || busy) return; if (value) chosen.add(unit.id); else chosen.delete(unit.id); update(); changed();},
    selectable: paths => !!data && !busy && !locked() && paths.every(path => byPath.has(path))
  };
}
if (typeof module !== 'undefined' && module.exports) module.exports = {createWallUsers};
