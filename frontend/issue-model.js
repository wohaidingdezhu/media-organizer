export const categories = {review: '需处理', handled: '已有成功记录', held: '已暂缓'};
export const pageSize = 50;
export const selectionLimit = 200;

export function buildIssues(preview, media) {
  const kinds = new Map(media.map(item => [item.path, item.kind]));
  const issues = new Map((preview.skipped || []).map(item => [item.path, item]));
  for (const item of preview.items) {
    const [kind, reasonGroup] = (item.relative || '').split('/', 2);
    const unknownRegion = kind === '电影' && reasonGroup === '待确认地区';
    const unknownDate = ['照片', '家庭视频'].includes(kind) && reasonGroup === '待确认日期';
    if (unknownRegion || unknownDate) {
      issues.set(item.path, {
        path: item.path,
        reason: '分类依据不明确：' + item.relative,
        reasonGroup,
      });
    }
  }
  return Array.from(issues.values(), item => ({
    ...item,
    kind: kinds.get(item.path) || '附件',
    category: ['handled', 'held'].includes(item.category) ? item.category : 'review',
  }));
}

export function scopedIssues(issues, {status, kind}) {
  return issues.filter(item => (status === 'all' || item.category === status) && (!kind || item.kind === kind));
}

export const issueReason = item => item.reasonGroup ?? item.reason;

export function filterIssues(issues, {reason, search}) {
  const normalize = value => value.normalize('NFC').toLocaleLowerCase();
  const terms = normalize(search).match(/\S+/g) || [];
  return issues.filter(item => {
    if (reason && issueReason(item) !== reason) return false;
    if (!terms.length) return true;
    const text = normalize(item.path + ' ' + item.reason);
    return terms.every(term => text.includes(term));
  });
}

export function issueCsv(issues) {
  const cell = value => {
    const text = String(value);
    const safe = /^\s*[=+@-]/.test(text) ? "'" + text : text;
    return '"' + safe.replaceAll('"', '""') + '"';
  };
  const rows = [['原路径', '原因', '状态'], ...issues.map(item => [
    item.path, item.reason, categories[item.category],
  ])];
  return '\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n');
}
