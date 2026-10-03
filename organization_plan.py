"""Reviewable, report-local organization plans. This module never opens media.

Targets are relative proposals, not filesystem operations. Invalid or colliding
targets remain visible but cannot be included in an exported plan.
"""

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import stat
import threading
import unicodedata


STATE_FILE = "organization-plan.json"
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_UPDATE_IDS = 1000
STATES = frozenset({"pending", "include", "hold"})
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _target_error(value):
    if not isinstance(value, str) or not value:
        return "缺少建议路径"
    if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "建议路径含控制字符"
    if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or "\\" in value):
        return "建议路径必须是相对路径"
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return "建议路径含无效目录层级"
    return ""


def _canonical_target(value):
    return unicodedata.normalize("NFC", value).casefold()


def _csv_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                  or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


class OrganizationPlan:
    """Build a plan from one scan and persist only its review decisions.

    Construction does not load saved decisions. A broken state file raises
    ValueError/OSError when the plan is read or updated, without blocking other
    report pages or overwriting that file. Updates are all-or-nothing.
    """

    def __init__(self, report_dir, document):
        self.report_dir = Path(report_dir).absolute()
        if not isinstance(document, dict) or not isinstance(document.get("files"), list):
            raise ValueError("扫描报告缺少文件清单")
        self._items = []
        self._by_id = {}
        targets = {}
        duplicate_groups = {}
        for number, group in enumerate(document.get("duplicates", []), 1):
            if isinstance(group, dict) and isinstance(group.get("paths"), list):
                for path in group["paths"]:
                    if isinstance(path, str):
                        duplicate_groups[path] = number
        for record in document["files"]:
            if not isinstance(record, dict):
                raise ValueError("扫描报告含无效文件记录")
            path = record.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                raise ValueError("扫描报告含无效原路径")
            identifier = hashlib.sha256(path.encode("utf-8")).hexdigest()
            if identifier in self._by_id:
                raise ValueError("扫描报告含重复原路径")
            target = record.get("suggested_path", "")
            error = _target_error(target)
            item = {"id": identifier, "path": path, "name": Path(path).name,
                    "kind": str(record.get("kind", "")),
                    "bytes": record.get("bytes", 0),
                    "suggested_path": target if isinstance(target, str) else "",
                    "reason": str(record.get("reason", "")),
                    "date_source": str(record.get("date_source", "")),
                    "hash_status": str(record.get("hash_status", "")),
                    "hardlink_to": str(record.get("hardlink_to", "")),
                    "duplicate_group": duplicate_groups.get(path),
                    "selectable": not error, "blocked_reason": error}
            if type(item["bytes"]) is not int or item["bytes"] < 0:
                raise ValueError("扫描报告含无效文件大小")
            self._items.append(item)
            self._by_id[identifier] = item
            if not error:
                targets.setdefault(_canonical_target(target), []).append(item)
        # Case/Unicode-equivalent names can collide on the user's Mac. A file
        # proposed as another file's parent directory is also a conflict.
        for target, items in targets.items():
            if len(items) > 1:
                for item in items:
                    item.update(selectable=False, blocked_reason="建议目标路径重名")
            parts = target.split("/")
            for depth in range(1, len(parts)):
                parents = targets.get("/".join(parts[:depth]), [])
                if parents:
                    for item in items + parents:
                        item.update(selectable=False, blocked_reason="建议文件与目录路径冲突")
        identity = sorted((item["id"], item["suggested_path"]) for item in self._items)
        self._report_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.report_dir.resolve()), threading.RLock())

    def _open_directory(self):
        return os.open(self.report_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))

    def _load(self, directory):
        try:
            descriptor = os.open(STATE_FILE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        except FileNotFoundError:
            return {}
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
                raise ValueError("整理计划文件不是常规文件或过大")
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("整理计划文件过大")
        try:
            saved = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("整理计划文件损坏，请保留该文件后检查") from error
        if (not isinstance(saved, dict) or type(saved.get("version")) is not int
                or saved["version"] != 1 or saved.get("report_id") != self._report_id
                or not isinstance(saved.get("states"), dict)):
            raise ValueError("整理计划文件格式无效或与当前报告不匹配")
        states = saved["states"]
        for identifier, state in states.items():
            if identifier not in self._by_id or not isinstance(state, str) or state not in STATES:
                raise ValueError("整理计划含无效文件标识或状态")
            if state == "include" and not self._by_id[identifier]["selectable"]:
                raise ValueError("整理计划包含不安全或冲突的目标路径")
        return states

    def _save(self, directory, states):
        body = json.dumps({"version": 1, "report_id": self._report_id, "states": states},
                          ensure_ascii=False, indent=2).encode("utf-8")
        if len(body) > MAX_STATE_BYTES:
            raise ValueError("整理计划文件过大")
        temporary = ".organization-plan-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, STATE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _snapshot(self, states):
        counts = {"total": len(self._items), "pending": 0, "include": 0, "hold": 0}
        items, folders = [], {}
        for record in self._items:
            item = {**record, "state": states.get(record["id"], "pending")}
            items.append(item)
            counts[item["state"]] += 1
            if not item["selectable"]:
                continue
            folder = str(PurePosixPath(item["suggested_path"]).parent)
            group = folders.setdefault(folder, {"path": folder, "count": 0, "bytes": 0,
                                                "pending": 0, "include": 0, "hold": 0})
            group["count"] += 1
            group["bytes"] += item["bytes"]
            group[item["state"]] += 1
        return {"items": items, "counts": counts, "folder_count": len(folders),
                "folders": sorted(folders.values(), key=lambda group: group["path"].casefold())}

    def snapshot(self):
        with self._lock:
            directory = self._open_directory()
            try:
                return self._snapshot(self._load(directory))
            finally:
                os.close(directory)

    def set_states(self, ids, state):
        if not isinstance(state, str) or state not in STATES:
            raise ValueError("整理状态必须为 pending、include 或 hold")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_UPDATE_IDS:
            raise ValueError(f"每次请选择 1–{MAX_UPDATE_IDS} 个文件")
        if any(not isinstance(identifier, str) or identifier not in self._by_id for identifier in ids):
            raise ValueError("文件标识不属于当前扫描报告")
        if state == "include" and any(not self._by_id[identifier]["selectable"] for identifier in ids):
            raise ValueError("不能加入计划：建议目标路径无效或重名")
        with self._lock:
            directory = self._open_directory()
            try:
                states = self._load(directory)
                for identifier in ids:
                    if state == "pending":
                        states.pop(identifier, None)
                    else:
                        states[identifier] = state
                self._save(directory, states)
                return self._snapshot(states)
            finally:
                os.close(directory)

    def export_document(self):
        snapshot = self.snapshot()
        included = [item for item in snapshot["items"] if item["state"] == "include"]
        return {"version": 1, "mode": "review_only", "report_id": self._report_id,
                "note": "仅整理计划，目标为相对路径；尚未复制、移动、重命名或删除原媒体。",
                "count": len(included), "items": included}

    def export_csv(self):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        fields = [("path", "原路径"), ("suggested_path", "建议相对路径"), ("kind", "类型"),
                  ("bytes", "字节数"), ("reason", "建议依据"), ("date_source", "日期来源"),
                  ("hash_status", "校验状态"), ("hardlink_to", "硬链接指向")]
        writer.writerow([label for _, label in fields])
        for item in self.export_document()["items"]:
            writer.writerow([_csv_cell(item[key]) for key, _ in fields])
        return stream.getvalue().encode("utf-8-sig")
