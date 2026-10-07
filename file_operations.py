"""Explicitly confirmed copies and recoverable macOS Trash operations.

Nothing happens at scan time. Each batch has a short-lived server-side preview,
is revalidated before execution and keeps a private journal. Never overwrites.
"""
import hashlib
import portable_lock as fcntl
import json
import portable_fs as os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import unicodedata

from media_actions import checked_stat, file_signature
from organization_plan import _target_error

_BATCH_LOCK = threading.Lock()
_COMPILE_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()
_ACTIVE_JOBS = {}
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def shutdown_when_idle(callback):
    """Reserve the operation lock until the controller and its viewers close."""
    if not _BATCH_LOCK.acquire(blocking=False):
        raise ValueError("文件操作正在执行，请等待完成后再退出本机服务")
    def stop():
        try:
            callback()
        finally:
            _BATCH_LOCK.release()
    try:
        threading.Thread(target=stop, daemon=True).start()
    except BaseException:
        _BATCH_LOCK.release()
        raise


def open_directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("请选择绝对路径的普通文件夹")
    descriptor = os.open(path.anchor, FLAGS | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, FLAGS | os.O_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def canonical(name):
    return unicodedata.normalize("NFC", name).casefold()


def child_directory(parent, name, create=False):
    matches = [entry for entry in os.listdir(parent) if canonical(entry) == canonical(name)]
    if matches and matches != [name]:
        raise ValueError("目标目录存在大小写或 Unicode 等价重名：" + name)
    if not matches and create:
        os.mkdir(name, 0o700, dir_fd=parent)
    return os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)


def check_target(destination, relative):
    if _target_error(relative):
        raise ValueError("分类路径无效")
    descriptor = open_directory(destination)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            try:
                child = child_directory(descriptor, part)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = child
        if any(canonical(entry) == canonical(parts[-1]) for entry in os.listdir(descriptor)):
            raise ValueError("目标已存在，不会覆盖：" + relative)
    finally:
        os.close(descriptor)


class OperationCancelled(ValueError):
    """A cooperative stop before publishing the current copy."""


