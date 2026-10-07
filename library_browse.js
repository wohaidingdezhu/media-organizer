// Shared, read-only browsing of the report snapshot. Never access source media.
function movieFileFolder(file) {
  if (file.source_folder) return file.source_folder;
  // Older reports only have paths. Recognize both separators without truncating
  // a Windows drive root or a POSIX root, even when viewing a copied report.
  const path = String(file.path || ''), end = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  if (end < 0) return '';
  return path.slice(0, end === 0 || (end === 2 && path[1] === ':') ? end + 1 : end);
}

function movieRating(group) {
  const rating = (group.personal || {}).rating;
  return Number.isInteger(rating) && rating >= 1 && rating <= 5 ? rating : 0;
}

function movieSizeLabel(bytes) {
  for (const [unit, label] of [[1099511627776, 'TB'], [1073741824, 'GB'], [1048576, 'MB'], [1024, 'KB']])
    if (bytes >= unit) return `${(bytes / unit).toFixed(1)} ${label}`;
  return `${bytes} B`;
}

function movieFileTime(file) {
  if (typeof file.mtime === 'number' && Number.isFinite(file.mtime)) return file.mtime;
  // Legacy timestamps are local wall-clock strings, not dates suitable for
  // implementation-dependent Date.parse. Validate components before sorting.
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(file.modified_at || '');
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, 0, 0);
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day || date.getUTCHours() !== hour || date.getUTCMinutes() !== minute) return null;
  return date.getTime() / 1000;
}

function movieBrowseFacts(group) {
  const files = group.files || [], times = files.map(movieFileTime).filter(time => time !== null);
  return {
    bytes: files.reduce((sum, file) => sum + (Number.isSafeInteger(file.bytes) && file.bytes >= 0 ? file.bytes : 0), 0),
    latest: times.length ? times.reduce((latest, time) => Math.max(latest, time)) : null,
    folders: [...new Set(files.map(movieFileFolder).filter(Boolean))],
    rating: movieRating(group)
  };
}

function movieFolderChoices(groups) {
  const counts = new Map();
  for (const group of groups) for (const folder of movieBrowseFacts(group).folders)
    counts.set(folder, (counts.get(folder) || 0) + 1);
  return [...counts].sort(([left], [right]) => left.localeCompare(right, 'zh-CN', {numeric: true}));
}

function browseMovies(groups, options = {}) {
  const query = String(options.query || '').trim().toLocaleLowerCase();
  const facts = new Map(groups.map(group => [group, movieBrowseFacts(group)]));
  const visible = groups.filter(group => {
    const tags = group.tags || [], files = group.files || [], details = facts.get(group);
    if (options.folder && !details.folders.includes(options.folder)) return false;
    if (options.rating === 'unrated' && details.rating) return false;
    if (/^[1-5]$/.test(options.rating || '') && details.rating < Number(options.rating)) return false;
    if (options.tag && !tags.includes(options.tag)) return false;
    switch (options.filter) {
      case 'watched': if (!tags.includes('已观看')) return false; break;
      case 'unwatched': if (tags.includes('已观看')) return false; break;
      case 'favorite': if (!tags.includes('收藏')) return false; break;
      case 'review': if (!group.needs_review) return false; break;
      case 'duplicates': if (!files.some(file => file.duplicate_group)) return false; break;
      case 'sidecars': if (!group.has_sidecars) return false; break;
      case 'posters': if (!group.poster) return false; break;
      case 'missing-posters': if (group.poster) return false; break;
    }
    return !query || [group.title, ...Object.values(group.metadata || {}).flat(), (group.personal || {}).note, ...tags,
      ...files.flatMap(file => [file.path, file.suggested_path, ...(file.sidecars || [])])]
      .some(value => String(value || '').toLocaleLowerCase().includes(query));
  });
  const titleOrder = (left, right) => String(left.title || '').localeCompare(String(right.title || ''), 'zh-CN', {numeric: true}) ||
    String(left.tag_key || (left.files || [])[0]?.path || '').localeCompare(String(right.tag_key || (right.files || [])[0]?.path || ''), 'zh-CN');
  return visible.sort((left, right) => {
    const a = facts.get(left), b = facts.get(right);
    if (options.sort === 'size') return b.bytes - a.bytes || titleOrder(left, right);
    if (options.sort === 'rating') return b.rating - a.rating || titleOrder(left, right);
    if (options.sort === 'newest' || options.sort === 'oldest') {
      if (a.latest === null || b.latest === null) return (a.latest === null) - (b.latest === null) || titleOrder(left, right);
      return (options.sort === 'newest' ? b.latest - a.latest : a.latest - b.latest) || titleOrder(left, right);
    }
    return titleOrder(left, right);
  });
}

if (typeof module !== 'undefined' && module.exports)
  module.exports = {browseMovies, movieFolderChoices, movieBrowseFacts, movieFileFolder, movieSizeLabel};
