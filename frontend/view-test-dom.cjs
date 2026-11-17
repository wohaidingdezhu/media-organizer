// Projects the actual JSX into the controller tests' lightweight DOM. The
// served-page regression uses ReactDOM and real DOM events independently.
const path = require('node:path');
const {buildSync} = require('esbuild');
function loadView(file) {
  const compiled = buildSync({entryPoints: [path.join(__dirname, file)],
    bundle: true, write: false, platform: 'node', external: ['react', 'react-dom', 'react-dom/client']});
  const result = {exports: {}};
  new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(require, result, result.exports);
  return result.exports;
}
function mountTestView(host, createElement, renderView, append) {
  const offset = append ? host.children.length : 0;
  function update(parent, children, start = 0) {
    const previous = parent.children.slice(start);
    const output = [];
    let text = '';
    function append(value) {
      if (value == null || typeof value === 'boolean') return;
      if (typeof value === 'string' || typeof value === 'number') {
        text += value;
        return;
      }
      if (Array.isArray(value)) return value.forEach(append);
      const {type, props, ref} = value;
      if (typeof type === 'symbol') return append(props.children); // React.Fragment
      if (typeof type === 'function') return append(type(props));
      if (typeof type === 'object') return append(type.type(props)); // React.memo
      const candidate = previous[output.length];
      const same = candidate?.tagName === type.toUpperCase() && candidate.reactTestKey === value.key;
      if (!same && candidate) detach(candidate);
      const element = same ? candidate : createElement(type);
      if (!same) {
        element.reactTestKey = value.key;
        element.reactTestRef = ref;
        element.className = '';
        element.isConnected = true;
      }
      for (const [key, prop] of Object.entries(props)) {
        if (key === 'children') continue;
        if (/^on[A-Z]/.test(key)) {
          element[key.toLowerCase()] = event => prop(event || {target: element, currentTarget: element});
        } else if (key === 'title' || key.startsWith('data-') || key.startsWith('aria-')) {
          if (prop != null) element.setAttribute(key, String(prop));
        } else if (key === 'defaultValue') {
          if (!same) element.value = String(prop ?? '');
        } else {
          element[key] = prop;
        }
      }
      update(element, props.children);
      output.push(element);
      if (!same && ref) ref(element);
    }
    append(children);
    for (const stale of previous.slice(output.length)) detach(stale);
    parent.textContent = text;
    parent.children = [...parent.children.slice(0, start), ...output];
  }
  function detach(node) {
    node.isConnected = false;
    if (node.reactTestRef) node.reactTestRef(null);
    for (const child of node.children) detach(child);
  }
  return {
    render(...args) { update(host, renderView(...args), offset); },
    dispose() { for (const node of host.children.slice(offset)) detach(node); host.children = host.children.slice(0, offset); },
  };
}

module.exports = {loadView, mountTestView};
