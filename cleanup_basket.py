"""Persistent manual candidates scoped to a scan. No original media access."""
import portable_lock as fcntl
import hashlib
import json
import portable_fs as os
from pathlib import Path
import secrets
import stat

from file_operations import open_directory, FLAGS
from media_actions import media_id


MAX_CANDIDATES = 200
STATE_FILE = 'cleanup-basket.json'
MAX_STATE_BYTES = 64 * 1024


class CleanupBasket:
    def __init__(self, report_dir, document):
        self.report_dir = Path(report_dir)
        records = document.get('files')
        if not isinstance(records, list):
            raise ValueError('扫描报告缺少有效文件清单')
        self.allowed = set()
        identity = []
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get('path'), str)
                    or not Path(record['path']).is_absolute() or '..' in Path(record['path']).parts
                    or '\x00' in record['path'] or not isinstance(record.get('kind'), str) or record['kind'] not in {'照片', '视频'}
                    or type(record.get('bytes')) is not int or record['bytes'] < 0):
                raise ValueError('扫描报告含无效媒体记录')
            identifier = media_id(record['path'])
            if identifier in self.allowed:
                raise ValueError('扫描报告含重复原路径')
            self.allowed.add(identifier)
            identity.append((identifier, record['bytes'], record.get('source_signature'), record.get('sha256')))
        self.report_id = hashlib.sha256(json.dumps(sorted(identity), ensure_ascii=False).encode()).hexdigest()

    def _load(self, directory):
        try:
            fd = os.open(STATE_FILE, FLAGS, dir_fd=directory)
        except FileNotFoundError:
            return set()
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
                raise ValueError('候选篮文件无效或过大，请先核对该报告中的 cleanup-basket.json')
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError('候选篮文件过大')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise ValueError('候选篮文件损坏，原记录已保留，请先人工核对') from error
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or data.get('report_id') != self.report_id
                or not isinstance(data.get('ids'), list) or len(data['ids']) > MAX_CANDIDATES
                or any(not isinstance(identifier, str) or identifier not in self.allowed for identifier in data['ids'])
                or len(set(data['ids'])) != len(data['ids'])):
            raise ValueError('候选篮与当前报告不匹配或清单无效，原记录已保留')
        return set(data['ids'])

    def _snapshot(self, ids):
        return {'ids': sorted(ids), 'count': len(ids), 'limit': MAX_CANDIDATES}

    def snapshot(self):
        directory = open_directory(self.report_dir)
        try:
            return self._snapshot(self._load(directory))
        finally:
            os.close(directory)

    def update(self, action, identifiers):
        if not isinstance(action, str) or action not in {'add', 'remove', 'clear'}:
            raise ValueError('候选篮操作无效')
        if (not isinstance(identifiers, list) or len(identifiers) > MAX_CANDIDATES
                or any(not isinstance(identifier, str) or identifier not in self.allowed for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers) or (action == 'clear' and identifiers)):
            raise ValueError('请选择本报告的媒体，候选篮单批最多 200 项')
        directory = open_directory(self.report_dir)
        lock = None
        temporary = '.cleanup-basket-temp-' + secrets.token_hex(12)
        created = False
        try:
            lock = os.open_lock('.cleanup-basket-lock', directory)
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise ValueError('候选篮锁无效')
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self._load(directory)
            new = current | set(identifiers) if action == 'add' else current - set(identifiers) if action == 'remove' else set()
            if len(new) > MAX_CANDIDATES:
                raise ValueError('候选篮最多 200 项，请先核对或移出部分候选；本次未添加')
            if new == current:
                return self._snapshot(current)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            created = True
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'report_id': self.report_id, 'ids': sorted(new)}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                info = os.stat(STATE_FILE, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('候选篮文件已被替换，拒绝覆盖')
            except FileNotFoundError:
                pass
            os.replace(temporary, STATE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
            created = False
            return self._snapshot(new)
        finally:
            try:
                if created:
                    os.unlink(temporary, dir_fd=directory)
            finally:
                if lock is not None:
                    os.close(lock)
                os.close(directory)
