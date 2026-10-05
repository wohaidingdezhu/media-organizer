"""Report-only photo catalog, movie notes and comparable scan changes."""
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from file_operations import open_directory
from media_actions import media_id


def storage_catalog(document):
    """Logical sizes from the saved scan, never filesystem allocation or free space."""
    records = document.get("files")
    if not isinstance(records, list):
        raise ValueError("扫描报告缺少有效文件清单")
    seen, items, warnings = set(), [], []
    roots = document.get("roots", [])
    roots = sorted({root for root in roots if isinstance(root, str) and Path(root).is_absolute()
                    and '..' not in Path(root).parts}, key=len, reverse=True) if isinstance(roots, list) else []
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("path"), str)
                or not Path(record["path"]).is_absolute() or '..' in Path(record["path"]).parts
                or '\x00' in record["path"] or not isinstance(record.get("kind"), str) or record["kind"] not in {"照片", "视频"}
                or type(record.get("bytes")) is not int or record["bytes"] < 0):
            raise ValueError("扫描报告含无效媒体路径、类型或大小")
        path = record["path"]
        if path in seen:
            raise ValueError("扫描报告含重复原路径，不能可靠汇总大小")
        seen.add(path)
        month = "日期未分类"
        if record["kind"] == "照片" and isinstance(record.get("suggested_path"), str):
            match = re.match(r'^照片/([1-9][0-9]{3})/(0[1-9]|1[0-2])/', record["suggested_path"])
            if match:
                month = '/'.join(match.groups())
        source = next((root for root in roots if Path(root) in Path(path).parents), "来源未提供")
        items.append({"id": media_id(path), "path": path, "name": Path(path).name,
                      "kind": record["kind"], "bytes": record["bytes"],
                      "folder": str(Path(path).parent), "root": source,
                      "month": month, "hardlink": bool(record.get("hardlink_to"))})

    def aggregate(field, selected):
        buckets = {}
        for item in selected:
            row = buckets.setdefault(item[field], {"name": item[field], "count": 0, "bytes": 0})
            row["count"] += 1
            row["bytes"] += item["bytes"]
        return sorted(buckets.values(), key=lambda row: (-row["bytes"], row["name"]))

    duplicates = exact_duplicate_catalog(document)
    warnings.extend(duplicates["warnings"])
    return {"logical_bytes": sum(item["bytes"] for item in items), "count": len(items),
            "kinds": aggregate("kind", items), "roots": aggregate("root", items),
            "folders": aggregate("folder", items),
            "photo_months": aggregate("month", [item for item in items if item["kind"] == "照片"]),
            "largest": sorted(items, key=lambda item: (-item["bytes"], item["path"]))[:100],
            "largest_limit": 100, "hardlink_references": sum(item["hardlink"] for item in items),
            "duplicate_logical_bytes": duplicates["counts"]["redundant_logical_bytes"],
            "warnings": warnings}


def exact_duplicate_catalog(document):
    """Use saved full hashes only; never open media or promote similar pairs."""
    records = document.get("files", [])
    groups = document.get("duplicates", [])
    if not isinstance(records, list) or not isinstance(groups, list):
        raise ValueError("扫描报告缺少有效文件清单或精确重复分组")
    by_path, repeated = {}, set()
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("path"), str):
            path = record["path"]
            if path in by_path:
                repeated.add(path)
            by_path[path] = record
    memberships = {}
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("paths"), list):
            for path in set(path for path in group["paths"] if isinstance(path, str)):
                memberships[path] = memberships.get(path, 0) + 1
    sidecars = {}
    for item in document.get("sidecars", []) if isinstance(document.get("sidecars", []), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("media_paths"), list):
            continue
        for path in item["media_paths"]:
            if isinstance(path, str):
                sidecars.setdefault(path, {})[item["path"]] = {"path": item["path"], "status": str(item.get("status", "需核对"))}
    result, warnings = [], []
    for number, group in enumerate(groups, 1):
        try:
            if not isinstance(group, dict):
                raise ValueError("分组格式无效")
            digest, size, paths = group.get("sha256"), group.get("bytes_each"), group.get("paths")
            if (not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
                    or type(size) is not int or size <= 0 or not isinstance(paths, list) or len(paths) < 2
                    or any(not isinstance(path, str) or not path or "\x00" in path for path in paths)
                    or len(set(paths)) != len(paths)):
                raise ValueError("缺少完整 SHA-256、大小或有效成员路径")
            items = []
            for path in paths:
                record = by_path.get(path)
                if (not record or path in repeated or memberships.get(path) != 1 or record.get("hardlink_to")
                        or record.get("sha256") != digest or type(record.get("bytes")) is not int or record["bytes"] != size
                        or not isinstance(record.get("kind"), str) or record["kind"] not in {"照片", "视频"}):
                    raise ValueError("成员校验信息不完整、重叠或与分组不一致")
                mtime = record.get("mtime")
                try:
                    valid_time = type(mtime) in {int, float} and math.isfinite(mtime) and abs(mtime) <= 8640000000000
                except OverflowError:
                    valid_time = False
                items.append({"id": media_id(path), "path": path, "name": Path(path).name,
                              "folder": str(Path(path).parent), "kind": record["kind"], "bytes": size,
                              "mtime": mtime if valid_time else None,
                              "sidecars": list(sidecars.get(path, {}).values())})
            result.append({"number": number, "sha256": digest, "bytes_each": size,
                           "redundant_logical_bytes": size * (len(items)-1), "items": items})
        except ValueError as error:
            warnings.append(f"精确重复第 {number} 组未展示：{error}；请核对报告或重新扫描。")
    result.sort(key=lambda group: (-group["redundant_logical_bytes"], group["number"]))
    return {"groups": result, "warnings": warnings,
            "counts": {"groups": len(result), "files": sum(len(group["items"]) for group in result),
                       "redundant_logical_bytes": sum(group["redundant_logical_bytes"] for group in result)}}


