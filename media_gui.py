"""Local browser dashboard for the read-only media scanner."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import webbrowser

import media_scan as scan
from library_server import create_library_server


BASE = Path(__file__).resolve().parent
REPORTS = BASE / "reports"
MAX_REQUEST = 4096


def latest_library(output=REPORTS):
    reports = (path for path in Path(output).glob("scan-*")
               if path.is_dir() and not path.is_symlink()
               and (path / "library.html").is_file() and (path / "report.json").is_file())
    return max(reports, default=None, key=lambda path: path.name)


def scan_arguments(folders, *, output=REPORTS, image_analysis=True, video_covers=True,
                   video_headers=False, video_rule="auto"):
    if not folders:
        raise ValueError("请先添加至少一个照片或视频文件夹")
    if video_rule not in {"auto", "name", "folder"}:
        raise ValueError("视频分类规则无效")
    args = [str(BASE / "media_scan.py"), *map(str, folders), "--output", str(output),
            "--video-rule", video_rule]
    if not image_analysis:
        args.append("--no-image-metadata")
    elif not video_covers:
        args.append("--no-video-covers")
    if video_headers:
        args.append("--check-video-headers")
    return args


def compile_helpers(log, image_analysis=True, video_covers=True):
    compiler = shutil.which("swiftc")
    wanted = [("image_probe", "照片解析")] if image_analysis else []
    if image_analysis and video_covers:
        wanted.append(("video_cover", "视频封面"))
    for name, description in wanted:
        source, target = BASE / "native" / f"{name}.swift", BASE / "native" / name
        if target.is_file() and os.access(target, os.X_OK) and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            continue
        if compiler is None:
            log(f"未找到 Swift 编译器；{description}将不可用。")
            continue
        log(f"正在准备{description}组件…")
        try:
            result = subprocess.run([compiler, str(source), "-o", str(target)], cwd=BASE,
                                    capture_output=True, text=True, timeout=180, check=False)
            if result.returncode:
                log(f"{description}组件未编译成功；文件查重仍可继续。")
                if result.stderr.strip():
                    log(result.stderr.strip()[-1200:])
        except (OSError, subprocess.TimeoutExpired) as error:
            log(f"{description}组件不可用：{error}")


class DashboardState:
    def __init__(self, output=REPORTS):
        self.output = Path(output)
        self.lock = threading.RLock()
        self.folders = []
        self.logs = deque(maxlen=300)
        self.running = False
        self.status = "准备就绪。请先添加照片或视频文件夹。"
        self.process = None
        self.cancel_requested = threading.Event()
        self.library_server = None
        self.library_report = None
        self.library_url = None

    def log(self, line):
        with self.lock:
            self.logs.append(str(line).rstrip())

    def snapshot(self):
        with self.lock:
            report = latest_library(self.output)
            return {"folders": list(self.folders), "running": self.running,
                    "status": self.status, "logs": list(self.logs),
                    "latest_report": report.name if report else None}

    def add_folders(self):
        choices = scan.choose_folders()
        with self.lock:
            if self.running:
                raise ValueError("扫描进行中，不能更改文件夹")
            for choice in choices:
                path = Path(choice).expanduser().absolute()
                if path.is_symlink() or not path.is_dir():
                    raise ValueError("请选择实际存在的普通文件夹，不要选择符号链接")
                if str(path) not in self.folders:
                    self.folders.append(str(path))
        return self.snapshot()

    def remove_folder(self, index):
        with self.lock:
            if self.running:
                raise ValueError("扫描进行中，不能更改文件夹")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(self.folders):
                raise ValueError("文件夹序号无效")
            self.folders.pop(index)
        return self.snapshot()

    def start_scan(self, options):
        if not isinstance(options, dict):
            raise ValueError("扫描选项无效")
        for key in ("image_analysis", "video_covers", "video_headers"):
            if key in options and not isinstance(options[key], bool):
                raise ValueError("扫描选项无效")
        image_analysis = options.get("image_analysis", True)
        video_covers = options.get("video_covers", True)
        video_headers = options.get("video_headers", False)
        video_rule = options.get("video_rule", "auto")
        with self.lock:
            if self.running:
                raise ValueError("已有扫描正在进行")
            args = scan_arguments(self.folders, output=self.output, image_analysis=image_analysis,
                                  video_covers=video_covers, video_headers=video_headers,
                                  video_rule=video_rule)
            self.running = True
            self.cancel_requested.clear()
            self.logs.clear()
            self.status = "正在准备组件并扫描；原始媒体不会被修改。"
        threading.Thread(target=self._run_scan, args=(args, image_analysis, video_covers), daemon=True).start()
        return self.snapshot()

    def _run_scan(self, args, image_analysis, video_covers):
        try:
            compile_helpers(self.log, image_analysis, video_covers)
            if self.cancel_requested.is_set():
                code = 130
            else:
                process = subprocess.Popen([sys.executable, "-u", *args], cwd=BASE,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1)
                with self.lock:
                    self.process = process
                    if self.cancel_requested.is_set():
                        process.send_signal(signal.SIGINT)
                for line in process.stdout:
                    self.log(line)
                code = process.wait()
            with self.lock:
                if code == 0:
                    self.status = "扫描完成。点击“打开影片资料库”查看海报墙和标签。"
                elif code == 130 or self.cancel_requested.is_set():
                    self.status = "扫描已取消；原始媒体没有被修改。"
                else:
                    self.status = "扫描未完成；请查看下面的记录。"
        except (OSError, ValueError) as error:
            with self.lock:
                self.status = f"扫描无法启动：{error}"
            self.log(error)
        finally:
            with self.lock:
                self.process = None
                self.running = False

    def cancel_scan(self):
        self.cancel_requested.set()
        with self.lock:
            process = self.process
            if self.running:
                self.status = "正在取消扫描…"
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
        return self.snapshot()

    def open_library(self):
        report = latest_library(self.output)
        if report is None:
            raise ValueError("尚无资料库，请先完成一次扫描")
        with self.lock:
            if self.library_server is None or self.library_report != report:
                self.close_library()
                self.library_server, self.library_url = create_library_server(report, self.output)
                self.library_report = report
                threading.Thread(target=self.library_server.serve_forever, daemon=True).start()
            return {"url": self.library_url}

    def close_library(self):
        if self.library_server is not None:
            self.library_server.shutdown()
            self.library_server.server_close()
            self.library_server = None
            self.library_report = None
            self.library_url = None

    def close(self):
        self.cancel_scan()
        with self.lock:
            self.close_library()


def create_dashboard_server(output=REPORTS):
    state = DashboardState(output)
    token = secrets.token_urlsafe(24)
    page = (BASE / "dashboard.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def route(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return None
            path = urlsplit(self.path).path
            prefix = f"/{token}/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def respond(self, code, body, content_type="application/json; charset=utf-8"):
            if content_type.startswith("application/json"):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.route()
            if route == "":
                self.respond(200, page, "text/html; charset=utf-8")
            elif route == "api/status":
                self.respond(200, state.snapshot())
            else:
                self.send_error(404)

        def do_POST(self):
            route = self.route()
            if route not in {"api/folders/add", "api/folders/remove", "api/scan/start",
                             "api/scan/cancel", "api/library/open", "api/reports/open", "api/quit"}:
                self.send_error(404)
                return
            if self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}":
                self.respond(403, {"error": "页面来源不匹配"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self.respond(415, {"error": "需要 JSON 请求"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    raise ValueError("请求过大或为空")
                data = json.loads(self.rfile.read(length))
                if route == "api/folders/add":
                    result = state.add_folders()
                elif route == "api/folders/remove":
                    result = state.remove_folder(data["index"])
                elif route == "api/scan/start":
                    result = state.start_scan(data)
                elif route == "api/scan/cancel":
                    result = state.cancel_scan()
                elif route == "api/library/open":
                    result = state.open_library()
                elif route == "api/reports/open":
                    state.output.mkdir(parents=True, exist_ok=True, mode=0o700)
                    subprocess.Popen(["/usr/bin/open", str(state.output)])
                    result = {"ok": True}
                else:
                    result = {"ok": True}
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self.respond(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server, state, f"http://127.0.0.1:{server.server_port}/{token}/"


def main():
    server, state, url = create_dashboard_server()
    print(f"媒体整理助手本机地址：{url}", flush=True)
    print("关闭控制台请点击页面中的“退出”。", flush=True)
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
        state.close()
        server.server_close()


if __name__ == "__main__":
    main()
