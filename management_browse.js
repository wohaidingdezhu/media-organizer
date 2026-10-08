// Report-only sorting shared by the file list and photo wall.
function sortManagementItems(items, order) {
  const nameOrder = (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN', {numeric: true}) ||
    String(a.path || '').localeCompare(String(b.path || ''), 'zh-CN', {numeric: true});
  const bytes = item => Number.isSafeInteger(item.bytes) && item.bytes >= 0 ? item.bytes : 0;
  const time = item => typeof item.mtime === 'number' && Number.isFinite(item.mtime) ? item.mtime : null;
  return [...items].sort((a, b) => {
    if (order === 'name') return nameOrder(a, b);
    if (order === 'size') return bytes(b) - bytes(a) || nameOrder(a, b);
    if (order === 'newest' || order === 'oldest') {
      const left = time(a), right = time(b);
      if (left === null || right === null) return (left === null) - (right === null) || nameOrder(a, b);
      return (order === 'newest' ? right - left : left - right) || nameOrder(a, b);
    }
    return 0;
  });
}

function photoBrowseSequence(visible, start) {
  const photos = visible.filter(item => item.kind === '照片');
  return photos.some(item => item.id === start.id) ? photos : [start];
}

if (typeof module !== 'undefined' && module.exports)
  module.exports = {sortManagementItems, photoBrowseSequence};
