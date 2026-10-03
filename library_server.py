"""Loopback-only reports, organizing plans and film tags. No media writes."""
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit
import webbrowser

from organization_plan import OrganizationPlan


MAX_TAG_FILE = 1024 * 1024
MAX_REQUEST = 16 * 1024
_TAG_LOCKS = {}
_TAG_LOCKS_LOCK = threading.Lock()


def clean_tags(values):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("标签必须是最多 20 项的列表")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("标签必须是文字")
        tag = value.strip()
        if not 1 <= len(tag) <= 32 or any(ord(char) < 32 for char in tag):
            raise ValueError("每个标签应为 1–32 个可见字符")
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def load_tags(path):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TAG_FILE:
            raise ValueError("标签文件不是常规文件或过大")
        raw = stream.read(MAX_TAG_FILE + 1)
    if len(raw) > MAX_TAG_FILE:
        raise ValueError("标签文件过大")
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"标签文件格式错误：{error}") from error
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("groups"), dict):
        raise ValueError("标签文件格式错误")
    groups = {}
    for key, values in document["groups"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("标签文件含无效影片标识")
        groups[key] = clean_tags(values)
    return groups


def save_tags(path, groups):
    path = Path(path)
    body = json.dumps({"version": 1, "groups": groups}, ensure_ascii=False, indent=2).encode("utf-8")
    if len(body) > MAX_TAG_FILE:
        raise ValueError("标签总量过大，请先减少部分标签")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                         prefix=".library-tags-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _dashboard_link(url):
    if url is None:
        return b""
    if not isinstance(url, str) or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("控制台地址无效")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                 and parsed.port is not None and parsed.port > 0
                 and parsed.netloc == f"127.0.0.1:{parsed.port}")
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("控制台地址必须是本机 HTTP 地址")
    return (f'<nav aria-label="报告导航" style="padding:12px 20px;background:#102b36;'
            f'border-bottom:1px solid #45606a;font:600 15px/1.5 system-ui,sans-serif">'
            f'<a href="{html.escape(url, quote=True)}" target="_top" '
            f'style="display:inline-block;color:#fff;text-decoration:underline;'
            f'text-underline-offset:3px">← 返回控制台</a></nav>').encode("utf-8")


