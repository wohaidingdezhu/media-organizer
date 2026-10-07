# coding: utf-8
"""Persistent dashboard collections, opt-in read-only refresh and restore previews."""
from pathlib import Path
import secrets
import threading
import time

from workspace_data import active_workspace, data_lock, read_json, write_json
from library_index import catalog, sync_index
from library_backup import export_backup, validate_backup, restore_backup
from file_operations import maintenance_when_idle


class WorkspaceController:
    def init_workspace(self, output):
        self.base_output = __import__("portable_fs").private_data_path(output)
        self.output = active_workspace(self.base_output)
        self.monitor = {'enabled': False, 'minutes': 15}
        self.next_scan = None
        self.monitor_stop = threading.Event()
        self.pending_restore = None
        self.scan_options = {}
        self.load_settings()

    def load_settings(self):
        try:
            settings = read_json(self.output, 'workspace-settings.json', {}, 65536) if self.output.exists() else {}
            folders = settings.get('folders', [])
            monitor = settings.get('monitor', self.monitor)
            if (not isinstance(folders, list) or len(folders) > 100 or not all(isinstance(path, str) and Path(path).is_absolute() and '..' not in Path(path).parts for path in folders)
                    or not isinstance(monitor, dict) or type(monitor.get('enabled')) is not bool or type(monitor.get('minutes')) is not int or not 1 <= monitor['minutes'] <= 1440):
                raise ValueError('目录合集或定时更新设置无效')
            self.folders = folders
            self.monitor = monitor
            self.next_scan = time.monotonic() + monitor['minutes'] * 60 if monitor['enabled'] else None
        except (OSError, ValueError, AttributeError) as error:
            self.log('设置未加载：' + str(error))

    def save_settings(self):
        with data_lock(self.output):
            write_json(self.output, 'workspace-settings.json', {'version': 1, 'folders': self.folders, 'monitor': self.monitor})

    def set_monitor(self, options):
        enabled, minutes = options.get('enabled'), options.get('minutes')
        if type(enabled) is not bool or type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError('更新间隔须为 1–1440 分钟')
        with self.lock:
            if enabled and not self.folders:
                raise ValueError('请先选择目录合集')
            self.monitor = {'enabled': enabled, 'minutes': minutes}
            self.next_scan = time.monotonic() + minutes * 60 if enabled else None
            self.save_settings()
        return self.snapshot()

    def monitor_tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if (not self.monitor['enabled'] or self.running or self.next_scan is None or now < self.next_scan):
                return False
            self.next_scan = now + self.monitor['minutes'] * 60
            try:
                self.start_scan(self.scan_options)
            except (OSError, ValueError) as error:
                self.status = '定时更新未启动：' + str(error)
            return True

    def start_monitor(self):
        def run():
            while not self.monitor_stop.wait(1):
                self.monitor_tick()
        threading.Thread(target=run, daemon=True).start()

    def workspace_catalog(self, options):
        with self.lock:
            return catalog(self.output, **{key: options.get(key, default) for key, default in
                                          [('query', ''), ('folder', ''), ('kind', ''), ('state', ''), ('page', 0)]})

    def backup(self):
        with self.lock:
            if self.running:
                raise ValueError('请先完成或取消扫描，再备份资料库')
            sync_index(self.output)
            return export_backup(self.output)

    def preview_restore(self, body):
        files, summary = validate_backup(body)
        with self.lock:
            token = secrets.token_urlsafe(24)
            self.pending_restore = (token, time.monotonic() + 600, files)
        return {**summary, 'token': token}

    def confirm_restore(self, token):
        with self.lock:
            if self.running:
                raise ValueError('请先完成或取消扫描，再恢复资料库')
            pending = self.pending_restore
            if not isinstance(token, str) or pending is None or not secrets.compare_digest(token, pending[0]) or time.monotonic() > pending[1]:
                raise ValueError('恢复预览已过期，请重新选择备份')
            with maintenance_when_idle(self.output), data_lock(self.output):
                target = restore_backup(self.base_output, pending[2])
                # Validate and index before activating; retain the previous workspace.
                sync_index(target)
                with data_lock(self.base_output):
                    write_json(self.base_output, 'active-workspace.json', {'workspace': target.name})
                self.close_library()
                self.output = target
                self.cancel_file = target / ('.scan-cancel-' + secrets.token_hex(12))
                self.report_cache.clear()
                self.folders = []
                self.monitor = {'enabled': False, 'minutes': 15}
                self.next_scan = None
                self.pending_restore = None
                self.status = '已恢复为独立资料库副本；请重新选择本机媒体目录并扫描，启用原文件操作。'
            return self.snapshot()
