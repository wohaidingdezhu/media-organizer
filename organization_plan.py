"""Reviewable, report-local organization plans. This module never opens media.

Targets are relative proposals, not filesystem operations. Invalid or colliding
targets remain visible but cannot be included in an exported plan.
"""

import csv
import hashlib
import io
import json
import math
import portable_fs as os
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
    if len(value) > 4096:
        return "建议路径过长"
    if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "建议路径含控制字符"
    if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or "\\" in value):
        return "建议路径必须是相对路径"
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return "建议路径含无效目录层级"
    if any(invalid_windows_name(part) for part in value.split("/")):
        return "建议路径含 Windows 不支持的文件名"
    return ""


def invalid_windows_name(part):
    import re
    return (bool(re.search(r'[<>:"|?*]', part)) or part.endswith((" ", "."))
            or part.split(".", 1)[0].rstrip().upper() in
            {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *[f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"]})


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
        duplicate_groups = {}
        duplicates = document.get("duplicates", [])
        if not isinstance(duplicates, list):
            raise ValueError("扫描报告含无效重复分组")
        for number, group in enumerate(duplicates, 1):
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
            pure = PureWindowsPath(path) if PureWindowsPath(path).is_absolute() else PurePosixPath(path)
            mtime = record.get('mtime')
            try:
                valid_time = type(mtime) in {int, float} and math.isfinite(mtime) and abs(mtime) <= 8640000000000
            except OverflowError:
                valid_time = False
            item = {"id": identifier, "path": path, "name": pure.name,
                    "source_folder": str(pure.parent), "mtime": mtime if valid_time else None,
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
        identity = sorted((item["id"], item["suggested_path"]) for item in self._items)
        self._report_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.report_dir.resolve()), threading.RLock())

    def _effective_items(self, overrides):
        items = []
        targets = {}
        for record in self._items:
            target = overrides.get(record["id"], record["suggested_path"])
            error = _target_error(target)
            item = {**record, "suggested_path": target,
                    "original_suggested_path": record["suggested_path"],
                    "target_edited": target != record["suggested_path"],
                    "selectable": not error, "blocked_reason": error}
            items.append(item)
            if not error:
                targets.setdefault(_canonical_target(target), []).append(item)
        # Case/Unicode-equivalent names can collide on the user's Mac. A file
        # proposed as another file's parent directory is also a conflict.
        for target, group in targets.items():
            if len(group) > 1:
                for item in group:
                    item.update(selectable=False, blocked_reason="建议目标路径重名")
            parts = target.split("/")
            for depth in range(1, len(parts)):
                parents = targets.get("/".join(parts[:depth]), [])
                if parents:
                    for item in group + parents:
                        item.update(selectable=False, blocked_reason="建议文件与目录路径冲突")
        return items

    def _open_directory(self):
        return os.open(self.report_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))

    def _load(self, directory):
        try:
            descriptor = os.open(STATE_FILE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        except FileNotFoundError:
            return {}, {}
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
                or saved["version"] not in {1, 2} or saved.get("report_id") != self._report_id
                or not isinstance(saved.get("states"), dict)):
            raise ValueError("整理计划文件格式无效或与当前报告不匹配")
        states = saved["states"]
        overrides = saved.get("targets", {}) if saved["version"] == 2 else {}
        if not isinstance(overrides, dict):
            raise ValueError("整理计划含无效分类位置")
        for identifier, target in overrides.items():
            if identifier not in self._by_id or _target_error(target):
                raise ValueError("整理计划含无效文件标识或分类位置")
        for identifier, state in states.items():
            if identifier not in self._by_id or not isinstance(state, str) or state not in STATES:
                raise ValueError("整理计划含无效文件标识或状态")
        self._validate_included(states, self._effective_items(overrides))
        return states, overrides

    @staticmethod
    def _validate_included(states, items):
        for item in items:
            if states.get(item["id"]) == "include" and not item["selectable"]:
                raise ValueError("整理计划包含不安全或冲突的目标路径")

    def _save(self, directory, states, overrides):
        body = json.dumps({"version": 2, "report_id": self._report_id,
                           "states": states, "targets": overrides},
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

    def _snapshot(self, states, overrides):
        counts = {"total": len(self._items), "pending": 0, "include": 0, "hold": 0}
        items, folders = [], {}
        for record in self._effective_items(overrides):
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
                "revision": self._revision(states, overrides),
                "review": {"blocked": sum(not item["selectable"] for item in items),
                           "duplicates": sum(bool(item["duplicate_group"]) for item in items),
                           "edited": sum(item["target_edited"] for item in items)},
                "folders": sorted(folders.values(), key=lambda group: group["path"].casefold())}

    def _revision(self, states, overrides):
        body = json.dumps([self._report_id, states, overrides], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()

    def _folder_changes(self, ids, folder, states, overrides):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 200
                or any(not isinstance(key, str) or key not in self._by_id for key in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('批量调整每次请选择 1–200 个不同的当前报告文件')
        error = _target_error(folder)
        if error:
            raise ValueError('分类目录无效：' + error)
        next_states, next_targets, changes = dict(states), dict(overrides), []
        for key in ids:
            old = overrides.get(key, self._by_id[key]['suggested_path'])
            if _target_error(old):
                raise ValueError('原建议路径无效，请先逐项修正：' + self._by_id[key]['name'])
            target = str(PurePosixPath(folder) / PurePosixPath(old).name)
            error = _target_error(target)
            if error:
                raise ValueError(error + '：' + self._by_id[key]['name'])
            if target == self._by_id[key]['suggested_path']:
                next_targets.pop(key, None)
            else:
                next_targets[key] = target
            changed = old != target
            if changed:
                next_states.pop(key, None)
            changes.append({'id': key, 'path': self._by_id[key]['path'], 'before': old,
                            'after': target, 'state': states.get(key, 'pending'), 'changed': changed})
        items = self._effective_items(next_targets)
        selected = set(ids)
        blocked = next((item for item in items if item['id'] in selected and not item['selectable']), None)
        if blocked:
            raise ValueError(blocked['blocked_reason'] + '：' + blocked['suggested_path'] + '；整批未保存')
        self._validate_included(next_states, items)
        return next_states, next_targets, changes

    def preview_folder(self, ids, folder):
        """Read-only preview of proposed targets; never read source media."""
        with self._lock:
            directory = self._open_directory()
            try:
                states, overrides = self._load(directory)
                _, _, changes = self._folder_changes(ids, folder, states, overrides)
                return {'revision': self._revision(states, overrides), 'folder': folder, 'items': changes,
                        'changed_count': sum(item['changed'] for item in changes),
                        'reset_count': sum(item['changed'] and item['state'] != 'pending' for item in changes)}
            finally:
                os.close(directory)

    def apply_folder(self, ids, folder, revision):
        """Atomic proposal update after preview; changed targets need review again."""
        with self._lock:
            directory = self._open_directory()
            try:
                states, overrides = self._load(directory)
                if not isinstance(revision, str) or revision != self._revision(states, overrides):
                    raise ValueError('整理计划已变化，请读取最新进度后重新预览；整批未保存')
                states, overrides, _ = self._folder_changes(ids, folder, states, overrides)
                self._save(directory, states, overrides)
                return self._snapshot(states, overrides)
            finally:
                os.close(directory)

    def snapshot(self):
        with self._lock:
            directory = self._open_directory()
            try:
                return self._snapshot(*self._load(directory))
            finally:
                os.close(directory)

    def set_states(self, ids, state):
        if not isinstance(state, str) or state not in STATES:
            raise ValueError("整理状态必须为 pending、include 或 hold")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_UPDATE_IDS:
            raise ValueError(f"每次请选择 1–{MAX_UPDATE_IDS} 个文件")
        if any(not isinstance(identifier, str) or identifier not in self._by_id for identifier in ids):
            raise ValueError("文件标识不属于当前扫描报告")
        with self._lock:
            directory = self._open_directory()
            try:
                states, overrides = self._load(directory)
                for identifier in ids:
                    if state == "pending":
                        states.pop(identifier, None)
                    else:
                        states[identifier] = state
                self._validate_included(states, self._effective_items(overrides))
                self._save(directory, states, overrides)
                return self._snapshot(states, overrides)
            finally:
                os.close(directory)

    def set_target(self, identifier, target):
        """Change a proposal only; None restores the scan's original proposal."""
        if not isinstance(identifier, str) or identifier not in self._by_id:
            raise ValueError("文件标识不属于当前扫描报告")
        if target is not None:
            error = _target_error(target)
            if error:
                raise ValueError(error)
        with self._lock:
            directory = self._open_directory()
            try:
                states, overrides = self._load(directory)
                previous = overrides.get(identifier, self._by_id[identifier]["suggested_path"])
                if target is None or target == self._by_id[identifier]["suggested_path"]:
                    overrides.pop(identifier, None)
                else:
                    overrides[identifier] = target
                items = self._effective_items(overrides)
                changed = next(item for item in items if item["id"] == identifier)
                if target is not None and not changed["selectable"]:
                    raise ValueError(changed["blocked_reason"])
                if changed["suggested_path"] != previous:
                    states.pop(identifier, None)
                for item in items:
                    if states.get(item["id"]) == "include" and not item["selectable"]:
                        raise ValueError(f"会与已纳入文件“{item['name']}”冲突；请先将该项恢复待核对")
                self._save(directory, states, overrides)
                return self._snapshot(states, overrides)
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
                  ("hash_status", "校验状态"), ("hardlink_to", "硬链接指向"),
                  ("original_suggested_path", "扫描原建议路径"), ("target_edited", "手动调整")]
        writer.writerow([label for _, label in fields])
        for item in self.export_document()["items"]:
            writer.writerow([_csv_cell(item[key]) for key, _ in fields])
        return stream.getvalue().encode("utf-8-sig")
