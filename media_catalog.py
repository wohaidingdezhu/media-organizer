"""Report-only photo catalog, movie notes and comparable scan changes."""
import json
import os
from pathlib import Path
import stat
import tempfile

from file_operations import open_directory
from media_actions import media_id


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
