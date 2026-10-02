"""Loopback-only report viewer and persistent film tags. No media writes."""
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
from urllib.parse import unquote, urlsplit
import webbrowser


MAX_TAG_FILE = 1024 * 1024
MAX_REQUEST = 16 * 1024


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
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".library-tags-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": 1, "groups": groups}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_library_server(report_dir, output_dir):
    report_dir = Path(report_dir).resolve(strict=True)
    output_dir = Path(output_dir).resolve(strict=True)
    if not report_dir.is_dir() or output_dir not in report_dir.parents:
        raise ValueError("影片报告目录无效")
    report = report_dir / "report.json"
    with report.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    allowed_keys = {group["tag_key"] for group in document["video_library"]["groups"]}
    tag_path = output_dir / "library-tags.json"
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def route(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return None
            path = unquote(urlsplit(self.path).path)
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

        def do_GET(self):
            route = self.route()
            if route is None:
                self.send_error(404)
                return
            if route == "api/tags":
                try:
                    tags = load_tags(tag_path)
                except (OSError, ValueError) as error:
                    self.send_json(500, {"error": str(error)})
                    return
                self.send_json(200, {"groups": {key: tags.get(key, []) for key in allowed_keys}})
                return
            if route == "":
                route = "library.html"
            parts = Path(route).parts
            if not parts or any(part in {"..", "."} or part.startswith(".") for part in parts):
                self.send_error(404)
                return
            if not (len(parts) == 1 and Path(route).suffix.lower() in {".html", ".json", ".csv"}
                    or len(parts) == 2 and parts[0] in {"covers", "previews"} and Path(route).suffix.lower() == ".png"):
                self.send_error(404)
                return
            try:
                target = (report_dir / route).resolve(strict=True)
                if report_dir not in target.parents or not target.is_file():
                    raise FileNotFoundError
                descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except (OSError, FileNotFoundError):
                self.send_error(404)
                return
            with os.fdopen(descriptor, "rb") as stream:
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile)

        def do_POST(self):
            if self.route() != "api/tags":
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
    server.tag_lock = threading.Lock()
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
