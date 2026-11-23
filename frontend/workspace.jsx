import {mountOperationControls} from './operation-controls.jsx';
import {mountExecutionControls} from './execution-controls.jsx';
import {mountOrganizedCopies} from './organized-cards.jsx';
import {mountOrganizedNavigation} from './organized-navigation.jsx';
import {mountExecutionPreview} from './execution-preview.jsx';
import {mountOperationRows} from './operation-progress.jsx';
import {mountDashboardScanStatus} from './dashboard-scan-status.jsx';
import {mountDashboardBackup} from './dashboard-backup.jsx';
import {mountDashboardCatalog} from './dashboard-catalog.jsx';
import {mountDashboardFolders} from './dashboard-folders.jsx';
import {mountReportClassification} from './report-classification.jsx';
import {mountReportDialog} from './report-dialog.jsx';
import {mountCenterNavigation} from './center-navigation.jsx';
import {mountCenterDetail} from './center-detail.jsx';
import {mountCenterReview} from './center-review.jsx';
import {mountCenterGallery} from './center-gallery.jsx';
import './issue-list.jsx';
import {createManagementFolderBrowser} from './folder-browser.jsx';
import {mountDuplicateReview} from './duplicate-review.jsx';

import {mountStoragePanel} from './storage-panel.jsx';

import {mountWorkspaceMedia} from './workspace-media.jsx';

import {mountUserWorkspace} from './user-workspace.jsx';

globalThis.mountCenterNavigation = mountCenterNavigation;
globalThis.mountCenterDetail = mountCenterDetail;
globalThis.mountCenterReview = mountCenterReview;
globalThis.mountCenterGallery = mountCenterGallery;
globalThis.createManagementFolderBrowser = createManagementFolderBrowser;
globalThis.mountUserWorkspace = mountUserWorkspace;
globalThis.mountWorkspaceMedia = mountWorkspaceMedia;
globalThis.mountStoragePanel = mountStoragePanel;
globalThis.mountDuplicateReview = mountDuplicateReview;

globalThis.mountReportDialog = mountReportDialog;
globalThis.mountReportClassification = mountReportClassification;
globalThis.mountDashboardFolders = mountDashboardFolders;
globalThis.mountDashboardCatalog = mountDashboardCatalog;
globalThis.mountDashboardBackup = mountDashboardBackup;
globalThis.mountDashboardScanStatus = mountDashboardScanStatus;

globalThis.mountOperationRows = mountOperationRows;

globalThis.mountExecutionPreview = mountExecutionPreview;

globalThis.mountOrganizedNavigation = mountOrganizedNavigation;

globalThis.mountOrganizedCopies = mountOrganizedCopies;

globalThis.mountExecutionControls = mountExecutionControls;

globalThis.mountOperationControls = mountOperationControls;