def _open_report_file(report_dir, parts):
    """Open beneath the report directory without following file/directory links."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    directory = os.open(report_dir, flags | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[:-1]:
            child = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], flags, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OSError("报告文件不是常规文件")
        return os.fdopen(descriptor, "rb")
    finally:
        os.close(directory)


def create_library_server(report_dir, output_dir, *, dashboard_url=None):
    dashboard_link = _dashboard_link(dashboard_url)
    report_dir = Path(report_dir).resolve(strict=True)
    output_dir = Path(output_dir).resolve(strict=True)
    if not report_dir.is_dir() or output_dir not in report_dir.parents:
        raise ValueError("影片报告目录无效")
    with _open_report_file(report_dir, ("report.json",)) as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError("报告内容无效")
    organization = None
    organization_lock = threading.Lock()

    def get_organization():
        nonlocal organization
        with organization_lock:
            if organization is None:
                organization = OrganizationPlan(report_dir, document)
            return organization
    library = document.get("video_library")
    groups = library.get("groups", []) if isinstance(library, dict) else []
    allowed_keys = {group["tag_key"] for group in groups if isinstance(group, dict)
                    and isinstance(group.get("tag_key"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", group["tag_key"])} if isinstance(groups, list) else set()
    tag_path = output_dir / "library-tags.json"
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def route(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return None
            try:
                path = unquote(urlsplit(self.path).path)
            except ValueError:
                return None
            prefix = f"/{token}/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_export(self, body, content_type, filename):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.route()
            if route is None:
                self.send_error(404)
                return
            if route in {"api/organization", "organization.csv", "organization.json"}:
                try:
                    organization = get_organization()
                    if route == "api/organization":
                        self.send_json(200, organization.snapshot())
                    elif route == "organization.csv":
                        self.send_export(organization.export_csv(), "text/csv; charset=utf-8", "organization.csv")
                    else:
                        body = json.dumps(organization.export_document(), ensure_ascii=False,
                                          indent=2).encode("utf-8")
                        self.send_export(body, "application/json; charset=utf-8", "organization.json")
                except (OSError, ValueError) as error:
                    self.send_json(500, {"error": str(error)})
                return
            if route == "api/tags":
                try:
                    tags = load_tags(tag_path) if allowed_keys else {}
                except (OSError, ValueError) as error:
                    self.send_json(500, {"error": str(error)})
                    return
                self.send_json(200, {"groups": {key: tags.get(key, []) for key in allowed_keys},
                                     "editable": bool(allowed_keys)})
                return
            if route == "":
                route = "library.html"
            parts = Path(route).parts
            if (not parts or Path(route).is_absolute() or "\x00" in route
                    or any(part in {"..", "."} or part.startswith(".") for part in parts)):
                self.send_error(404)
                return
            if not (len(parts) == 1 and Path(route).suffix.lower() in {".html", ".json", ".csv"}
                    or len(parts) == 2 and parts[0] in {"covers", "previews"} and Path(route).suffix.lower() == ".png"):
                self.send_error(404)
                return
            try:
                page_dir = Path(__file__).resolve().parent if route == "organize.html" else report_dir
                stream = _open_report_file(page_dir, ("organization.html",) if route == "organize.html" else parts)
            except OSError:
                self.send_error(404)
                return
            with stream:
                body = None
                if dashboard_link and route in {"report.html", "library.html", "organize.html"}:
                    body = stream.read()
                    opening = re.search(br"<body(?:\s[^>]*)?>", body, re.IGNORECASE)
                    point = opening.end() if opening else 0
                    body = body[:point] + dashboard_link + body[point:]
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(parts[-1])[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body) if body is not None else os.fstat(stream.fileno()).st_size))
                if Path(route).suffix.lower() == ".csv":
                    fallback = re.sub(r"[^A-Za-z0-9_.-]", "_", parts[-1])
                    self.send_header("Content-Disposition", f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(parts[-1], safe="")}')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if body is not None:
                    self.wfile.write(body)
                else:
                    shutil.copyfileobj(stream, self.wfile)

        def do_POST(self):
            route = self.route()
            if route not in {"api/tags", "api/organization"}:
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                self.send_json(403, {"error": "页面来源不匹配"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self.send_json(415, {"error": "需要 JSON 请求"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    raise ValueError("请求过大或为空")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求内容必须是 JSON 对象")
                if route == "api/organization":
                    result = get_organization().set_states(payload["ids"], payload["state"])
                    self.send_json(200, result)
                    return
                key = payload["key"]
                if key not in allowed_keys:
                    raise ValueError("影片标识无效")
                values = clean_tags(payload["tags"])
                with self.server.tag_lock:
                    groups = load_tags(tag_path)
                    if values:
                        groups[key] = values
                    else:
                        groups.pop(key, None)
                    save_tags(tag_path, groups)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})
                return
            self.send_json(200, {"tags": values})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    with _TAG_LOCKS_LOCK:
        server.tag_lock = _TAG_LOCKS.setdefault(tag_path, threading.Lock())
    return server, f"http://127.0.0.1:{server.server_port}/{token}/library.html"


def serve_library(report_dir, output_dir):
    server, url = create_library_server(report_dir, output_dir)
    print("本地资料库已打开；可编辑标签。关闭此终端或按 Ctrl+C 停止访问，已保存的标签会保留。", flush=True)
    if sys.platform == "darwin":
        try:
            subprocess.run(["/usr/bin/open", url], check=True, timeout=15)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            webbrowser.open(url)
    else:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