def copy_one(media, identifier, destination, relative, expected, dest_identity=None, progress=None, cancelled=None):
    def check_cancelled():
        if cancelled and cancelled():
            raise OperationCancelled("已安全停止当前复制，未完成的临时副本已清理；原件保留")
    check_cancelled()
    record, info = media.validate(identifier)
    if file_signature(info) != expected:
        raise ValueError("文件自预览后发生变化")
    if _target_error(relative):
        raise ValueError("分类路径无效")
    parent = open_directory(destination)
    source_parent = None
    source = None
    temporary = ".media-copy-" + secrets.token_hex(12)
    temporary_created = False
    try:
        if dest_identity is not None and [os.fstat(parent).st_dev, os.fstat(parent).st_ino] != dest_identity:
            raise ValueError("分类目标已被替换")
        source_parent = open_directory(Path(record["path"]).parent)
        source = os.open(Path(record["path"]).name, FLAGS, dir_fd=source_parent)
        if file_signature(os.fstat(source)) != expected:
            raise ValueError("原文件已被替换")
        parts = relative.split("/")
        for part in parts[:-1]:
            child = child_directory(parent, part, create=True)
            os.close(parent)
            parent = child
        if any(canonical(entry) == canonical(parts[-1]) for entry in os.listdir(parent)):
            raise ValueError("目标已存在，不会覆盖：" + relative)
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        temporary_created = True
        digest, amount = hashlib.sha256(), 0
        next_update = 0
        with os.fdopen(output, "wb") as stream:
            if progress:
                progress(0, "copying")
            while True:
                check_cancelled()
                chunk = os.read(source, 4 * 1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                amount += len(chunk)
                if progress and time.monotonic() >= next_update:
                    progress(amount, "copying")
                    next_update = time.monotonic() + 1
            stream.flush()
            os.fsync(stream.fileno())
            os.utime(stream.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
        if amount != info.st_size or file_signature(os.fstat(source)) != expected:
            raise ValueError("复制期间原文件发生变化")
        _, latest = media.validate(identifier)
        if file_signature(latest) != expected:
            raise ValueError("复制期间原路径发生变化")
        expected_hash = digest.hexdigest()
        if record.get("sha256") and record["sha256"] != expected_hash:
            raise ValueError("复制内容与扫描时 SHA-256 不一致")
        verify = hashlib.sha256()
        verified, next_update = 0, 0
        if progress:
            progress(0, "verifying")
        descriptor = os.open(temporary, FLAGS, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                check_cancelled()
                verify.update(chunk)
                verified += len(chunk)
                if progress and time.monotonic() >= next_update:
                    progress(verified, "verifying")
                    next_update = time.monotonic() + 1
        if verify.hexdigest() != expected_hash:
            raise ValueError("目标副本 SHA-256 校验失败")
        check_cancelled()
        # Exclusive publication: an existing file is never replaced.
        os.publish(temporary, parts[-1], parent)
        if progress:
            progress(verified, "verified")
        return {"target": str(Path(destination) / relative), "sha256": expected_hash}
    finally:
        try:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass  # Windows publishes by an exclusive rename.
        finally:
            if source is not None:
                os.close(source)
            if source_parent is not None:
                os.close(source_parent)
            os.close(parent)


def trash_helper():
    if sys.platform == "win32":
        from system_integration import recycle_available
        recycle_available()
        return None
    if sys.platform != "darwin":
        raise ValueError("移到废纸篓 / 回收站需要 macOS")
    base = Path(__file__).resolve().parent / "native"
    source, helper = base / "trash_media.swift", base / "trash_media"
    with _COMPILE_LOCK:
        if helper.is_symlink() or source.is_symlink():
            raise ValueError("废纸篓 / 回收站组件不能是符号链接")
        if not helper.is_file() or helper.stat().st_mtime_ns < source.stat().st_mtime_ns:
            compiler = shutil.which("swiftc")
            if not compiler:
                raise ValueError("缺少 Swift 编译器，无法准备废纸篓 / 回收站组件；可在 文件管理器 自行处理")
            try:
                result = subprocess.run([compiler, str(source), "-o", str(helper)], capture_output=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValueError("废纸篓 / 回收站组件准备失败") from error
            if result.returncode:
                raise ValueError("废纸篓 / 回收站组件编译失败；可在 文件管理器 自行处理")
    return helper


class OutcomeUnknown(ValueError):
    pass


def trash_one(media, identifier, expected):
    record, info = media.validate(identifier)
    if file_signature(info) != expected:
        raise ValueError("文件自预览后发生变化")
    if sys.platform == "win32":
        from system_integration import recycle_file
        return recycle_file(record["path"], expected)
    try:
        result = subprocess.run([str(trash_helper())], input=json.dumps({"path": record["path"], "signature": expected}),
                                capture_output=True, text=True, timeout=60)
        answer = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise OutcomeUnknown("系统操作结果未确认；请先在 文件管理器 和废纸篓 / 回收站核对，不要直接重试") from error
    if not isinstance(answer, dict):
        raise OutcomeUnknown("系统返回的清理结果无效，请先在 文件管理器 核对")
    if result.returncode or not answer.get("ok"):
        try:
            if file_signature(checked_stat(record["path"])) != expected:
                raise OutcomeUnknown("原路径已变化，清理结果未确认，请先在 文件管理器 核对")
        except OSError as error:
            raise OutcomeUnknown("原文件已不在原位置，清理结果未确认，请先在 文件管理器 核对") from error
        raise ValueError(answer.get("error", "系统未能移到废纸篓 / 回收站"))
    return {"trashed_path": answer.get("trashed_path", "")}


class FileOperations:
    def __init__(self, report_dir, media, plan):
        self.report_dir, self.media, self.plan = Path(report_dir), media, plan
        self.lock = _STATE_LOCK
        self.report_key = os.path.abspath(self.report_dir)
        self.previews, self.jobs = {}, {}

    def _stop_requested(self, identifier):
        parent = open_directory(self.report_dir / "operations")
        try:
            try:
                fd = os.open(".stop-" + identifier, FLAGS, dir_fd=parent)
            except FileNotFoundError:
                return False
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size != 0:
                    raise ValueError("停止请求记录无效，请先核对操作记录")
                return True
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def request_stop(self, identifier):
        with self.lock:
            job = self.snapshot(identifier)  # Validates the identifier and report scope.
            if job["status"] not in {"running", "external_running"}:
                return job  # A late or repeated request never changes finished work.
            parent = open_directory(self.report_dir / "operations")
            try:
                try:
                    fd = os.open(".stop-" + identifier, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                except FileExistsError:
                    self._stop_requested(identifier)
                else:
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                os.close(parent)
            return self.snapshot(identifier)

    def _job_lock(self):
        directory = self.report_dir / "operations"
        if directory.is_symlink():
            raise ValueError("操作记录目录不能是符号链接")
        directory.mkdir(mode=0o700, exist_ok=True)
        parent = open_directory(directory)
        try:
            fd = os.open_lock(".batch-lock", parent)
        finally:
            os.close(parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("操作锁文件无效")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _record_is_active(self, identifier):
        parent = open_directory(self.report_dir / "operations")
        try:
            try:
                fd = os.open(".batch-lock", FLAGS, dir_fd=parent)
            except FileNotFoundError:
                return False  # Legacy journals have no process lock.
        finally:
            os.close(parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("操作锁文件无效")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return os.pread(fd, 25, 0).decode("ascii", errors="replace") == identifier
            return False
        finally:
            os.close(fd)

    def update_plan(self, callback):
        """Keep confirmed targets fixed while any batch owns the report lock."""
        if not _BATCH_LOCK.acquire(blocking=False):
            raise ValueError("文件操作正在执行，请等待完成后再修改整理计划")
        descriptor = None
        try:
            try:
                descriptor = self._job_lock()
            except BlockingIOError as error:
                raise ValueError("另一服务正在执行文件操作，请等待完成后再修改整理计划") from error
            return callback()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            _BATCH_LOCK.release()

    def _check_kept_duplicates(self, ids, current):
        selected_paths = {self.media.records[identifier]["path"] for identifier in ids if identifier in self.media.records}
        duplicate_sets = {}
        for item in current.values():
            if item.get("duplicate_group"):
                duplicate_sets.setdefault(item["duplicate_group"], set()).add(item["path"])
        for paths in duplicate_sets.values():
            if not paths.intersection(selected_paths):
                continue
            if len(paths) > 1 and paths <= selected_paths:
                raise ValueError("同一精确重复组请至少保留一个文件；不能整组一起清理")
            kept = [identifier for identifier, record in self.media.records.items() if record["path"] in paths - selected_paths]
            available = False
            for identifier in kept:
                try:
                    self.media.validate(identifier)
                    available = True
                    break
                except (OSError, ValueError):
                    continue
            if not available:
                raise ValueError("计划保留的精确重复文件已变化或不可访问，请先重新扫描并核对")

    def preview(self, mode, ids, destination=None):
        if mode not in {"copy", "trash"} or not isinstance(ids, list) or not 1 <= len(ids) <= 200:
            raise ValueError("请选择 1–200 个文件并指定复制或废纸篓 / 回收站操作")
        if any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("文件选择无效")
        current = {item["id"]: item for item in self.plan().snapshot()["items"]}
        if mode == "copy":
            if not isinstance(destination, str) or not destination or "\x00" in destination:
                raise ValueError("请先选择分类目标文件夹")
            path = Path(destination)
            descriptor = open_directory(path)
            try:
                dest_identity = [os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino]
                available = os.free_bytes(descriptor)
            finally:
                os.close(descriptor)
            for record in self.media.records.values():
                if path == Path(record["path"]).parent or path in Path(record["path"]).parents:
                    raise ValueError("目标文件夹不能包含原媒体；请选择独立分类目录")
            # Also prevent placing the result inside a scanned tree.
            for root in getattr(self.media, "roots", []):
                if path == root or root in path.parents:
                    raise ValueError("目标文件夹不能位于扫描来源中")
        else:
            trash_helper()  # Capability errors happen before the confirmation page.
            destination, dest_identity, available = None, None, None
            self._check_kept_duplicates(ids, current)
        items = []
        for identifier in ids:
            record, info = self.media.validate(identifier)
            if mode == "trash" and record.get("source_signature") is None:
                raise ValueError("这份旧报告缺少文件身份记录，请重新扫描后再使用废纸篓 / 回收站清理")
            item = current.get(identifier)
            if item is None:
                raise ValueError("文件不属于整理清单")
            target = item["suggested_path"]
            if mode == "copy":
                if item["state"] != "include" or not item["selectable"]:
                    raise ValueError("复制前请先核对并纳入分类计划")
                check_target(destination, target)
            items.append({"id": identifier, "path": record["path"], "bytes": info.st_size,
                          "target": str(Path(destination) / target) if mode == "copy" else "系统废纸篓 / 回收站",
                          "relative": target, "signature": file_signature(info)})
        total = sum(item["bytes"] for item in items)
        if mode == "copy" and available < total:
            raise ValueError("目标磁盘可用空间不足")
        preview = {"token": secrets.token_urlsafe(24), "mode": mode, "items": items,
                   "bytes": total, "destination": destination, "dest_identity": dest_identity,
                   "available_bytes": available, "expires": time.monotonic() + 600}
        with self.lock:
            self.previews = {key: value for key, value in self.previews.items() if value["expires"] > time.monotonic()}
            self.previews[preview["token"]] = preview
        return {key: value for key, value in preview.items() if key not in {"expires", "dest_identity"}}

    def _save_job(self, job):
        directory = self.report_dir / "operations"
        if directory.is_symlink():
            raise ValueError("操作记录目录不能是符号链接")
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor = open_directory(directory)
        name = job["id"] + ".json"
        temporary = ".job-" + secrets.token_hex(12)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(job, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
            os.close(descriptor)

    def start(self, token):
        with self.lock:
            if not isinstance(token, str):
                raise ValueError("确认标识无效")
            preview = self.previews.get(token)
            if not preview or preview["expires"] < time.monotonic():
                raise ValueError("预览已过期，请重新预览")
            for item in preview["items"]:
                _, info = self.media.validate(item["id"])
                if file_signature(info) != item["signature"]:
                    raise ValueError("预览后文件发生变化")
            if preview["mode"] == "copy":
                descriptor = open_directory(preview["destination"])
                try:
                    if [os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino] != preview["dest_identity"]:
                        raise ValueError("目标文件夹自预览后发生变化")
                finally:
                    os.close(descriptor)
                current = {item["id"]: item for item in self.plan().snapshot()["items"]}
                for item in preview["items"]:
                    now = current[item["id"]]
                    if now["state"] != "include" or not now["selectable"] or now["suggested_path"] != item["relative"]:
                        raise ValueError("分类计划已变化，请重新预览")
                    check_target(preview["destination"], item["relative"])
            if not _BATCH_LOCK.acquire(blocking=False):
                raise ValueError("有文件操作正在执行，请等待完成")
            job = {"id": secrets.token_hex(12), "mode": preview["mode"], "status": "running",
                   "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": [], "total": len(preview["items"]),
                   "planned_items": [{key: item[key] for key in ("path", "target", "bytes")} for item in preview["items"]]}
            descriptor = None
            try:
                try:
                    descriptor = self._job_lock()
                except BlockingIOError as error:
                    raise ValueError("另一服务正在执行本次报告的文件操作，请等待完成") from error
                # A second service could edit the plan before we acquired its lock.
                if preview["mode"] == "copy":
                    current = {item["id"]: item for item in self.plan().snapshot()["items"]}
                    for item in preview["items"]:
                        now = current[item["id"]]
                        if now["state"] != "include" or not now["selectable"] or now["suggested_path"] != item["relative"]:
                            raise ValueError("分类计划已变化，请重新预览")
                os.ftruncate(descriptor, 0)
                os.write(descriptor, job["id"].encode("ascii"))
                os.fsync(descriptor)
                self._save_job(job)  # Refuse action if a journal cannot be written.
                self.previews.pop(token)
                self.jobs[job["id"]] = job
                _ACTIVE_JOBS[(self.report_key, job["id"])] = job
                threading.Thread(target=self._run, args=(job, preview, descriptor), daemon=True).start()
            except BaseException:
                _ACTIVE_JOBS.pop((self.report_key, job["id"]), None)
                self.jobs.pop(job["id"], None)
                if descriptor is not None:
                    os.close(descriptor)
                _BATCH_LOCK.release()
                raise
            return json.loads(json.dumps(job))

    def _run(self, job, preview, batch_descriptor):
        final_status = "stopped"
        def cancelled():
            requested = self._stop_requested(job["id"])
            if requested:
                with self.lock:
                    job["stop_requested"] = True
            return requested
        try:
            for item in preview["items"]:
                if cancelled():
                    break
                result = {"path": item["path"], "target": item["target"], "status": "processing", "bytes": item["bytes"]}
                with self.lock:
                    job["items"].append(result)
                    self._save_job(job)
                try:
                    if preview["mode"] == "copy":
                        descriptor = open_directory(preview["destination"])
                        try:
                            if [os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino] != preview["dest_identity"]:
                                raise ValueError("分类目标已被替换")
                        finally:
                            os.close(descriptor)
                        def progress(amount, phase):
                            with self.lock:
                                result.update(processed_bytes=amount, phase=phase)
                                self._save_job(job)
                        answer = copy_one(self.media, item["id"], preview["destination"], item["relative"], item["signature"], preview["dest_identity"], progress, cancelled)
                    else:
                        current = {record["id"]: record for record in self.plan().snapshot()["items"]}
                        self._check_kept_duplicates([entry["id"] for entry in preview["items"]], current)
                        if cancelled():
                            raise OperationCancelled("已停止，当前文件未移到废纸篓 / 回收站")
                        answer = trash_one(self.media, item["id"], item["signature"])
                    with self.lock:
                        result.update(answer, status="success")
                except OperationCancelled as error:
                    with self.lock:
                        result.update(status="cancelled", error=str(error))
                    break
                except OutcomeUnknown as error:
                    with self.lock:
                        result.update(status="unknown", error=str(error))
                    break
                except (OSError, ValueError) as error:
                    with self.lock:
                        result.update(status="failed", error=str(error))
                    break  # Keep successful items; do not automatically retry or remove copies.
                finally:
                    with self.lock:
                        self._save_job(job)
            with self.lock:
                success = all(item["status"] == "success" for item in job["items"])
                if len(job["items"]) == job["total"] and success:
                    final_status = "complete"
                elif (job.get("stop_requested") or any(item["status"] == "cancelled" for item in job["items"])) and not any(item["status"] in {"failed", "unknown"} for item in job["items"]):
                    final_status = "cancelled"
                else:
                    final_status = "stopped"
        except (OSError, ValueError) as error:
            with self.lock:
                final_status = "stopped"
                job.update(error="操作记录写入失败，先核对原文件和目标位置：" + str(error))
        finally:
            with self.lock:
                try:
                    job["status"] = final_status
                    self._save_job(job)
                except (OSError, ValueError) as error:
                    job.update(status="stopped", error="操作记录写入失败，请先核对原文件和目标位置：" + str(error))
                finally:
                    _ACTIVE_JOBS.pop((self.report_key, job["id"]), None)
                    os.close(batch_descriptor)
                    _BATCH_LOCK.release()

    def _read_job(self, identifier):
        descriptor = open_directory(self.report_dir / "operations")
        try:
            fd = os.open(identifier + ".json", FLAGS, dir_fd=descriptor)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_size > 4 * 1024 * 1024:
                    raise ValueError("操作记录无效")
                job = json.load(stream)
        finally:
            os.close(descriptor)
        if (not isinstance(job, dict) or job.get("id") != identifier
                or job.get("mode") not in {"copy", "trash"}
                or job.get("status") not in {"running", "complete", "stopped", "cancelled"}
                or type(job.get("total")) is not int or not 1 <= job["total"] <= 200
                or not isinstance(job.get("items"), list) or len(job["items"]) > job["total"]
                or any(not isinstance(item, dict) or item.get("status") not in {"processing", "success", "failed", "unknown", "cancelled"}
                       or not isinstance(item.get("path"), str) or not isinstance(item.get("target"), str)
                       for item in job["items"])):
            raise ValueError("操作记录无效")
        planned = job.get("planned_items")
        if planned is not None and (not isinstance(planned, list) or len(planned) != job["total"]
                or any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                       or not isinstance(item.get("target"), str) for item in planned)):
            raise ValueError("批次文件清单无效")
        started = {item["path"]: item["target"] for item in job["items"]}
        if len(started) != len(job["items"]):
            raise ValueError("操作记录包含重复文件")
        if planned is not None:
            targets = {item["path"]: item["target"] for item in planned}
            if len(targets) != len(planned) or any(targets.get(path) != target for path, target in started.items()):
                raise ValueError("批次文件清单与处理结果不一致")
        return job

    def snapshot(self, identifier):
        with self.lock:
            if not isinstance(identifier, str) or len(identifier) != 24 or any(char not in "0123456789abcdef" for char in identifier):
                raise ValueError("操作记录不存在")
            if identifier in self.jobs:
                job = json.loads(json.dumps(self.jobs[identifier]))
                if job["status"] == "running":
                    job["stop_requested"] = self._stop_requested(identifier)
                return job
            active = _ACTIVE_JOBS.get((self.report_key, identifier))
            if active is not None:
                job = json.loads(json.dumps(active))
                job["stop_requested"] = self._stop_requested(identifier)
                return job
            job = self._read_job(identifier)
            if job.get("status") == "running":
                if self._record_is_active(identifier):
                    job["status"] = "external_running"
                    job["stop_requested"] = self._stop_requested(identifier)
                    job["error"] = "另一服务正在执行此操作，进度会自动更新。请保持执行服务运行。"
                else:
                    # The owning process may have finished just before the lock probe.
                    job = self._read_job(identifier)
                    if job["status"] != "running":
                        return job
                    job["status"] = "interrupted"
                    job["error"] = "服务已停止，部分结果可能未确认。请核对原文件、目标位置或废纸篓 / 回收站后再处理。"
            return job

    def _history(self):
        directory = self.report_dir / "operations"
        try:
            descriptor = open_directory(directory)
        except FileNotFoundError:
            return {"jobs": [], "warnings": []}
        try:
            names = sorted((name[:-5] for name in os.listdir(descriptor) if name.endswith('.json')))
        finally:
            os.close(descriptor)
        jobs, warnings = [], []
        for identifier in names:
            try:
                jobs.append(self.snapshot(identifier))
            except (OSError, ValueError, TypeError) as error:
                warnings.append("记录 " + identifier + " 读取失败：" + str(error))
        jobs.sort(key=lambda job: (job["status"] in {"running", "external_running"}, str(job.get("created_at", ""))), reverse=True)
        return {"jobs": jobs, "warnings": warnings}

    def history(self):
        history = self._history()
        handled = {"copy": set(), "trash": set()}
        for job in history["jobs"]:
            for item in job["items"]:
                if item["status"] == "success":
                    handled[job["mode"]].add(item["path"])
        return {"jobs": history["jobs"][:20], "warnings": history["warnings"],
                "record_count": len(history["jobs"]),
                "handled": {mode: sorted(paths) for mode, paths in handled.items()}}

    def remaining(self, identifier):
        """Report-only selection advice; never retry or read original media here."""
        job = self.snapshot(identifier)
        if job["status"] not in {"cancelled", "stopped"} or job.get("error") or not job.get("planned_items"):
            raise ValueError("此批次不能直接继续核对；请先检查逐项记录、原文件与目标位置")
        history = self._history()
        if history["warnings"] or any(entry["status"] in {"running", "external_running"} for entry in history["jobs"]):
            raise ValueError("请等待正在执行的批次完成，或先核对无法读取的操作记录")
        completed, uncertain = set(), set()
        for entry in history["jobs"]:
            completed.update(item["path"] for item in entry["items"] if item["status"] == "success")
            uncertain.update(item["path"] for item in entry["items"] if item["status"] in {"processing", "unknown", "failed"})
            if entry.get("error") or entry["status"] == "interrupted":
                uncertain.update(item["path"] for item in entry.get("planned_items", []))
        current = {item["path"]: item for item in self.plan().snapshot()["items"]}
        results = {item["path"]: item["status"] for item in job["items"]}
        identifiers, skipped = [], []
        for item in job["planned_items"]:
            path, reason = item["path"], None
            record = current.get(path)
            if path in uncertain:
                reason = "曾失败或结果未确认，请先人工核对"
            elif path in completed:
                reason = "已有成功处理记录，跳过以避免重复操作"
            elif results.get(path) not in {None, "cancelled"}:
                reason = "此项不能作为未处理文件继续核对"
            elif record is None or record["id"] not in self.media.records:
                reason = "不在当前可操作媒体清单，请重新扫描"
            if reason:
                skipped.append({"path": path, "reason": reason})
            else:
                identifiers.append(record["id"])
        return {"ids": identifiers, "skipped": skipped, "mode": job["mode"],
                "message": "只勾选未开始或已安全取消的文件；请核对当前分类计划并重新预览，文件状态会在预览时检查。"}
