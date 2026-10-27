"""Open selected scanned media with native apps; never modify media here."""
import hashlib
import math
import re
import threading
import portable_fs as os
from pathlib import Path
import stat
import subprocess
import sys


IMAGE_EXT = set("jpg jpeg png heic heif tif tiff gif bmp webp avif dng cr2 cr3 nef arw raf orf rw2 pef srw".split())
VIDEO_EXT = set("mp4 mov m4v mkv avi wmv flv webm mpg mpeg mts m2ts ts 3gp vob rmvb rm asf".split())


def media_id(path):
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def file_signature(info):
    # Windows stat/fstat differ in ctime semantics on recent Python versions.
    # Use the stable creation time there, alongside file identity/size/mtime.
    identity_time = getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity_time]


def checked_stat(path):
    """Inspect an absolute regular file without following any symlink component."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("原文件路径无效")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    directory = os.open(path.anchor, flags | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("原文件不是普通文件，请重新扫描")
        return info
    finally:
        os.close(directory)


class MediaActions:
    def __init__(self, document):
        self.records = {}
        self.view_signatures = {}
        self.view_lock = threading.Lock()
        if document.get('restored_snapshot'):
            self.roots = []
            return
        roots = document.get("roots", [])
        if not isinstance(roots, list):
            roots = []
        roots = [Path(root) for root in roots if isinstance(root, str) and Path(root).is_absolute()]
        self.roots = roots
        files = document.get("files", [])
        if not isinstance(files, list):
            files = []
        for record in files:
            if not isinstance(record, dict):
                continue
            path = record.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                continue
            source = Path(path)
            extensions = IMAGE_EXT if record.get("kind") == "照片" else VIDEO_EXT if record.get("kind") == "视频" else set()
            if (not source.is_absolute() or ".." in source.parts or source.suffix.lower().lstrip(".") not in extensions
                    or not any(root in source.parents for root in roots)
                    or type(record.get("bytes")) is not int or record["bytes"] < 0
                    or type(record.get("mtime")) not in {int, float} or not math.isfinite(record["mtime"])):
                continue
            self.records[media_id(path)] = record

    def snapshot(self):
        return {"available": sys.platform in {"darwin", "win32"},
                "ids_by_path": {record["path"]: key for key, record in self.records.items()}}

    def availability(self, identifier):
        """Only call a file missing if its scanned root is securely accessible.

        A disconnected drive, inaccessible directory or unsafe path is not proof
        of deletion. These states must never silently hide a library entry.
        """
        if not isinstance(identifier, str) or identifier not in self.records:
            raise ValueError("文件不属于当前扫描的媒体清单，请重新扫描")
        path = Path(self.records[identifier]['path'])
        roots = [root for root in self.roots if root in path.parents]
        root = max(roots, key=lambda item: len(item.parts))
        from file_operations import open_directory
        try:
            descriptor = open_directory(root)
        except (OSError, ValueError):
            return 'unavailable'
        try:
            # A mount point can still exist after an external disk disappears.
            # Hide only when the root is on the same device as the recorded
            # file; old reports and nested mounts without proof stay visible.
            expected = self.records[identifier].get('source_signature')
            try:
                root_device = os.fstat(descriptor).st_dev
            except OSError:
                return 'unavailable'
            if (not isinstance(expected, list) or len(expected) != 5
                    or any(type(value) is not int for value in expected)
                    or root_device != expected[0]):
                return 'unavailable'
            try:
                checked_stat(path)
            except FileNotFoundError:
                # Reopen the root after the lookup to catch a disconnected or
                # replaced volume during the check.
                try:
                    current = open_directory(root)
                    try:
                        if os.fstat(current).st_dev != expected[0]:
                            return 'unavailable'
                    finally:
                        os.close(current)
                except (OSError, ValueError):
                    return 'unavailable'
                return 'missing'
            except (OSError, ValueError):
                return 'unavailable'
            return 'available'
        finally:
            os.close(descriptor)

    def validate(self, identifier):
        if not isinstance(identifier, str) or identifier not in self.records:
            raise ValueError("文件不属于当前扫描的媒体清单，请重新扫描")
        record = self.records[identifier]
        try:
            info = checked_stat(record["path"])
        except OSError as error:
            if self.availability(identifier) == 'missing':
                raise ValueError(f"文件已不在原位置：{record['path']}。可刷新列表移除该失效记录；原文件不会被操作。") from error
            raise ValueError(f"无法访问文件：{record['path']}。请检查来源磁盘、文件夹权限或符号链接，连接恢复后重试。") from error
        expected = record.get("source_signature")
        if expected is not None:
            if (not isinstance(expected, list) or len(expected) != 5
                    or any(type(value) is not int for value in expected)
                    or file_signature(info) != expected):
                actual = file_signature(info)
                if isinstance(expected, list) and len(expected) == 5 and actual[:4] == expected[:4]:
                    raise ValueError("文件属性自扫描后已更新（大小和修改时间未变），请重新扫描后再操作；播放器或系统可能更新文件属性")
                raise ValueError("原文件的内容或身份自扫描后发生变化，请重新扫描后再操作")
        elif info.st_size != record["bytes"] or info.st_mtime != record["mtime"]:
            raise ValueError("原文件自扫描后发生变化，请重新扫描")
        return record, info

    def validate_for_view(self, identifier):
        """Recover attribute-only drift only after proving unchanged full content.

        This cache is private to viewing. Copy/trash continue using validate()
        against the original scan signature, including its identity timestamp.
        """
        try:
            return self.validate(identifier)
        except ValueError as failure:
            if not isinstance(identifier, str) or identifier not in self.records:
                raise
            record = self.records[identifier]
            expected, digest = record.get('source_signature'), record.get('sha256')
            try:
                info = checked_stat(record['path'])
            except (OSError, ValueError):
                raise failure
            actual = file_signature(info)
            if (not isinstance(expected, list) or len(expected) != 5
                    or any(type(n) is not int for n in expected) or actual[:4] != expected[:4]
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                    or info.st_size > 64 * 1024 * 1024):
                raise failure
            with self.view_lock:
                if self.view_signatures.get(identifier) == actual:
                    return record, info
                with os.pinned_source(record['path']) as (descriptor, _, __):
                    if file_signature(os.fstat(descriptor)) != actual:
                        raise failure
                    calculated = hashlib.sha256()
                    remaining = info.st_size
                    while remaining:
                        block = os.read(descriptor, min(1024 * 1024, remaining))
                        if not block:
                            raise failure
                        remaining -= len(block)
                        calculated.update(block)
                    if (calculated.hexdigest() != digest or file_signature(os.fstat(descriptor)) != actual
                            or file_signature(checked_stat(record['path'])) != actual):
                        raise failure
                self.view_signatures[identifier] = actual
                return record, info

    def perform(self, identifier, action):
        if not isinstance(action, str) or action not in {"open", "reveal"}:
            raise ValueError("仅支持打开媒体或在文件管理器定位")
        if sys.platform == "win32":
            from system_integration import open_path
            record, _ = self.validate_for_view(identifier)
            open_path(record["path"], reveal=action == "reveal")
            return {"ok": True, "action": action, "message": "已交给系统默认程序打开。" if action == "open" else "已在资源管理器定位原文件。"}
        if sys.platform != "darwin":
            raise ValueError("打开与定位功能目前需要 macOS")
        record, _ = self.validate_for_view(identifier)
        command = ["/usr/bin/open"]
        if action == "reveal":
            command.append("-R")
        command.append(record["path"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("无法启动系统打开功能；请重试或在 文件管理器 操作") from error
        if result.returncode:
            raise ValueError("系统未能打开文件；请在 文件管理器 检查默认播放器或查看程序")
        message = "已在 文件管理器 定位原文件。" if action == "reveal" else (
            "已交给系统默认程序打开；播放情况请在播放器中查看。" if record["kind"] == "视频"
            else "已交给系统默认程序打开照片。")
        return {"ok": True, "action": action, "message": message}
