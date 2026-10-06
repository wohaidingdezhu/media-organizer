#!/usr/bin/env python3
"""只读媒体清点、SHA-256 精确查重、图片相似候选与分类预览。Python 3.9+。"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import csv
import datetime as dt
import hashlib
import html
import json
import queue
import portable_fs as os
from pathlib import Path, PurePosixPath
import re
import select
import stat
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
from library_server import load_tags, serve_library
import media_backend

BASE = Path(__file__).resolve().parent
IMAGE_EXT = set("jpg jpeg png heic heif tif tiff gif bmp webp avif dng cr2 cr3 nef arw raf orf rw2 pef srw".split())
RAW_EXT = set("dng cr2 cr3 nef arw raf orf rw2 pef srw".split())
VIDEO_EXT = set("mp4 mov m4v mkv avi wmv flv webm mpg mpeg mts m2ts ts 3gp vob rmvb rm asf".split())
PHOTO_SIDECAR_EXT = {"xmp", "aae"}
VIDEO_SIDECAR_EXT = {"srt", "ass", "ssa", "vtt", "sub", "idx", "nfo"}
SIDECAR_EXT = PHOTO_SIDECAR_EXT | VIDEO_SIDECAR_EXT
PACKAGE_EXT = {".photoslibrary", ".photolibrary", ".app", ".bundle", ".backupdb"}
SKIP_DIRS = {".git", ".Trash", ".Trashes", "node_modules", "__pycache__"}
DATALESS_FLAG = 0x40000000  # macOS UF_DATALESS: avoid downloading cloud placeholders.


def signature(st):
    from media_actions import file_signature
    return tuple(file_signature(st))


def inside(path, directory):
    return path == directory or directory in path.parents


def size_text(size):
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def issue(issues, path, reason):
    issues.append({"path": str(path), "reason": reason})


def normalize_roots(paths, output):
    roots = []
    for text in paths:
        selected = Path(text).expanduser()
        if selected.is_symlink() or getattr(selected.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError(f"扫描目录不能是符号链接：{selected}")
        path = selected.resolve(strict=True)
        if os.name == "nt":
            from file_operations import open_directory
            directory = open_directory(selected.absolute())
            os.close(directory)
        if not path.is_dir():
            raise ValueError(f"请选择文件夹：{path}")
        if any(folder.suffix.lower() in PACKAGE_EXT for folder in (path, *path.parents)):
            raise ValueError("请先从“照片”应用导出原片，再扫描导出的文件夹；不直接扫描资料库包。")
        if inside(path, output):
            raise ValueError(f"扫描目录不能位于报告目录内：{path}")
        if not any(inside(path, old) for old in roots):
            roots = [old for old in roots if not inside(old, path)]
            roots.append(path)
    return roots


def discover(roots, output, include_hidden, issues, folders=None, sidecars=None):
    records, identities = [], {}
    skipped = collections.Counter()
    for root in roots:
        def walk_error(error):
            issue(issues, error.filename or root, f"无法读取目录：{error.strerror}")
        for parent, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
            if folders is not None:
                folders.append(str(Path(parent)))
            kept = []
            for name in sorted(directories):
                path = Path(parent) / name
                try:
                    st = path.lstat()
                    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                        skipped["符号链接"] += 1
                    elif inside(path, output):
                        skipped["报告目录"] += 1
                    elif name in SKIP_DIRS or path.suffix.lower() in PACKAGE_EXT:
                        skipped["应用或照片资料库等目录"] += 1
                    elif not include_hidden and name.startswith("."):
                        skipped["隐藏项目"] += 1
                    elif getattr(st, "st_flags", 0) & DATALESS_FLAG:
                        issue(issues, path, "云端占位目录，未下载或扫描")
                    else:
                        kept.append(name)
                except OSError as error:
                    issue(issues, path, f"目录状态读取失败：{error}")
            directories[:] = kept
            for name in sorted(files):
                path = Path(parent) / name
                if not include_hidden and name.startswith("."):
                    skipped["隐藏项目"] += 1
                    continue
                ext = path.suffix.lower().lstrip(".")
                is_sidecar = sidecars is not None and ext in SIDECAR_EXT
                if ext not in IMAGE_EXT | VIDEO_EXT and not is_sidecar:
                    skipped["非支持的媒体扩展名"] += 1
                    continue
                try:
                    st = path.lstat()
                    if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                        skipped["符号链接或特殊文件"] += 1
                        continue
                    if getattr(st, "st_flags", 0) & DATALESS_FLAG:
                        issue(issues, path, "云端占位文件，未下载或读取")
                        continue
                    if is_sidecar:
                        sidecars.append({"path": str(path), "extension": ext, "bytes": st.st_size})
                        continue
                    identity = (st.st_dev, st.st_ino)
                    record = {
                        "path": str(path), "name": name, "root": str(root),
                        "relative": str(path.relative_to(root)),
                        "kind": "照片" if ext in IMAGE_EXT else "视频", "extension": ext,
                        "bytes": st.st_size, "mtime": st.st_mtime,
                        "sha256": "", "hash_status": "未处理",
                        "hardlink_to": identities.get(identity, ""),
                        "suggested_path": "", "reason": "", "date_source": "",
                        "_signature": signature(st),
                    }
                    identities.setdefault(identity, str(path))
                    records.append(record)
                    if len(records) % 1000 == 0:
                        print(f"已发现 {len(records):,} 个媒体文件…", flush=True)
                except OSError as error:
                    issue(issues, path, f"文件状态读取失败：{error}")
    return records, dict(skipped)


def full_hash(record):
    """Always read full bytes; reject files changed since discovery or during reading."""
    descriptor = os.open(record["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        initial = os.fstat(source.fileno())
        if not stat.S_ISREG(initial.st_mode) or signature(initial) != record["_signature"]:
            raise ValueError("文件自扫描开始后发生变化，已排除查重")
        digest = hashlib.sha256()
        amount, last_update = 0, time.monotonic()
        while True:
            chunk = source.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            amount += len(chunk)
            if time.monotonic() - last_update > 3:
                print(f"  {record['name']}：已读取 {size_text(amount)} / {size_text(initial.st_size)}", flush=True)
                last_update = time.monotonic()
        if signature(os.fstat(source.fileno())) != record["_signature"] or amount != initial.st_size:
            raise ValueError("读取过程中发生变化，已排除查重")
    if signature(os.stat(record["path"], follow_symlinks=False)) != record["_signature"]:
        raise ValueError("读取后文件已变化，已排除查重")
    return digest.hexdigest()


def exact_duplicates(records, issues):
    by_size, by_digest = collections.defaultdict(list), collections.defaultdict(list)
    for record in records:
        if record["hardlink_to"]:
            record["hash_status"] = "硬链接：同一物理文件的另一入口"
        elif not record["bytes"]:
            record["hash_status"] = "空文件：不计入重复"
            issue(issues, record["path"], "空媒体文件，请人工检查")
        else:
            by_size[record["bytes"]].append(record)
    candidates = [r for group in by_size.values() if len(group) > 1 for r in group]
    print(f"媒体清单 {len(records):,} 项；需完整校验 {len(candidates):,} 个同大小文件（共 {size_text(sum(r['bytes'] for r in candidates))}）。", flush=True)
    for group in by_size.values():
        if len(group) == 1:
            group[0]["hash_status"] = "大小唯一：无需哈希即可排除精确重复"
    for index, record in enumerate(candidates, 1):
        print(f"内容校验 {index}/{len(candidates)}：{record['name']}", flush=True)
        try:
            record["sha256"] = full_hash(record)
            record["hash_status"] = "完整 SHA-256 已校验"
            by_digest[(record["bytes"], record["sha256"])].append(record)
        except (OSError, ValueError) as error:
            record["hash_status"] = "读取失败或文件变化：未确认"
            issue(issues, record["path"], str(error))
    return [{"sha256": key[1], "bytes_each": key[0], "paths": [r["path"] for r in group],
             "redundant_logical_bytes": key[0] * (len(group) - 1)}
            for key, group in by_digest.items() if len(group) > 1]


def video_header_matches(extension, header):
    """Recognize common container signatures; this cannot prove playability."""
    image_brands = {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
    if extension in {"mp4", "m4v", "3gp"}:
        return len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12].lower() not in image_brands | {b"m4a "}
    if extension == "mov":
        if len(header) < 8:
            return False
        if header[4:8] == b"ftyp":
            return len(header) >= 12 and header[8:12].lower() not in image_brands | {b"m4a "}
        return header[4:8] in {b"moov", b"mdat", b"wide", b"free"}
    if extension in {"mkv", "webm"}:
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == "avi":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI "
    if extension == "flv":
        return header.startswith(b"FLV\x01")
    return None


def inspect_video_headers(records, issues, enabled):
    """Optionally read only the first bytes of supported video containers."""
    summary = {"enabled": enabled, "checked": 0, "recognized": 0, "unrecognized": 0}
    by_path = {record["path"]: record for record in records}
    for record in records:
        if record["kind"] != "视频":
            continue
        record["video_header_status"] = "未检查"
        if not enabled:
            continue
        if record["hardlink_to"]:
            original = by_path[record["hardlink_to"]]
            record["video_header_status"] = "硬链接：" + original.get("video_header_status", "原入口未检查")
            continue
        if not record["bytes"]:
            record["video_header_status"] = "空文件：未检查"
            continue
        if video_header_matches(record["extension"], b"") is None:
            record["video_header_status"] = "未检查：没有此格式的轻量文件头规则"
            continue
        try:
            descriptor = os.open(record["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or signature(before) != record["_signature"]:
                    raise ValueError("视频文件已变化，文件头未确认")
                header = source.read(32)
                if signature(os.fstat(source.fileno())) != record["_signature"]:
                    raise ValueError("读取视频文件头时文件发生变化")
            if signature(os.stat(record["path"], follow_symlinks=False)) != record["_signature"]:
                raise ValueError("读取视频文件头后文件发生变化")
            summary["checked"] += 1
            if video_header_matches(record["extension"], header):
                record["video_header_status"] = "识别到常见容器文件头（不保证可播放）"
                summary["recognized"] += 1
            else:
                record["video_header_status"] = "未识别常见容器文件头：需人工核对"
                summary["unrecognized"] += 1
                issue(issues, record["path"], "视频扩展名与常见容器文件头未匹配；不能据此判定无法播放")
        except (OSError, ValueError) as error:
            record["video_header_status"] = "读取失败或文件变化：未确认"
            issue(issues, record["path"], f"视频文件头未检查：{error}")
    return summary


def helper_available(helper):
    try:
        return (stat.S_ISREG(helper.lstat().st_mode) and media_backend.portable_available(helper)
                and (helper.suffix == ".py" or os.access(helper, os.X_OK)))
    except OSError:
        return False


class ImageProbeWorker:
    """Reuse one native ImageIO process, with a deadline for each request."""

    def __init__(self, helper):
        self.helper = helper
        self.process = None
        self._stdout_buffer = b""
        self.lock = threading.Lock()

    def close(self, force=False):
        process, self.process = self.process, None
        self._stdout_buffer = b""
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            if force:
                process.kill()
                process.wait()
            else:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        process.stdout.close()

    def _read_response(self, timeout):
        if sys.platform == "win32":
            try:
                line = self._responses.get(timeout=timeout)
            except queue.Empty as error:
                raise subprocess.TimeoutExpired(str(self.helper), timeout) from error
            if isinstance(line, Exception):
                raise line
            if not line or len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                raise OSError("图片解析进程提前退出或响应过长")
            return line
        deadline = time.monotonic() + timeout
        maximum = 1024 * 1024
        while b"\n" not in self._stdout_buffer:
            if len(self._stdout_buffer) > maximum:
                raise ValueError("图片解析响应过长")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(str(self.helper), timeout)
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not readable:
                raise subprocess.TimeoutExpired(str(self.helper), timeout)
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise OSError("图片解析进程提前退出")
            self._stdout_buffer += chunk
        line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
        if len(line) > maximum:
            raise ValueError("图片解析响应过长")
        return line

    def request(self, path, thumbnail=None, timeout=25):
        with self.lock:
            if self.process is None:
                if not helper_available(self.helper):
                    raise OSError("图片解析程序不存在、不可执行或是符号链接")
                self.process = subprocess.Popen([*media_backend.command(self.helper), "--batch"], stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if sys.platform == "win32":
                    responses, output = queue.Queue(), self.process.stdout
                    self._responses = responses
                    def read_lines():
                        try:
                            while True:
                                line = output.readline(1024 * 1024 + 1)
                                responses.put(line)
                                if not line or len(line) > 1024 * 1024:
                                    break
                        except (OSError, ValueError) as error:
                            responses.put(error)
                    threading.Thread(target=read_lines, daemon=True).start()
            request = {"path": path}
            if thumbnail is not None:
                request["thumbnail"] = thumbnail
            try:
                self.process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                self.process.stdin.flush()
                result = json.loads(self._read_response(timeout))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.close(force=True)
                raise
            if not isinstance(result, dict) or result.get("path") != path:
                self.close(force=True)
                raise ValueError("图片解析结果路径不匹配")
            if result.get("error"):
                raise ValueError(result["error"])
            return result


def inspect_images(records, helper, issues, enabled):
    available = helper_available(helper)
    if not enabled or not available:
        return {"available": available, "enabled": enabled, "inspected": 0,
                "note": "照片日期使用文件名或修改时间；图片相似检测未运行。"}
    images = [r for r in records if r["kind"] == "照片" and not r["hardlink_to"] and r["bytes"]]
    success = 0
    workers = [ImageProbeWorker(helper) for _ in range(2)]
    def inspect_one(item):
        worker, record = item
        try:
            before = os.stat(record["path"], follow_symlinks=False)
            if signature(before) != record["_signature"]:
                raise ValueError("文件已变化，未读取照片信息")
            metadata = worker.request(record["path"])
            if signature(os.stat(record["path"], follow_symlinks=False)) != record["_signature"]:
                raise ValueError("读取照片信息时文件发生变化")
            return metadata, ""
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return None, str(error)
    # Keep only a few requests queued so Ctrl+C does not wait for a large backlog.
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for start in range(0, len(images), 4):
                batch = images[start:start + 4]
                assignments = [(workers[(start + offset) % 2], record) for offset, record in enumerate(batch)]
                for index, (record, (metadata, error)) in enumerate(zip(batch, pool.map(inspect_one, assignments)), start + 1):
                    if index == 1 or index % 50 == 0 or index == len(images):
                        print(f"读取照片信息 {index}/{len(images)}…", flush=True)
                    if error:
                        issue(issues, record["path"], f"照片信息未读取，仍保留文件查重结果：{error}")
                    else:
                        record["image"] = metadata
                        success += 1
    finally:
        for worker in workers:
            worker.close()
    return {"available": True, "enabled": True, "inspected": success,
            "note": "使用 ImageIO 或 Pillow；动画仅比较第一帧，支持格式取决于已安装的解码器。"}


def month_for(record):
    metadata = record.get("image", {})
    original = metadata.get("date_original") or ""
    # Only EXIF original is confidently treated as capture date; TIFF may be edited time.
    if metadata.get("date_source") == "exif_original":
        try:
            return dt.datetime.strptime(original[:10], "%Y:%m:%d").strftime("%Y/%m"), "EXIF 拍摄日期"
        except ValueError:
            pass
    match = re.search(r"(?<!\d)((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)|(?<!\d)((?:19|20)\d{2})([01]\d)([0-3]\d)(?=\d{6}(?:\D|$))", record["name"])
    if match:
        parts = match.groups()[:3] if match.group(1) else match.groups()[3:]
        try:
            return dt.date(*map(int, parts)).strftime("%Y/%m"), "文件名日期（推测）"
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(record["mtime"]).strftime("%Y/%m"), "文件修改时间（回退，非拍摄日期）"


def safe_segment(text):
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", text).strip(" .")
    text = text[:100].rstrip(" .") or "待整理"
    from organization_plan import invalid_windows_name
    return "_" + text if invalid_windows_name(text) else text


def related_folders(folders, records, sidecars=()):
    """Compare scanned media in repeated-name folders using existing full hashes."""
    counts = {path: {"path": path, "media_files": 0, "sidecar_files": 0, "logical_bytes": 0} for path in folders}
    folder_media = collections.defaultdict(list)
    for record in records:
        root = Path(record["root"])
        parent = Path(record["path"]).parent
        while inside(parent, root):
            details = counts.get(str(parent))
            if details is not None:
                details["media_files"] += 1
                details["logical_bytes"] += record["bytes"]
                folder_media[str(parent)].append(record)
            if parent == root:
                break
            parent = parent.parent
    for sidecar in sidecars:
        parent = Path(sidecar["path"]).parent
        while True:
            details = counts.get(str(parent))
            if details is None:
                break
            details["sidecar_files"] += 1
            parent = parent.parent
    groups = collections.defaultdict(list)
    for path, details in counts.items():
        name = Path(path).name
        groups[unicodedata.normalize("NFC", safe_segment(name)).casefold()].append((name, details))
    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        names = {unicodedata.normalize("NFC", name).casefold() for name, _ in members}
        by_content = collections.defaultdict(list)
        for _, details in members:
            media = folder_media[details["path"]]
            if not media:
                details["content_check"] = "未确认：没有已扫描媒体"
            elif any(not record.get("sha256") for record in media):
                details["content_check"] = "未确认：部分媒体没有完整 SHA-256"
            else:
                details["content_check"] = "已校验：未发现已确认的同内容文件夹"
                fingerprint = tuple(sorted((record["bytes"], record["sha256"]) for record in media))
                by_content[fingerprint].append(details)
            details["content_match_example"] = ""
        # An ancestor and its descendant can contain the very same files; that is
        # not evidence of two independent folders with matching media contents.
        for matching in by_content.values():
            ordered = sorted(((Path(item["path"]), item) for item in matching), key=lambda pair: pair[0].parts)
            leaves = [path for index, (path, _) in enumerate(ordered)
                      if index + 1 == len(ordered) or not inside(ordered[index + 1][0], path)]
            if len(leaves) < 2:
                continue
            first, last = leaves[0], leaves[-1]
            for path, details in ordered:
                for example in (first, last):
                    if not inside(path, example) and not inside(example, path):
                        details["content_match_example"] = str(example)
                        break
        for _, details in members:
            if details["content_match_example"]:
                details["content_check"] = "已确认：已扫描媒体的字节内容集合一致"
        result.append({"type": "同名" if len(names) == 1 else "整理后名称冲突",
                       "name": members[0][0], "folders": [details for _, details in members]})
    return sorted(result, key=lambda group: (-sum(item["media_files"] for item in group["folders"]),
                                              group["name"].casefold()))


def source_folder_labels(records):
    """Keep different source folders distinct in folder-based video suggestions."""
    roots = {Path(record["root"]) for record in records}
    paths = set()
    for record in records:
        root = Path(record["root"])
        parent = Path(record["path"]).parent
        while inside(parent, root):
            paths.add(parent)
            if parent == root:
                break
            parent = parent.parent
    grouped = collections.defaultdict(list)
    for path in paths:
        parent_key = None if path in roots else str(path.parent)
        label = safe_segment(path.name)
        grouped[(parent_key, unicodedata.normalize("NFC", label).casefold())].append(path)
    labels, changed = {}, set()
    for members in grouped.values():
        for path in members:
            label = safe_segment(path.name)
            if len(members) > 1:
                token = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
                label = label[:90].rstrip(" .") + "__" + token
                changed.add(path)
            labels[path] = label
    return labels, changed


def video_id(stem):
    match = re.search(r"(?i)(?<![A-Z0-9])FC2[-_ ]?(?:PPV[-_ ]?)?(\d{5,9})(?!\d)", stem)
    if match:
        return "FC2-PPV-" + match.group(1)
    match = re.search(r"(?i)(?<![A-Z0-9])([A-Z]{2,10})[-_ ](\d{3,7})(?!\d)", stem)
    if match and match.group(1).upper() not in {"IMG", "DSC", "DSCF", "DSCN", "MOV", "VID", "PXL", "MVI", "GOPR", "VIDEO", "SCREENSHOT"}:
        return match.group(1).upper() + "-" + match.group(2)
    return ""


def video_title(stem):
    """A conservative display/grouping title after common part and release tags."""
    title = re.sub(r"(?i)[\[【(（](?:2160p|1080p|720p|4k|uhd|bluray|bdrip|webrip|web-dl|x264|x265|h264|h265|hevc|av1|hdr|dv|(?:part|cd|disc|pt)\s*\d+)[\]】)）]", " ", stem)
    title = re.sub(r"(?i)(?<![A-Z0-9])(?:2160p|1080p|720p|4k|uhd|bluray|bdrip|webrip|web-dl|x264|x265|h264|h265|hevc|av1|hdr|dv|(?:part|cd|disc|pt)[-_ ]*\d+)(?![A-Z0-9])", " ", title)
    title = re.sub(r"[._\-]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def video_variant(stem):
    """Only describe filename clues; never infer actual subtitles or playable parts."""
    labels = []
    if video_id(stem) and re.search(r"(?i)(?:[-_. ](?:C|CH))(?=$|[-_. ])", stem):
        labels.append("字幕变体标记（文件名）")
    match = re.search(r"(?i)(?:^|[-_. ])(?:CD|DISC|PART|PT)[-_. ]?(\d{1,2})(?=$|[-_. ])", stem)
    if match:
        labels.append(f"第 {int(match.group(1))} 段标记（文件名）")
    return "；".join(labels)


def associate_sidecars(sidecars, records):
    """Match same-directory filenames only, leaving uncertain matches for review."""
    index = collections.defaultdict(list)
    def normalized(value):
        return unicodedata.normalize("NFC", value).casefold()
    for record in records:
        path = Path(record["path"])
        index[(str(path.parent), normalized(path.stem), record["kind"])].append(record)
    result = []
    for sidecar in sidecars:
        path = Path(sidecar["path"])
        kind = "照片" if sidecar["extension"] in PHOTO_SIDECAR_EXT else "视频"
        expected = IMAGE_EXT if kind == "照片" else VIDEO_EXT
        stems = [path.stem]
        if kind == "视频":
            # Try the literal stem before removing language/accessibility suffixes.
            suffix = r"(?i)[._-](?:zh(?:[-_]?(?:cn|tw|hk|hans|hant))?|chs|cht|en|eng|ja|jpn|ko|kor|forced|sdh|cc)$"
            for _ in range(2):
                reduced = re.sub(suffix, "", stems[-1])
                if reduced == stems[-1]:
                    break
                stems.append(reduced)
        matches = []
        for candidate in stems:
            embedded_extension = Path(candidate).suffix.lower().lstrip(".")
            media_stem = Path(candidate).stem if embedded_extension in expected else candidate
            found = index.get((str(path.parent), normalized(media_stem), kind), [])
            if embedded_extension in expected:
                found = [item for item in found if item["extension"] == embedded_extension]
            if found:
                matches = found
                break
        matches = sorted(matches, key=lambda item: item["path"])
        status = "已关联" if len(matches) == 1 else "多项候选：需人工确认" if matches else "未关联"
        result.append({**sidecar, "status": status, "media_paths": [item["path"] for item in matches],
                       "media_suggested_path": matches[0]["suggested_path"] if len(matches) == 1 else ""})
    return result


def related_videos(records):
    """Filename-based review groups; these are never treated as duplicates."""
    groups = collections.defaultdict(list)
    for record in records:
        if record["kind"] != "视频" or record["hardlink_to"]:
            continue
        identifier = video_id(Path(record["name"]).stem)
        if identifier:
            key = ("编号", identifier)
        else:
            title = video_title(Path(record["name"]).stem)
            # Short/common fragments create noisy cross-folder matches.
            if len(re.sub(r"\W", "", title, flags=re.UNICODE)) < 4:
                continue
            key = ("名称", title.casefold())
        groups[key].append(record)
    result = []
    for (kind, key), members in groups.items():
        if len(members) < 2:
            continue
        result.append({"type": kind, "label": key if kind == "编号" else video_title(Path(members[0]["name"]).stem),
                       "files": [{"path": r["path"], "bytes": r["bytes"], "extension": r["extension"],
                                  "variant": video_variant(Path(r["name"]).stem),
                                  "modified_at": dt.datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M") if "mtime" in r else "",
                                  "hash_status": r.get("hash_status", ""), "sha256": r.get("sha256", ""),
                                  "video_header_status": r.get("video_header_status", "未检查")}
                                 for r in members]})
    return sorted(result, key=lambda g: (-len(g["files"]), g["type"], g["label"].casefold()))


def build_video_library(records, sidecars, duplicates, issues, folder_groups, saved_tags=None):
    """Build a read-only, per-scan film index from already collected facts."""
    videos = [record for record in records if record["kind"] == "视频"]
    video_paths = {record["path"] for record in videos}
    by_path_issues = collections.defaultdict(list)
    review = []
    for item in issues:
        if item["path"] in video_paths:
            by_path_issues[item["path"]].append(item["reason"])
            review.append({"path": item["path"], "reason": item["reason"], "type": "视频"})
    duplicate_numbers = {}
    for number, group in enumerate(duplicates, 1):
        for path in group["paths"]:
            if path in video_paths:
                duplicate_numbers[path] = number
                review.append({"path": path, "reason": f"精确重复第 {number} 组；需人工核对保留哪份", "type": "精确重复"})
    attachments = collections.defaultdict(list)
    for item in sidecars:
        if item["status"] == "已关联" and item["media_paths"][0] in video_paths:
            attachments[item["media_paths"][0]].append(item["path"])
        elif item["extension"] in VIDEO_SIDECAR_EXT:
            review.append({"path": item["path"], "reason": "附属文件" + item["status"], "type": "附属文件"})
    video_folders = set()
    for record in videos:
        parent = Path(record["path"]).parent
        root = Path(record["root"])
        while inside(parent, root):
            video_folders.add(str(parent))
            if parent == root:
                break
            parent = parent.parent
    for group in folder_groups:
        if any(folder["path"] in video_folders for folder in group["folders"]):
            review.append({"path": "；".join(folder["path"] for folder in group["folders"]),
                           "reason": f"{group['type']}文件夹候选，需核对目录内容", "type": "文件夹"})
    saved_tags = saved_tags or {}
    grouped = collections.defaultdict(list)
    labels = {}
    for record in videos:
        path = Path(record["path"])
        identifier = video_id(path.stem)
        title = identifier or video_title(path.stem) or path.stem
        # Titles without an identifier are only joined within the same source folder.
        key = ("编号", identifier) if identifier else ("原目录名称", str(path.parent), unicodedata.normalize("NFC", title).casefold())
        labels[key] = ("编号" if identifier else "原目录名称", title)
        grouped[key].append({"path": record["path"], "extension": record["extension"],
                             "bytes": record["bytes"], "modified_at": dt.datetime.fromtimestamp(record["mtime"]).strftime("%Y-%m-%d %H:%M"),
                             "suggested_path": record["suggested_path"], "hash_status": record["hash_status"],
                             "video_header_status": record.get("video_header_status", "未检查"),
                             "sidecars": sorted(attachments[record["path"]]),
                             "issues": by_path_issues[record["path"]],
                             "duplicate_group": duplicate_numbers.get(record["path"], 0)})
    groups = []
    for key, files in grouped.items():
        kind, title = labels[key]
        tag_key = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()
        groups.append({"type": kind, "title": title, "tag_key": tag_key, "tags": saved_tags.get(tag_key, []),
                       "files": sorted(files, key=lambda item: item["path"]),
                       "needs_review": any(item["issues"] or item["duplicate_group"] for item in files),
                       "has_sidecars": any(item["sidecars"] for item in files)})
    groups.sort(key=lambda group: (group["title"].casefold(), group["files"][0]["path"]))
    return {"groups": groups, "issues": review, "video_files": len(videos),
            "duplicate_files": len(duplicate_numbers)}


def classify(records, video_rule):
    image_stems = collections.defaultdict(list)
    for record in records:
        if record["kind"] == "照片":
            image_stems[(str(Path(record["path"]).parent), Path(record["name"]).stem.casefold())].append(record)
    by_path = {r["path"]: r for r in records}
    folder_labels, renamed_folders = source_folder_labels(records)
    for record in records:
        if record["hardlink_to"]:
            record["image"] = by_path[record["hardlink_to"]].get("image", {})
        path = Path(record["path"])
        month, source = month_for(record)
        record["date_source"] = source
        filename = safe_segment(path.stem) + path.suffix.lower()
        if record["kind"] == "照片":
            category = "RAW" if record["extension"] in RAW_EXT else "动图" if record["extension"] == "gif" else "照片"
            if re.search(r"(?i)screenshot|screen[ _-]?shot|屏幕快照|截[图屏]", record["name"]):
                category = "截图"
            target = f"照片/{month}/{category}/{filename}"
            reason = f"{source}；类型根据扩展名或截图文件名推测"
        else:
            paired_images = image_stems.get((str(path.parent), path.stem.casefold()), [])
            paired = paired_images[0] if len(paired_images) == 1 else None
            identifier = video_id(path.stem) if video_rule == "auto" else ""
            if paired and record["extension"] in {"mov", "mp4", "m4v"} and video_rule == "auto":
                month, source = month_for(paired)
                record["date_source"] = source
                target = f"照片/{month}/照片/{filename}"
                reason = "与照片同目录同名，可能是实况照片配对视频，需人工确认"
            elif identifier:
                target = f"视频/编号/{identifier}/{filename}"
                reason = "从名称推测编号；保留分段、字幕和画质标记；同编号不代表重复"
            elif video_rule == "folder" or (video_rule == "auto" and path.parent != Path(record["root"])):
                source_folders = [Path(record["root"])]
                for part in Path(record["relative"]).parts[:-1]:
                    source_folders.append(source_folders[-1] / part)
                labels = [folder_labels[folder] for folder in source_folders]
                target = "视频/原目录/" + "/".join(labels + [filename])
                reason = "保留来源目录层级（可用 --video-rule name 改为名称归组）"
                if any(folder in renamed_folders for folder in source_folders):
                    reason += "；来源文件夹名称冲突，已添加路径标识避免混合"
            else:
                title = video_title(path.stem)
                target = f"视频/名称/{safe_segment(title)}/{filename}"
                reason = "按文件名推测作品名，需人工核对"
            if len(paired_images) > 1 and record["extension"] in {"mov", "mp4", "m4v"} and video_rule == "auto":
                reason += "；同目录同名照片不唯一，未自动判断实况照片配对"
        record["suggested_path"], record["reason"] = target, reason
    occupied = set()
    for record in records:
        original = PurePosixPath(record["suggested_path"])
        proposal, index = str(original), 1
        while unicodedata.normalize("NFC", proposal).casefold() in occupied:
            token = hashlib.sha256(record["path"].encode()).hexdigest()[:8]
            suffix = f"__{token}" + (f"_{index}" if index > 1 else "")
            proposal = str(original.with_name(original.stem + suffix + original.suffix))
            index += 1
        if proposal != str(original):
            record["reason"] += "；建议目标重名，已添加路径标识避免覆盖"
        occupied.add(unicodedata.normalize("NFC", proposal).casefold())
        record["suggested_path"] = proposal


class BKTree:
    def __init__(self):
        self.root = None

    def add(self, value, record):
        if self.root is None:
            self.root = [value, [record], {}]
            return
        node = self.root
        while True:
            distance = bin(value ^ node[0]).count("1")
            if distance == 0:
                node[1].append(record)
                return
            if distance not in node[2]:
                node[2][distance] = [value, [record], {}]
                return
            node = node[2][distance]

    def query(self, value, threshold):
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop()
            distance = bin(value ^ node[0]).count("1")
            if distance <= threshold:
                for record in node[1]:
                    yield distance, record
            stack.extend(child for d, child in node[2].items() if distance - threshold <= d <= distance + threshold)


def similar_images(records, threshold, limit, enabled):
    pairs, tree = [], BKTree()
    eligible = [r for r in records if not r["hardlink_to"] and r.get("image", {}).get("dhash")
                and not r["image"].get("low_detail") and r["image"].get("width") and r["image"].get("height")]
    if not enabled:
        return {"pairs": [], "eligible": len(eligible), "enabled": False, "truncated": False}
    for record in eligible:
        value = int(record["image"]["dhash"], 16)
        ratio = record["image"]["width"] / record["image"]["height"]
        for distance, other in tree.query(value, threshold):
            if record["sha256"] and record["sha256"] == other["sha256"]:
                continue
            other_ratio = other["image"]["width"] / other["image"]["height"]
            if abs(ratio - other_ratio) / max(ratio, other_ratio) > 0.08:
                continue
            if len(pairs) >= limit:
                return {"pairs": pairs, "eligible": len(eligible), "enabled": True, "truncated": True}
            pairs.append({"left": other["path"], "right": record["path"], "distance": distance})
        tree.add(value, record)
    return {"pairs": pairs, "eligible": len(eligible), "enabled": True, "truncated": False}


def csv_cell(value):
    # Prevent spreadsheet formula execution in untrusted filenames and metadata.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, fields, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([label for _, label in fields])
        for row in rows:
            writer.writerow([csv_cell(row.get(key, "")) for key, _ in fields])


def export_previews(directory, records, similarity, helper, issues, displayed_pairs=300, photo_limit=500):
    """Create report-local previews only for images shown in the HTML report."""
    paths = dict.fromkeys(path for pair in similarity["pairs"][:displayed_pairs]
                          for path in (pair["left"], pair["right"]))
    paths.update(dict.fromkeys(record["path"] for record in records if record.get("kind") == "照片") if photo_limit is None else
                 dict.fromkeys([record["path"] for record in records if record.get("kind") == "照片"][:photo_limit]))
    if not paths:
        return {}
    by_path = {record["path"]: record for record in records}
    preview_dir = directory / "previews"
    preview_dir.mkdir(mode=0o700)
    previews = {}
    workers = [ImageProbeWorker(helper) for _ in range(2)]
    def export_one(item):
        index, path = item
        record = by_path[path]
        destination = preview_dir / f"image-{index:05d}.png"
        try:
            before = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or signature(before) != record["_signature"]:
                raise ValueError("图片已变化，未生成预览")
            workers[(index - 1) % 2].request(path, thumbnail=str(destination))
            after = os.stat(path, follow_symlinks=False)
            if signature(after) != record["_signature"] or not destination.is_file():
                raise ValueError("图片在生成预览时发生变化")
            return path, f"previews/{destination.name}", ""
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            return path, "", str(error)
    items = list(enumerate(paths, 1))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for start in range(0, len(items), 4):
                for path, relative, error in pool.map(export_one, items[start:start + 4]):
                    if error:
                        issue(issues, path, f"图片浏览预览未生成：{error}")
                    else:
                        previews[path] = relative
    finally:
        for worker in workers:
            worker.close()
    return previews


def local_cover_candidates(groups, records):
    """Prefer exact or identifier images; only use generic folder art for one work."""
    photos = collections.defaultdict(list)
    for record in records:
        if record["kind"] == "照片":
            path = Path(record["path"])
            photos[(str(path.parent), unicodedata.normalize("NFC", path.stem).casefold())].append(record)
    extension_order = {name: index for index, name in enumerate(("jpg", "jpeg", "png", "webp", "heic", "heif", "tif", "tiff"))}
    folder_groups = collections.defaultdict(set)
    for index, group in enumerate(groups):
        for file in group["files"]:
            folder_groups[str(Path(file["path"]).parent)].add(index)
    result = []
    for index, group in enumerate(groups):
        candidates, seen = [], set()
        folders = list(dict.fromkeys(str(Path(file["path"]).parent) for file in group["files"]))
        stems = [group["title"]] if group["type"] == "编号" else []
        stems += [Path(file["path"]).stem for file in group["files"]]
        stems += [stem + suffix for stem in list(stems) for suffix in ("-poster", "-cover")]
        for folder in folders:
            for stem in stems:
                for photo in sorted(photos.get((folder, unicodedata.normalize("NFC", stem).casefold()), []),
                                    key=lambda item: (extension_order.get(item["extension"], 99), item["path"])):
                    if photo["path"] not in seen:
                        candidates.append(photo)
                        seen.add(photo["path"])
            if len(folder_groups[folder]) == 1:
                for stem in ("poster", "folder", "cover", "封面"):
                    for photo in sorted(photos.get((folder, stem.casefold()), []),
                                        key=lambda item: (extension_order.get(item["extension"], 99), item["path"])):
                        if photo["path"] not in seen:
                            candidates.append(photo)
                            seen.add(photo["path"])
        result.append(candidates)
    return result


def export_local_covers(directory, groups, records, helper, issues, enabled=True):
    for group in groups:
        group["poster"] = ""
        group["poster_source"] = ""
    candidates = local_cover_candidates(groups, records)
    if not enabled or not any(candidates) or not helper_available(helper):
        return 0
    cover_dir = directory / "covers"
    cover_dir.mkdir(mode=0o700)
    workers = [ImageProbeWorker(helper) for _ in range(2)]
    count = 0
    def export_one(item):
        index, options = item
        destination = cover_dir / f"poster-{index + 1:05d}.png"
        for record in options:
            try:
                before = os.stat(record["path"], follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or signature(before) != record["_signature"]:
                    raise ValueError("封面图片自扫描后发生变化")
                workers[index % 2].request(record["path"], thumbnail=str(destination))
                after = os.stat(record["path"], follow_symlinks=False)
                if signature(after) != record["_signature"] or not destination.is_file():
                    raise ValueError("封面图片导出时发生变化")
                return index, f"covers/{destination.name}", record["path"], ""
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
                last_error = f"本地封面未生成：{error}"
        return index, "", "", last_error if options else ""
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for index, relative, source, error in pool.map(export_one, enumerate(candidates)):
                groups[index]["poster"] = relative
                groups[index]["poster_source"] = source
                if relative:
                    count += 1
                elif error:
                    issue(issues, candidates[index][0]["path"], error)
    finally:
        for worker in workers:
            worker.close()
    return count


def export_video_frame_covers(directory, groups, records, helper, issues, limit=500, enabled=True):
    """Use macOS AVFoundation for missing posters, bounded by group count."""
    if not enabled or not helper_available(helper):
        return 0
    by_path = {record["path"]: record for record in records if record["kind"] == "视频"}
    supported = VIDEO_EXT if helper.suffix == ".py" else {"mp4", "mov", "m4v", "3gp", "mpg", "mpeg"}
    pending = []
    for index, group in enumerate(groups):
        if group["poster"]:
            continue
        options = [by_path[file["path"]] for file in group["files"]
                   if file["extension"] in supported and not by_path[file["path"]]["hardlink_to"]]
        if options:
            if len(pending) >= limit:
                group["poster_status"] = "超过本次视频截帧数量上限"
            else:
                pending.append((index, options))
    if not pending:
        return 0
    cover_dir = directory / "covers"
    cover_dir.mkdir(mode=0o700, exist_ok=True)
    workers = [ImageProbeWorker(helper) for _ in range(2)]
    def export_one(item):
        index, options = item
        destination = cover_dir / f"frame-{index + 1:05d}.png"
        last_error = ""
        for record in options:
            try:
                before = os.stat(record["path"], follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or signature(before) != record["_signature"]:
                    raise ValueError("视频自扫描后发生变化")
                workers[index % 2].request(record["path"], thumbnail=str(destination), timeout=25)
                after = os.stat(record["path"], follow_symlinks=False)
                if signature(after) != record["_signature"] or not destination.is_file():
                    raise ValueError("视频截帧时发生变化")
                return index, f"covers/{destination.name}", record["path"], ""
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
                last_error = str(error)
        return index, "", "", last_error
    count = 0
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for index, relative, source, error in pool.map(export_one, pending):
                if relative:
                    groups[index]["poster"] = relative
                    groups[index]["poster_source"] = "视频截帧：" + source
                    count += 1
                else:
                    groups[index]["poster_status"] = "无法生成视频封面；视频是否可播放仍需人工确认"
                    if error:
                        issue(issues, groups[index]["files"][0]["path"], f"视频封面未生成：{error}")
    finally:
        for worker in workers:
            worker.close()
    return count


def render_report(data):
    e = lambda value: html.escape(str(value), quote=True)
    s = data["summary"]
    def table(headers, rows):
        return "<div class='scroll'><table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table></div>"
    duplicates = "".join(f"<details open><summary>第 {i} 组 · {len(g['paths'])} 个文件 · 每个 {size_text(g['bytes_each'])}</summary><ul>" + "".join(f"<li>{e(p)}</li>" for p in g["paths"]) + f"</ul><small>SHA-256：{e(g['sha256'])}</small></details>" for i, g in enumerate(data["duplicates"][:300], 1)) or "<p class='empty'>本次成功读取的候选文件中未确认精确重复。请同时检查跳过和错误记录。</p>"
    pairs = data["similar"]["pairs"]
    previews = data["previews"]
    def image_tile(path):
        preview = previews.get(path)
        picture = f"<img loading='lazy' src='{e(preview)}' alt='图片预览'>" if preview else "<span class='no-preview'>预览不可用</span>"
        return f"<div class='image-tile'>{picture}<div>{e(path)}</div></div>"
    similarity = "".join(f"<article class='pair'><strong>候选 {i} · dHash 距离 {p['distance']}</strong><div class='pair-grid'>{image_tile(p['left'])}{image_tile(p['right'])}</div></article>" for i, p in enumerate(pairs[:300], 1)) if pairs else "<p class='empty'>没有图片相似候选，或本次未启用可用的图片解析组件。</p>"
    video_groups = "".join(f"<details><summary>{e(g['type'])}：{e(g['label'])} · {len(g['files'])} 个文件</summary><ul>" + "".join(f"<li>{e(f['path'])} <small>({e(f['extension'].upper())} · {size_text(f['bytes'])} · {e(f['variant'] or '无变体标记')} · 修改于 {e(f['modified_at'] or '未知')} · {e(f['hash_status'] or '未校验')} · {e(f['video_header_status'])})</small></li>" for f in g["files"]) + "</ul></details>" for g in data["video_groups"][:300]) or "<p class='empty'>没有发现同编号或同标题的相关视频候选。</p>"
    sidecar_items = data.get("sidecars", [])
    sidecars = table(["附属文件", "类型", "关联状态", "媒体文件", "媒体建议路径"],
                     [[item["path"], item["extension"].upper(), item["status"], "；".join(item["media_paths"]), item["media_suggested_path"]] for item in sidecar_items[:300]]) if sidecar_items else "<p class='empty'>没有发现支持的附属文件。</p>"
    video_inspection = data.get("video_inspection", {"enabled": False, "checked": 0, "recognized": 0, "unrecognized": 0})
    video_check_note = (f"本次轻量检查 {video_inspection['checked']} 个视频文件头，识别 {video_inspection['recognized']} 个，待核对 {video_inspection['unrecognized']} 个。" if video_inspection["enabled"] else "本次未启用可选的视频文件头检查。")
    folder_groups = data.get("folder_groups", [])
    folder_sections = "".join(f"<details><summary>{e(g['type'])}：{e(g['name'])} · {len(g['folders'])} 个文件夹</summary><ul>" + "".join(f"<li>{e(f['path'])} <small>（包含 {f['media_files']} 项媒体、{f['sidecar_files']} 项附属文件，媒体大小 {size_text(f['logical_bytes'])}；{e(f['content_check'])}" + (f"；匹配示例：{e(f['content_match_example'])}" if f["content_match_example"] else "") + "）</small></li>" for f in g["folders"]) + "</ul></details>" for g in folder_groups[:300]) or "<p class='empty'>没有发现同名或整理后名称冲突的文件夹。</p>"
    plan = "<div class='scroll'><table><thead><tr><th>类型</th><th>原文件</th><th>建议路径（仅预览）</th><th>判断依据</th></tr></thead><tbody id='plan-rows'></tbody></table></div>"
    browse_data = json.dumps([{"kind": r["kind"], "path": r["path"], "suggested_path": r["suggested_path"], "reason": r["reason"]} for r in data["files"]], ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    script = """
    const allRows = JSON.parse(document.getElementById('classification-data').textContent);
    const search = document.getElementById('search');
    const kind = document.getElementById('kind-filter');
    const tbody = document.getElementById('plan-rows');
    const count = document.getElementById('plan-count');
    const previous = document.getElementById('previous');
    const next = document.getElementById('next');
    let filtered = allRows, page = 0;
    const pageSize = 100;
    function render() {
      const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(page, pages - 1);
      tbody.replaceChildren();
      for (const item of filtered.slice(page * pageSize, (page + 1) * pageSize)) {
        const tr = document.createElement('tr');
        for (const value of [item.kind, item.path, item.suggested_path, item.reason]) {
          const td = document.createElement('td'); td.textContent = value; tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      count.textContent = `共 ${filtered.length} 条 · 第 ${page + 1}/${pages} 页`;
      previous.disabled = page === 0; next.disabled = page >= pages - 1;
    }
    function filter() {
      const q = search.value.trim().toLocaleLowerCase();
      filtered = allRows.filter(item => (!kind.value || item.kind === kind.value) &&
        (!q || [item.path, item.suggested_path, item.reason].some(value => value.toLocaleLowerCase().includes(q))));
      page = 0; render();
    }
    search.addEventListener('input', filter); kind.addEventListener('change', filter);
    previous.addEventListener('click', () => { page--; render(); });
    next.addEventListener('click', () => { page++; render(); });
    render();
    """
    problems = table(["路径", "说明"], [[r["path"], r["reason"]] for r in data["issues"][:500]]) if data["issues"] else "<p class='empty'>没有读取错误。</p>"
    skipped = "；".join(f"{e(k)}：{v}" for k, v in data["skipped"].items()) or "无"
    cards = "".join(f"<div class='card'><span>{label}</span><strong>{e(value)}</strong></div>" for label, value in [("媒体文件", s["files"]), ("精确重复组", s["duplicate_groups"]), ("多余副本逻辑大小", size_text(s["redundant_logical_bytes"])), ("图片相似候选", len(pairs)), ("相关视频候选组", len(data["video_groups"])), ("附属文件", len(sidecar_items)), ("文件夹名称候选组", len(folder_groups))])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' file: data:"><title>媒体整理助手 · 扫描报告</title><style>
    :root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:#1d2939;font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}}main{{max-width:1280px;margin:38px auto;padding:0 28px}}header{{padding:30px;background:#14352f;color:white;border-radius:20px}}h1{{margin:8px 0;font-size:32px}}header p{{color:#d5e9e2;margin:6px 0}}.badge{{font-size:12px;letter-spacing:2px;color:#9ee2c7}}.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:20px 0}}.card,section{{background:white;border:1px solid #dde5e6;border-radius:14px;padding:22px}}.card span{{display:block;color:#607076;font-size:13px}}.card strong{{font-size:27px}}nav{{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}}a{{color:#126653}}section{{margin:18px 0}}h2{{margin:0 0 8px;font-size:22px}}small,.muted{{color:#627378}}.notice{{background:#fff5dc;padding:12px 16px;border-radius:8px}}input,select,button{{padding:10px;border:1px solid #a8babc;border-radius:8px;font:inherit}}button{{background:white;cursor:pointer}}button:disabled{{opacity:.45;cursor:default}}.controls,.pager{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}}.controls input{{flex:1;min-width:240px}}.scroll{{overflow:auto;max-height:650px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #e4eaec;overflow-wrap:anywhere;min-width:150px}}th{{background:#f1f5f4;position:sticky;top:0}}td:first-child{{max-width:460px}}details,.pair{{border:1px solid #dce5e5;border-radius:8px;padding:14px;margin:12px 0;overflow-wrap:anywhere}}summary{{cursor:pointer;font-weight:600}}li{{margin:8px 0}}.empty{{color:#627378;padding:12px 0}}.pair-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:10px}}.image-tile{{min-width:0;overflow-wrap:anywhere;font-size:12px}}.image-tile img,.no-preview{{display:block;width:100%;height:220px;object-fit:contain;background:#f1f5f4;border-radius:8px}}.no-preview{{display:grid;place-items:center;color:#627378}}@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:700px){{main{{padding:0 14px}}h1{{font-size:27px}}.pair-grid{{grid-template-columns:1fr}}}}[hidden]{{display:none!important}}
    </style></head><body><main><header><div class="badge">LOCAL MEDIA AUDIT / 只读扫描</div><h1>让媒体库清楚一点</h1><p>精确重复 · 图片相似候选 · 分类预览</p><p>生成时间：{e(data['created_at'])}　原文件未被移动、重命名或删除。</p></header><div class="cards">{cards}</div>
    <nav><a href="library.html"><strong>打开影片资料库 →</strong></a><a href="#duplicates">精确重复</a><a href="#similar">图片相似</a><a href="#video-groups">相关视频</a><a href="#sidecars">附属文件</a><a href="#folder-groups">文件夹名称</a><a href="#plan">分类预览</a><a href="#issues">跳过与错误</a><a href="inventory.csv">完整清单 CSV</a><a href="classification.csv">分类计划 CSV</a><a href="report.json">完整 JSON</a></nav>
    <p class="muted">扫描目录：{'；'.join(e(p) for p in data['roots'])}</p><p class="notice">这是一份人工核对报告。多余副本大小是逻辑估算，硬链接已排除；APFS 克隆、压缩和云盘会影响实际可释放空间。报告含本地完整路径，请妥善保存。</p>
    <section id="duplicates"><h2>内容完全重复</h2><p>先按大小筛选，再读取整个文件计算 SHA-256。名称相同、编号相同或同一视频的不同编码不会据此算重复。页面最多展示 300 组，全部结果见 <a href="duplicates.csv">重复明细 CSV</a>。</p>{duplicates}</section>
    <section id="similar"><h2>图片相似候选</h2><p>64 位 dHash 距离阈值：{data['options']['distance']}，同时限制宽高比差异。视觉相似只供对照，不能作为删除依据；裁剪、旋转、连拍和纯色图片可能漏检或误报。</p><p class="muted">{e(data['image_inspection']['note'])} 可比较图片 {data['similar']['eligible']} 张；硬链接 {s['hardlinks']} 项未重复计算。</p>{'<p class="notice">候选达到数量上限，结果可能不完整。可以缩小扫描目录或提高 --max-similar。</p>' if data['similar']['truncated'] else ''}{similarity}<p><a href="similar.csv">全部已生成候选 CSV</a> · 页面最多展示 300 对</p></section>
    <section id="video-groups"><h2>相关视频候选</h2><p>根据同一编号或清理分段、画质标记后的标题归组，方便检查同一作品的分段和不同版本。这些分组不表示内容重复；视频是否完全相同只看上方的 SHA-256 结果。可用 --check-video-headers 轻量识别部分容器文件头，但无法证明视频可播放。{e(video_check_note)}页面最多展示 300 组，全部见 <a href="video_groups.csv">视频关联 CSV</a>。</p>{video_groups}</section>
    <section id="sidecars"><h2>附属文件关联</h2><p>同目录文件名关联 XMP、AAE 与字幕、NFO；仅记录名称和大小，不读取附属文件内容。多项候选及未关联项需要人工核对；不会移动或修改附属文件。页面最多展示 300 项，全部见 <a href="sidecars.csv">附属文件 CSV</a>。</p>{sidecars}</section>
    <section id="folder-groups"><h2>文件夹名称候选</h2><p>列出同名（大小写视为相同）或整理后名称可能冲突的文件夹，并统计其下媒体和附属文件项。仅在两处已扫描媒体均有完整 SHA-256、字节内容集合一致且文件夹互不包含时标记匹配；附属文件只计数，不比较内容。文件名、其他非媒体文件和跳过项也未比较，因此不代表整个文件夹完全相同。页面最多展示 300 组，全部见 <a href="folder_names.csv">文件夹名称 CSV</a>。</p>{folder_sections}</section>
    <section id="plan"><h2>分类建议 · 仅预览</h2><p>照片优先使用 EXIF 拍摄日期，其次文件名日期，最后修改时间。视频按编号、名称或原目录归组。目标重名会加路径标识。实况照片配对仅按同目录同名推测。</p><div class="controls"><input id="search" type="search" aria-label="筛选全部分类建议" placeholder="搜索全部文件名、目录或建议"><select id="kind-filter" aria-label="按媒体类型筛选"><option value="">全部类型</option><option value="照片">照片</option><option value="视频">视频</option></select></div>{plan}<div class="pager"><button id="previous" type="button">上一页</button><span id="plan-count"></span><button id="next" type="button">下一页</button></div><p class="muted">可筛选全部 {s['files']} 条建议，每页显示 100 条。完整建议见 classification.csv；本工具没有执行移动或删除的功能。</p></section>
    <section id="issues"><h2>跳过与错误</h2><p>读取问题 {len(data['issues'])} 条（页面最多展示 500 条，完整列表见 <a href="issues.csv">问题 CSV</a>）。目录不可读或文件变化会使结果不完整。</p><p class="muted">按规则跳过：{skipped}。隐藏项、符号链接、照片资料库包和可识别的云端占位项默认不读取。</p>{problems}</section><footer class="muted">离线生成 · 不上传媒体 · 不访问远程元数据 · 完整记录见同目录 CSV / JSON</footer></main><script id="classification-data" type="application/json">{browse_data}</script><script>{script}</script></body></html>'''


def render_video_library(data):
    library = data["video_library"]
    payload = json.dumps(library, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' file: data:; connect-src 'self'"><title>影片资料库 · 媒体整理助手</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:#1d2939;font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}main{max-width:1180px;margin:32px auto;padding:0 24px}header{background:#14352f;color:white;padding:28px;border-radius:18px}h1{font-size:31px;margin:4px 0}header p{margin:4px 0;color:#d5e9e2}a{color:#126653}header a{color:#b9f0d8}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}.stat{background:white;border:1px solid #dce5e5;border-radius:12px;padding:13px 20px;min-width:170px}.stat strong{display:block;font-size:25px}.controls{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}input,select,button{font:inherit;border:1px solid #a8babc;border-radius:8px;padding:9px;background:white}input{flex:1;min-width:220px}section{background:white;border:1px solid #dce5e5;border-radius:12px;padding:18px;margin:12px 0}.wall{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:16px;align-items:start}.card{background:#fff;border:1px solid #dce5e5;border-radius:12px;overflow:hidden;overflow-wrap:anywhere;box-shadow:0 3px 12px #1d29390d}.poster{display:block;width:100%;aspect-ratio:2/3;object-fit:cover;background:#e4eae8}.poster-placeholder{display:grid;place-items:center;background:linear-gradient(145deg,#194438,#537d69);color:#f2fff8;padding:18px;text-align:center;font-size:27px;font-weight:700}.card-body{padding:14px}.card h3{margin:0 0 4px;font-size:18px;line-height:1.4}.card details{border-top:1px solid #e3eaeb;margin-top:10px;padding-top:8px}.card summary{cursor:pointer;color:#126653}.meta{color:#627378;font-size:13px}.badge{display:inline-block;background:#e5f3ec;color:#205640;border-radius:999px;padding:2px 9px;margin:5px 6px 5px 0;font-size:12px}.alert{background:#fff0d7;color:#80520c}.file{border-top:1px solid #e3eaeb;padding:10px 0}.path{font-weight:600;overflow-wrap:anywhere}.minor{color:#607076;font-size:13px;overflow-wrap:anywhere}.issue{border-bottom:1px solid #e3eaeb;padding:10px 0}.pager{display:flex;align-items:center;gap:10px;margin:12px 0}.muted{color:#627378}.empty{padding:15px;color:#627378}.tag-editor{background:#f3f8f5;border:1px solid #c9ded3;border-radius:8px;padding:9px;margin:8px 0}.tag-editor input{display:block;width:100%;min-width:0;margin-bottom:8px}.tag-editor button{padding:5px 8px;margin-right:5px;font-size:12px}.tag-editor .minor{margin-top:5px}@media(max-width:650px){main{padding:0 12px}h1{font-size:25px}.wall{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.card-body{padding:10px}}
    </style></head><body><main><header><a href="report.html">← 返回扫描总报告</a><h1>影片资料库</h1><p>本次扫描快照 · @@CREATED@@</p><p>只读浏览和问题核对；重新扫描会生成新快照，不修改原片。</p></header>
    <div class="stats"><div class="stat">影片分组<strong id="group-total">0</strong></div><div class="stat">可用封面<strong>@@POSTER_TOTAL@@</strong></div><div class="stat">视频文件<strong>@@VIDEO_FILES@@</strong></div><div class="stat">精确重复涉及视频<strong>@@DUPLICATE_FILES@@</strong></div><div class="stat">待核对项目<strong>@@ISSUE_TOTAL@@</strong></div></div>
    <section><h2>影片海报墙</h2><p class="muted">同编号跨目录归组；没有编号时，仅将同一原目录内的同名变体归组。封面优先使用同目录的同名或编号图片，其次尝试从视频截帧；无法生成时显示占位图。分组只靠文件名，不表示内容相同。每页 50 组。</p><p class="muted" id="tag-help">标签可筛选；通过“媒体整理助手.app”或“打开资料库.command”打开本地服务后可以编辑并跨扫描保留。</p><div class="controls"><input id="search" type="search" aria-label="搜索影片、路径、标签和附属文件" placeholder="搜索编号、影片名、路径、标签、字幕…"><select id="filter" aria-label="筛选影片"><option value="all">全部影片</option><option value="unwatched">未标记已观看</option><option value="watched">已观看</option><option value="favorite">收藏</option><option value="review">有视频问题或精确重复</option><option value="duplicates">精确重复涉及视频</option><option value="sidecars">有附属文件</option><option value="posters">有封面</option><option value="missing-posters">缺少封面</option></select><select id="tag-filter" aria-label="按标签筛选"><option value="">全部标签</option></select></div><div id="groups" class="wall"></div><div class="pager"><button id="previous" type="button">上一页</button><span id="page-label"></span><button id="next" type="button">下一页</button></div></section>
    <section><h2>待核对清单</h2><p class="muted">包含本次扫描发现的视频读取或文件头问题、精确重复、未唯一关联的字幕/NFO，以及同名文件夹候选。未启用文件头检查时，不会据此判断视频能否播放。下方显示与搜索词匹配的前 200 条；完整数据见 <a href="library_issues.csv">问题 CSV</a>。</p><div id="issues"></div></section>
    <p class="muted">播放使用系统默认播放器；分段或多个版本请展开文件列表选择。已观看与收藏需手动标记，播放不会自动标为已观看。本页面使用本次扫描结果。其他跳过项与照片问题请查看<a href="report.html#issues">总报告</a>；完整原始数据见 <a href="report.json">JSON</a>。</p></main>
    <script id="library-data" type="application/json">@@DATA@@</script><script>
    const library = JSON.parse(document.getElementById('library-data').textContent);
    const search = document.getElementById('search');
    const filter = document.getElementById('filter');
    const tagFilter = document.getElementById('tag-filter');
    const groups = document.getElementById('groups');
    const issues = document.getElementById('issues');
    const tagApi = location.protocol === 'http:' && location.hostname === '127.0.0.1' && /^\/[^/]+\/library\.html$/.test(location.pathname)
      ? location.pathname.replace(/library\.html$/, 'api/tags') : '';
    let page = 0, mediaIds = {}, mediaAvailable = false;
    const make = (tag, className, value) => { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = value; return node; };
    function updateTagChoices() {
      const selected = tagFilter.value;
      const names = [...new Set(library.groups.flatMap(group => group.tags || []))].sort((a, b) => a.localeCompare(b));
      const all = make('option', '', '全部标签'); all.value = ''; tagFilter.replaceChildren(all);
      for (const name of names) { const option = make('option', '', name); option.value = name; tagFilter.append(option); }
      tagFilter.value = names.includes(selected) ? selected : '';
    }
    function editTags(group, edit) {
      edit.disabled = true;
      const panel = make('div', 'tag-editor');
      const input = make('input'); input.type = 'text'; input.value = (group.tags || []).join(', ');
      input.placeholder = '多个标签用逗号分隔'; input.setAttribute('aria-label', `${group.title} 的标签`);
      const save = make('button', '', '保存'); save.type = 'button';
      const cancel = make('button', '', '取消'); cancel.type = 'button';
      const message = make('div', 'minor', '清空后保存可删除全部标签。');
      cancel.addEventListener('click', () => { panel.remove(); edit.disabled = false; });
      async function submit() {
        const tags = input.value.split(/[,，]/).map(tag => tag.trim()).filter(Boolean);
        save.disabled = true; message.textContent = '正在保存…';
        try {
          const response = await fetch(tagApi, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: group.tag_key, tags})});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || '标签保存失败');
          group.tags = result.tags; updateTagChoices(); render();
        } catch (error) { message.textContent = `标签未保存：${error.message}`; save.disabled = false; }
      }
      save.addEventListener('click', submit);
      input.addEventListener('keydown', event => { if (event.key === 'Enter') submit(); });
      panel.append(input, save, cancel, message); edit.after(panel); input.focus();
    }
    async function toggleMark(group, tag, button, message) {
      if(group.markSaving)return;group.markSaving=true;
      const controls=[...button.parentElement.querySelectorAll('button')];controls.forEach(control=>control.disabled=true);
      try {
        const response = await fetch(tagApi.replace(/tags$/, 'tags/toggle'), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:group.tag_key,tag,enabled:!(group.tags || []).includes(tag)})});
        const result = await response.json(); if (!response.ok) throw new Error(result.error || '保存失败');
        group.tags = result.tags; group.markSaving=false; updateTagChoices(); render();
      } catch (error) {message.textContent=error.message;}finally{group.markSaving=false;controls.forEach(control=>control.disabled=false);}
    }
    async function openMedia(file, action, button, message) {
      button.disabled = true; message.textContent='正在打开…';
      try {
        const response=await fetch('api/media/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:mediaIds[file.path],action})});
        const result=await response.json();if(!response.ok)throw new Error(result.error || '无法打开原文件');message.textContent=result.message;
      } catch(error){message.textContent=error.message;}finally{button.disabled=false;}
    }
    function mediaButtons(file, parent) {
      if (!mediaAvailable || !mediaIds[file.path]) return;
      const message=make('div','minor');message.setAttribute('role','status');
      for (const [action,label] of [['open','播放'],['reveal','在 文件管理器 定位']]) {const button=make('button','',label);button.type='button';button.setAttribute('aria-label',label+' '+file.path.split('/').pop());button.addEventListener('click',()=>openMedia(file,action,button,message));parent.append(button);}parent.append(message);
    }
    function render() {
      const query = search.value.trim().toLocaleLowerCase();
      const visible = library.groups.filter(group => {
        if (filter.value === 'watched' && !(group.tags || []).includes('已观看')) return false;
        if (filter.value === 'unwatched' && (group.tags || []).includes('已观看')) return false;
        if (filter.value === 'favorite' && !(group.tags || []).includes('收藏')) return false;
        if (filter.value === 'review' && !group.needs_review) return false;
        if (filter.value === 'duplicates' && !group.files.some(file => file.duplicate_group)) return false;
        if (filter.value === 'sidecars' && !group.has_sidecars) return false;
        if (filter.value === 'posters' && !group.poster) return false;
        if (filter.value === 'missing-posters' && group.poster) return false;
        if (tagFilter.value && !(group.tags || []).includes(tagFilter.value)) return false;
        return !query || [group.title, (group.personal||{}).note||'', ...(group.tags || []), ...group.files.flatMap(file => [file.path, file.suggested_path, ...file.sidecars])].some(value => value.toLocaleLowerCase().includes(query));
      });
      const pages = Math.max(1, Math.ceil(visible.length / 50)); page = Math.min(page, pages - 1);
      groups.replaceChildren();
      for (const group of visible.slice(page * 50, page * 50 + 50)) {
        const card = make('article', 'card');
        if (group.poster) {
          const picture = make('img', 'poster'); picture.src = group.poster; picture.alt = `${group.title} 的封面`; picture.loading = 'lazy'; card.append(picture);
        } else card.append(make('div', 'poster poster-placeholder', group.title.slice(0, 12)));
        const body = make('div', 'card-body');
        body.append(make('h3', '', group.title), make('div', 'meta', `${group.type} · ${group.files.length} 个视频`));
        if(group.personal&&group.personal.rating)body.append(make('div','meta',`个人评分：${group.personal.rating} 星`));
        const detail=make('button','','影片详情');detail.setAttribute('aria-label','影片详情 '+group.title);detail.onclick=()=>movieDetails(group);body.append(detail);
        for (const tag of group.tags || []) body.append(make('span', 'badge', tag));
        if (tagApi && /^[0-9a-f]{64}$/.test(group.tag_key || '')) {
          const message=make('div','minor');message.setAttribute('role','status');
          for(const tag of ['已观看','收藏']){const marked=(group.tags||[]).includes(tag),button=make('button','',tag==='已观看'?(marked?'取消已观看':'标记已观看'):(marked?'取消收藏':'收藏'));button.type='button';button.setAttribute('aria-label',button.textContent+' '+group.title);button.addEventListener('click',()=>toggleMark(group,tag,button,message));body.append(button);}body.append(message);
          const edit=make('button','','编辑标签');edit.type='button';edit.addEventListener('click',()=>editTags(group,edit));body.append(edit);
        }
        if(group.files.length===1)mediaButtons(group.files[0],body);
        if (group.needs_review) body.append(make('span', 'badge alert', '待核对'));
        if (group.has_sidecars) body.append(make('span', 'badge', '有附属文件'));
        if (group.poster_source) body.append(make('div', 'minor', `封面：${group.poster_source.startsWith('视频截帧：') ? '视频截帧' : '本地图片'}`));
        const details = make('details'); details.append(make('summary', '', `查看 ${group.files.length} 个文件`));
        for (const file of group.files) {
          const row = make('div', 'file');
          row.append(make('div', 'path', file.path), make('div', 'minor', `${file.extension.toUpperCase()} · ${(file.bytes / 1048576).toFixed(1)} MB · 修改于 ${file.modified_at} · ${file.hash_status}`));
          if (file.duplicate_group) row.append(make('div', 'badge alert', `精确重复第 ${file.duplicate_group} 组`));
          if (file.video_header_status !== '未检查') row.append(make('div', 'minor', `文件头：${file.video_header_status}`));
          if (file.sidecars.length) row.append(make('div', 'minor', `附属文件：${file.sidecars.join('；')}`));
          for (const reason of file.issues) row.append(make('div', 'badge alert', reason));
          row.append(make('div', 'minor', `分类建议：${file.suggested_path}`));
          if(group.files.length>1)mediaButtons(file,row);
          details.append(row);
        }
        body.append(details); card.append(body);
        groups.append(card);
      }
      if (!visible.length) groups.append(make('p', 'empty', '没有符合条件的影片。'));
      document.getElementById('page-label').textContent = `共 ${visible.length} 组 · 第 ${page + 1}/${pages} 页`;
      document.getElementById('previous').disabled = page === 0;
      document.getElementById('next').disabled = page >= pages - 1;
      const matches = library.issues.filter(item => !query || [item.path, item.reason, item.type].some(value => value.toLocaleLowerCase().includes(query)));
      issues.replaceChildren();
      for (const item of matches.slice(0, 200)) {
        const row = make('div', 'issue');
        row.append(make('span', 'badge alert', item.type), make('div', 'path', item.path), make('div', 'minor', item.reason));
        issues.append(row);
      }
      if (!matches.length) issues.append(make('p', 'empty', '没有符合条件的待核对项目。'));
      if (matches.length > 200) issues.append(make('p', 'muted', `还有 ${matches.length - 200} 条，见问题 CSV。`));
    }
    search.addEventListener('input', () => { page = 0; render(); });
    filter.addEventListener('change', () => { page = 0; render(); });
    tagFilter.addEventListener('change', () => { page = 0; render(); });
    document.getElementById('previous').addEventListener('click', () => { page--; render(); });
    document.getElementById('next').addEventListener('click', () => { page++; render(); });
    document.getElementById('group-total').textContent = library.groups.length;
    @@DETAILS@@
    updateTagChoices();
    render();
    loadMovieNotes();
    if (tagApi) fetch('api/media').then(response=>{if(!response.ok)throw new Error();return response.json();}).then(result=>{mediaIds=result.ids_by_path;mediaAvailable=result.available;render();}).catch(()=>{});
    if (tagApi) fetch(tagApi).then(response => { if (!response.ok) throw new Error('读取标签失败'); return response.json(); })
      .then(result => { for (const group of library.groups) group.tags = result.groups[group.tag_key] || []; updateTagChoices(); render();
        document.getElementById('tag-help').textContent = '编辑标签会立即保存到本机 reports/library-tags.json；重新扫描后仍可按标签查找。'; })
      .catch(() => { document.getElementById('tag-help').textContent = '标签服务暂不可用；当前仅显示扫描时保存的标签。'; });
    </script></body></html>'''
    replacements = {"CREATED": html.escape(data["created_at"], quote=True), "POSTER_TOTAL": str(library.get("poster_count", 0)), "VIDEO_FILES": str(library["video_files"]),
                    "DUPLICATE_FILES": str(library["duplicate_files"]), "ISSUE_TOTAL": str(len(library["issues"])), "DATA": payload,
                    "DETAILS": (BASE / "library_details.js").read_text(encoding="utf-8")}
    return re.sub(r"@@(CREATED|POSTER_TOTAL|VIDEO_FILES|DUPLICATE_FILES|ISSUE_TOTAL|DATA|DETAILS)@@", lambda match: replacements[match.group(1)], template)


def write_reports(directory, data):
    files = data["files"]
    fields = [("path", "原路径"), ("kind", "类型"), ("bytes", "字节数"), ("sha256", "SHA-256"), ("hash_status", "校验状态"), ("hardlink_to", "硬链接指向"), ("suggested_path", "建议相对路径"), ("reason", "建议依据"), ("date_source", "日期来源"), ("video_header_status", "视频文件头状态")]
    write_csv(directory / "inventory.csv", fields, files)
    write_csv(directory / "classification.csv", [fields[i] for i in [0, 6, 7, 8]], files)
    write_csv(directory / "duplicates.csv", [("group", "组号"), ("path", "路径"), ("bytes", "字节数"), ("sha256", "SHA-256")],
              ({"group": i, "path": p, "bytes": g["bytes_each"], "sha256": g["sha256"]} for i, g in enumerate(data["duplicates"], 1) for p in g["paths"]))
    write_csv(directory / "similar.csv", [("left", "图片A"), ("right", "图片B"), ("distance", "dHash距离")], data["similar"]["pairs"])
    write_csv(directory / "video_groups.csv", [("type", "分组依据"), ("label", "关联标签"), ("path", "原路径"),
                                                 ("bytes", "字节数"), ("extension", "扩展名"),
                                                 ("variant", "文件名变体标记"),
                                                 ("modified_at", "文件修改时间"), ("hash_status", "校验状态"),
                                                 ("sha256", "SHA-256"), ("video_header_status", "视频文件头状态")],
              ({"type": group["type"], "label": group["label"], **file}
               for group in data["video_groups"] for file in group["files"]))
    write_csv(directory / "sidecars.csv", [("path", "附属文件路径"), ("extension", "扩展名"), ("bytes", "字节数"),
                                            ("status", "关联状态"), ("media_paths", "媒体候选路径"),
                                            ("media_suggested_path", "媒体建议路径")],
              ({**item, "media_paths": " | ".join(item["media_paths"])} for item in data["sidecars"]))
    write_csv(directory / "folder_names.csv", [("type", "分组依据"), ("name", "文件夹名称"), ("path", "来源文件夹"),
                                                  ("media_files", "包含媒体项"), ("sidecar_files", "包含附属文件项"),
                                                  ("logical_bytes", "媒体逻辑字节数"),
                                                  ("content_check", "媒体内容校验"), ("content_match_example", "匹配文件夹示例")],
              ({"type": group["type"], "name": group["name"], **folder}
               for group in data["folder_groups"] for folder in group["folders"]))
    write_csv(directory / "issues.csv", [("path", "路径"), ("reason", "说明")], data["issues"])
    write_csv(directory / "library_issues.csv", [("type", "类别"), ("path", "路径"), ("reason", "核对原因")], data["video_library"]["issues"])
    with (directory / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    with (directory / "report.html").open("x", encoding="utf-8") as stream:
        stream.write(render_report(data))
    with (directory / "library.html").open("x", encoding="utf-8") as stream:
        stream.write(render_video_library(data))


def choose_folders():
    if sys.platform == "win32":
        from system_integration import choose_directory
        selected = choose_directory("选择照片或视频文件夹（只读扫描，可继续添加多个目录）")
        return [selected] if selected else []
    if sys.platform != "darwin":
        raise ValueError("请在命令后提供要扫描的文件夹路径。")
    script = '''set choices to choose folder with prompt "选择照片或视频文件夹（可多选）。只扫描，不修改文件。" with multiple selections allowed
set answer to ""
repeat with folderChoice in choices
set answer to answer & POSIX path of folderChoice & ASCII character 0
end repeat
return answer'''
    result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    if result.returncode:
        if "-128" in result.stderr:
            return []
        raise ValueError("无法打开文件夹选择窗口：" + result.stderr.strip())
    return [p for p in result.stdout.rstrip("\n").split("\0") if p]


def main(argv=None):
    parser = argparse.ArgumentParser(description="媒体整理助手：仅扫描和生成报告，不移动、不删除、不上传文件。")
    parser.add_argument("folders", nargs="*", help="扫描目录，可多个；不填则弹出文件夹选择窗口")
    parser.add_argument("--output", type=Path, default=BASE / "reports", help="报告存放目录")
    parser.add_argument("--video-rule", choices=["auto", "name", "folder"], default="auto", help="视频分类建议规则")
    parser.add_argument("--distance", type=int, choices=range(0, 17), default=6, metavar="0-16", help="图片 dHash 距离阈值，默认 6")
    parser.add_argument("--max-similar", type=int, default=5000, help="相似候选上限，默认 5000")
    parser.add_argument("--no-similar", action="store_true", help="关闭相似图片比较")
    parser.add_argument("--no-image-metadata", action="store_true", help="跳过照片解析和封面生成，加快扫描；仍有精确文件查重")
    parser.add_argument("--check-video-headers", action="store_true", help="可选：读取少量视频文件头并提示常见容器是否可识别；不验证能否播放")
    parser.add_argument("--no-video-covers", action="store_true", help="不从视频截帧生成封面；仍使用本地图片封面")
    parser.add_argument("--max-video-covers", type=int, default=500, help="视频截帧封面数量上限，默认 500")
    parser.add_argument("--include-hidden", action="store_true", help="包含隐藏文件和目录（仍跳过资料库和系统目录）")
    parser.add_argument("--open", action="store_true", help="扫描完成后打开 HTML 报告")
    parser.add_argument("--edit-tags", action="store_true", help="扫描后打开本机资料库，可编辑标签；关闭终端停止服务")
    parser.add_argument("--serve-library", action="store_true", help="打开最近一次资料库并编辑标签，不重新扫描")
    args = parser.parse_args(argv)
    cancel_path = os.environ.get("MEDIA_ORGANIZER_CANCEL_FILE")
    cancel_monitor_done = threading.Event()
    if cancel_path:
        def monitor_cancel():
            import _thread
            while not cancel_monitor_done.wait(.2):
                if Path(cancel_path).exists():
                    _thread.interrupt_main()
                    return
        threading.Thread(target=monitor_cancel, daemon=True).start()
    if args.max_similar < 1:
        parser.error("--max-similar 必须大于 0")
    if args.max_video_covers < 0:
        parser.error("--max-video-covers 不能小于 0")
    previous_umask = os.umask(0o077)
    try:
        output = args.output.expanduser().resolve()
        if args.serve_library:
            reports = sorted((path for path in output.glob("scan-*") if path.is_dir() and (path / "library.html").is_file()), reverse=True)
            if not reports:
                raise ValueError("没有找到可打开的影片资料库，请先运行一次扫描")
            serve_library(reports[0], output)
            return 0
        folders = args.folders or choose_folders()
        if not folders:
            print("已取消，没有扫描文件。")
            return 0
        roots = normalize_roots(folders, output)
        issues = []
        print("只读扫描开始。可按 Ctrl+C 取消；原文件不会修改。", flush=True)
        folders_seen = []
        found_sidecars = []
        records, skipped = discover(roots, output, args.include_hidden, issues, folders_seen, found_sidecars)
        duplicates = exact_duplicates(records, issues)
        video_inspection = inspect_video_headers(records, issues, args.check_video_headers)
        image_inspection = inspect_images(records, media_backend.helper("image_probe"), issues, not args.no_image_metadata)
        classify(records, args.video_rule)
        sidecars = associate_sidecars(found_sidecars, records)
        similarity = similar_images(records, args.distance, args.max_similar, not args.no_similar and not args.no_image_metadata and image_inspection["available"])
        video_groups = related_videos(records)
        folder_groups = related_folders(folders_seen, records, sidecars)
        tag_path = output / "library-tags.json"
        try:
            saved_tags = load_tags(tag_path)
        except (OSError, ValueError) as error:
            issue(issues, tag_path, f"标签文件未读取：{error}")
            saved_tags = {}
        video_library = build_video_library(records, sidecars, duplicates, issues, folder_groups, saved_tags)
        directory = output / dt.datetime.now().strftime("scan-%Y%m%d-%H%M%S-%f")
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        previews = export_previews(directory, records, similarity, media_backend.helper("image_probe"), issues,
                                   photo_limit=500 if not args.no_image_metadata and image_inspection["available"] else 0)
        cover_issue_start = len(issues)
        video_library["poster_count"] = export_local_covers(directory, video_library["groups"], records,
                                                             media_backend.helper("image_probe"), issues,
                                                             enabled=not args.no_image_metadata)
        video_library["frame_count"] = export_video_frame_covers(directory, video_library["groups"], records,
                                                                  media_backend.helper("video_cover"), issues,
                                                                  limit=args.max_video_covers,
                                                                  enabled=not args.no_image_metadata and not args.no_video_covers)
        video_library["poster_count"] += video_library["frame_count"]
        video_library["issues"].extend({"path": item["path"], "reason": item["reason"], "type": "封面"}
                                       for item in issues[cover_issue_start:])
        clean_records = [{**{k: v for k, v in r.items() if not k.startswith("_")},
                          "source_signature": list(r["_signature"])} for r in records]
        data = {"version": 10, "created_at": dt.datetime.now().astimezone().isoformat(), "roots": [str(r) for r in roots],
                "summary": {"files": len(records), "duplicate_groups": len(duplicates), "hardlinks": sum(bool(r["hardlink_to"]) for r in records),
                            "redundant_logical_bytes": sum(g["redundant_logical_bytes"] for g in duplicates)},
                "options": {"distance": args.distance, "video_rule": args.video_rule, "include_hidden": args.include_hidden,
                            "check_video_headers": args.check_video_headers, "max_video_covers": args.max_video_covers,
                            "video_covers_enabled": not args.no_video_covers and not args.no_image_metadata},
                "image_inspection": image_inspection, "video_inspection": video_inspection,
                "files": clean_records, "duplicates": duplicates,
                "similar": similarity, "previews": previews, "video_groups": video_groups, "sidecars": sidecars,
                "folder_groups": folder_groups, "video_library": video_library, "issues": issues, "skipped": skipped}
        write_reports(directory, data)
        print(f"\n完成：{len(records)} 个媒体文件，{len(duplicates)} 组精确重复，{len(similarity['pairs'])} 对图片相似候选，{len(video_groups)} 组相关视频候选，{len(sidecars)} 个附属文件，{len(folder_groups)} 组文件夹名称候选。")
        print(f"读取问题 {len(issues)} 条。报告：{directory / 'report.html'}")
        if args.edit_tags:
            serve_library(directory, output)
        elif args.open:
            webbrowser.open((directory / "report.html").as_uri())
        return 0
    except KeyboardInterrupt:
        print("\n扫描已取消。原文件未修改；如在导出报告时取消，报告可能不完整。", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"无法完成：{error}", file=sys.stderr)
        return 1
    finally:
        cancel_monitor_done.set()
        os.umask(previous_umask)


if __name__ == "__main__":
    sys.exit(main())
