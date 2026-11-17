import './issue-list.jsx';
import {mountDuplicateReview} from './duplicate-review.jsx';

import {mountStoragePanel} from './storage-panel.jsx';

import {mountWorkspaceMedia} from './workspace-media.jsx';

import {mountUserWorkspace} from './user-workspace.jsx';

globalThis.mountUserWorkspace = mountUserWorkspace;
globalThis.mountWorkspaceMedia = mountWorkspaceMedia;
globalThis.mountStoragePanel = mountStoragePanel;
globalThis.mountDuplicateReview = mountDuplicateReview;
