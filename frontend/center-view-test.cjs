const {loadView, mountTestView} = require('./view-test-dom.cjs');
const {CenterGallery} = loadView('center-gallery.jsx');
const {CenterReview} = loadView('center-review.jsx');
const {CenterNavigationCards} = loadView('center-navigation.jsx');
const {CenterDetailContent, CenterDetailActions} = loadView('center-detail.jsx');

const mountTestGallery = (host, createElement) => mountTestView(host, createElement, (entries, ui) => CenterGallery({entries, ui}));
const mountTestReview = (host, createElement) => mountTestView(host, createElement, CenterReview, true);
function mountTestDetail(content, buttons, createElement) {
  const view = mountTestView(content, createElement, CenterDetailContent);
  const footer = mountTestView(buttons, createElement, CenterDetailActions);
  return {
    render(props) { view.render(props); footer.render(props); },
    dispose() { view.dispose(); footer.dispose(); },
  };
}
const mountTestNavigation = (host, createElement) => mountTestView(host, createElement, CenterNavigationCards);
module.exports = {mountTestGallery, mountTestReview, mountTestDetail, mountTestNavigation};
