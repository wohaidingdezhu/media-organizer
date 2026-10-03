"""Open selected scanned media with native apps; never modify media here."""
import hashlib
import math
import os
from pathlib import Path
import stat
import subprocess
import sys


IMAGE_EXT = set("jpg jpeg png heic heif tif tiff gif bmp webp avif dng cr2 cr3 nef arw raf orf rw2 pef srw".split())
VIDEO_EXT = set("mp4 mov m4v mkv avi wmv flv webm mpg mpeg mts m2ts ts 3gp vob rmvb rm asf".split())


def media_id(path):
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def file_signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


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
        return {"available": sys.platform == "darwin",
                "ids_by_path": {record["path"]: key for key, record in self.records.items()}}

    def validate(self, identifier):
        if not isinstance(identifier, str) or identifier not in self.records:
            raise ValueError("文件不属于当前扫描的媒体清单，请重新扫描")
        record = self.records[identifier]
        try:
            info = checked_stat(record["path"])
        except OSError as error:
            raise ValueError("原文件已移走、磁盘未连接或路径含符号链接，请重新扫描") from error
        expected = record.get("source_signature")
        if expected is not None:
            if (not isinstance(expected, list) or len(expected) != 5
                    or any(type(value) is not int for value in expected)
                    or file_signature(info) != expected):
                raise ValueError("原文件自扫描后发生变化，请重新扫描")
        elif info.st_size != record["bytes"] or info.st_mtime != record["mtime"]:
            raise ValueError("原文件自扫描后发生变化，请重新扫描")
        return record, info

    def perform(self, identifier, action):
        if not isinstance(action, str) or action not in {"open", "reveal"}:
            raise ValueError("仅支持打开媒体或在 Finder 定位")
        if sys.platform != "darwin":
            raise ValueError("打开与定位功能目前需要 macOS")
        record, _ = self.validate(identifier)
        command = ["/usr/bin/open"]
        if action == "reveal":
            command.append("-R")
        command.append(record["path"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("无法启动系统打开功能；请重试或在 Finder 操作") from error
        if result.returncode:
            raise ValueError("系统未能打开文件；请在 Finder 检查默认播放器或查看程序")
        message = "已在 Finder 定位原文件。" if action == "reveal" else (
            "已交给系统默认程序打开；播放情况请在播放器中查看。" if record["kind"] == "视频"
            else "已交给系统默认程序打开照片。")
        return {"ok": True, "action": action, "message": message}
