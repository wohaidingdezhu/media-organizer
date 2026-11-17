// Integration with the existing confirmation workflow while it is migrated.
const duplicatePanel = {current: null};
let renderDuplicateReview;
function renderDuplicateGroups() {
  renderDuplicateReview?.({busy: saving || operating, available: mediaAvailable, ids: mediaIds, handled: handledTrash, photos});
}
function openDuplicatePanel() {
  duplicatePanel.current?.();
}
renderDuplicateReview = mountDuplicateReview($('duplicate-section'), {
  openPanel: duplicatePanel,
  load: () => api('api/duplicates'),
  formatSize: fileSize,
  compare: items => viewPictures(items),
  openMedia: (item, action) => openMedia(item, action),
  trash: item => previewTrashItem(item),
  async preview(kept, copies) {
    if (saving || operating) return;
    const ids = copies.map(item => item.id);
    selected = new Set(ids);
    await previewOperation('trash', false, false, false, ids, kept.path);
    if (!$('execution').open && !$('error').hidden) throw Error($('error').textContent);
  },
});
renderDuplicateGroups();
$('duplicate-open').onclick = openDuplicatePanel;
