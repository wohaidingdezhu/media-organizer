"""Append bounded scan batches for browsing; never authorize file operations."""
import json
import re
import threading
import time

import portable_fs as fs
from workspace_data import read_json, write_json

PENDING = 'scan-pending.json'


class ScanFeed:
    def __init__(self, output, directory, roots, photo_enabled, video_enabled):
        self.output, self.directory = output, directory
        self.manifest = {'version': 1, 'report': directory.name, 'roots': list(map(str, roots)),
                         'chunks': 0, 'photo_enabled': photo_enabled, 'video_enabled': video_enabled}
        self.files, self.previews, self.groups = {}, {}, {}
        self.last = None

    def record(self, record):
        self.files[record['path']] = record
        self.flush()

    def preview(self, path, relative):
        self.previews[path] = relative
        self.flush()

    def group(self, group):
        self.groups[group.get('group_key') or group['tag_key']] = group
        self.flush()

    def flush(self, force=False):
        if not (self.files or self.previews or self.groups):
            return
        now = time.monotonic()
        if not force and self.last is not None and len(self.files) + len(self.previews) + len(self.groups) < 200 and now - self.last < 2:
            return
        files = [{**{key: value for key, value in item.items() if not key.startswith('_')},
                  'source_signature': list(item['_signature']), 'browse_only': True}
                 for item in self.files.values()]
        chunk = {'files': files, 'previews': self.previews, 'groups': self.groups}
        number = self.manifest['chunks'] + 1
        # Publish the manifest last, so readers never see half a written batch.
        write_json(self.directory, 'progress-%08d.json' % number, chunk)
        write_json(self.output, PENDING, {**self.manifest, 'chunks': number})
        self.manifest['chunks'] = number
        self.files, self.previews, self.groups = {}, {}, {}
        self.last = now


class ScanFeedReader:
    def __init__(self, output):
        self.output = output
        self.lock = threading.Lock()
        self.report, self.count, self.files, self.previews, self.groups = '', 0, {}, {}, {}
        self.cached = None

    def snapshot(self):
        with self.lock:
            manifest = read_json(self.output, PENDING, limit=128 * 1024 * 1024)
            if manifest is not None and not isinstance(manifest, dict):
                raise ValueError('扫描中的文件清单格式无效')
            if manifest is None or 'files' in manifest:  # Earlier discovery-only snapshots.
                return manifest
            report, count = manifest.get('report'), manifest.get('chunks')
            if (not isinstance(report, str) or not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', report)
                    or type(count) is not int or not 0 <= count <= 1000000):
                raise ValueError('扫描中的文件清单无效')
            if report != self.report or count < self.count:
                self.report, self.count, self.files, self.previews, self.groups = report, 0, {}, {}, {}
                self.cached = None
            if count == self.count and self.cached is not None:
                return self.cached
            # Apply each complete chunk only once; unchanged status polling does
            # not reread all files or rewrite another immutable library snapshot.
            for number in range(self.count + 1, count + 1):
                chunk = json.loads(fs.read_private_file(self.output, (report, 'progress-%08d.json' % number), 128 * 1024 * 1024))
                if (not isinstance(chunk, dict) or not isinstance(chunk.get('files'), list)
                        or any(not isinstance(file, dict) or not isinstance(file.get('path'), str) for file in chunk['files'])
                        or not isinstance(chunk.get('previews'), dict) or not isinstance(chunk.get('groups'), dict)):
                    raise ValueError('扫描更新批次格式无效')
                self.files.update((file['path'], file) for file in chunk['files'])
                self.previews.update(chunk['previews'])
                self.groups.update(chunk['groups'])
                self.count = number
            self.cached = {**manifest, 'revision': report + ':' + str(count), 'files': list(self.files.values()),
                           'previews': dict(self.previews), 'groups': list(self.groups.values())}
            return self.cached
