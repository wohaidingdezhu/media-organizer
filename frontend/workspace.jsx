import {mountCenterDetail} from './center-detail.jsx';
import {mountCenterReview} from './center-review.jsx';
import {mountCenterGallery} from './center-gallery.jsx';
import './issue-list.jsx';
import {createManagementFolderBrowser} from './folder-browser.jsx';
import {mountDuplicateReview} from './duplicate-review.jsx';

import {mountStoragePanel} from './storage-panel.jsx';

import {mountWorkspaceMedia} from './workspace-media.jsx';

import {mountUserWorkspace} from './user-workspace.jsx';

globalThis.mountCenterDetail = mountCenterDetail;
globalThis.mountCenterReview = mountCenterReview;
globalThis.mountCenterGallery = mountCenterGallery;
globalThis.createManagementFolderBrowser = createManagementFolderBrowser;
globalThis.mountUserWorkspace = mountUserWorkspace;
globalThis.mountWorkspaceMedia = mountWorkspaceMedia;
globalThis.mountStoragePanel = mountStoragePanel;
globalThis.mountDuplicateReview = mountDuplicateReview;