def photo_catalog(document):
    previews = document.get("previews", {})
    if not isinstance(previews, dict):
        previews = {}
    duplicates = {}
    groups = document.get("duplicates", [])
    for number, group in enumerate(groups if isinstance(groups, list) else [], 1):
        for path in group.get("paths", []) if isinstance(group, dict) and isinstance(group.get("paths"), list) else []:
            if isinstance(path, str):
                duplicates[path] = number
    records = document.get("files", [])
    if not isinstance(records, list):
        records = []
    return {"items": [{"id": media_id(record["path"]), "path": record["path"],
        "name": Path(record["path"]).name, "bytes": record.get("bytes", 0),
        "month": "/".join(record.get("suggested_path", "").split("/")[1:3]),
        "preview": previews.get(record["path"], ""), "duplicate_group": duplicates.get(record["path"]),
        "folder": str(Path(record["path"]).parent)} for record in records if isinstance(record, dict)
        and record.get("kind") == "照片" and isinstance(record.get("path"), str)
        and isinstance(record.get("suggested_path", ""), str)]}


def root_status(roots):
    result = []
    for root in roots:
        try:
            descriptor = open_directory(root)
            os.close(descriptor)
            state = "available"
        except (OSError, ValueError):
            state = "unavailable"
        result.append({"path": str(root), "status": state})
    return {"roots": result}


def compare_scans(document, previous):
    if sorted(document.get("roots", [])) != sorted(previous.get("roots", [])):
        raise ValueError("扫描来源不同，不能直接比较")
    before = {item["path"]: item for item in previous["files"]}
    after = {item["path"]: item for item in document["files"]}
    changes = []
    for path in sorted(after.keys() | before.keys()):
        if path not in before:
            state = "added"
        elif path not in after:
            state = "absent"
        else:
            old, new = before[path], after[path]
            changed = (old.get("bytes") != new.get("bytes") or old.get("mtime") != new.get("mtime")
                       or old.get("source_signature") is not None and new.get("source_signature") is not None
                       and old["source_signature"] != new["source_signature"]
                       or old.get("sha256") and new.get("sha256") and old["sha256"] != new["sha256"])
            if not changed:
                continue
            state = "changed"
        changes.append({"path": path, "state": state})
    return {"previous_created_at": previous.get("created_at", ""), "items": changes,
            "counts": {state: sum(item["state"] == state for item in changes) for state in ("added", "changed", "absent")}}


def previous_scan(report_dir, document, read_document):
    candidates = sorted((path for path in Path(report_dir).parent.glob("scan-*")
                         if path.name < Path(report_dir).name and path.is_dir() and not path.is_symlink()), reverse=True)
    for path in candidates[:50]:
        try:
            older = read_document(path)
            if not isinstance(older, dict) or not isinstance(older.get("files"), list):
                continue
            if sorted(older.get("roots", [])) == sorted(document.get("roots", [])):
                return compare_scans(document, older)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {"previous_created_at": None, "items": [], "counts": {}, "message": "没有找到同一组来源的较早扫描（最多检查最近 50 份），本次为比较基准。"}


def clean_note(value):
    if not isinstance(value, dict) or type(value.get("rating")) is not int or not 0 <= value["rating"] <= 5:
        raise ValueError("评分应为 0（未评分）至 5 星")
    note = value.get("note", "")
    if not isinstance(note, str) or len(note) > 2000 or any(ord(char) < 32 and char not in "\n\t" for char in note):
        raise ValueError("备注应为最多 2000 字的文字")
    return {"rating": value["rating"], "note": note.strip()}


def load_notes(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError("影片备注文件无效或过大")
        document = json.load(stream)
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("groups"), dict):
        raise ValueError("影片备注文件格式无效")
    groups = {}
    for key, value in document["groups"].items():
        if not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("影片备注标识无效")
        groups[key] = clean_note(value)
    return groups


def save_notes(path, groups):
    path = Path(path)
    body = json.dumps({"version": 1, "groups": groups}, ensure_ascii=False, indent=2).encode("utf-8")
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("影片备注总量过大")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".movie-notes-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
