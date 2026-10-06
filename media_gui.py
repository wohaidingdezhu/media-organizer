"""Local browser dashboard for the read-only media scanner."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import portable_fs as os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
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
MAX_REPORT_BYTES = 128 * 1024 * 1024
MAX_REPORT_HISTORY = 50
REPORT_VIEWS = {
    "overview": "report.html", "duplicates": "report.html#duplicates",
    "similar": "report.html#similar", "library": "library.html",
    "issues": "report.html#issues", "folders": "report.html#folder-groups",
    "organize": "organize.html", "photos": "photos.html",
}


def selected_report(output, report_id):
    """Resolve only one ordinary report directory directly under the output root."""
    if not isinstance(report_id, str) or not re.fullmatch(r"scan-[A-Za-z0-9_-]{1,120}", report_id):
        raise ValueError("扫描记录标识无效")
    output = Path(output)
    if output.is_symlink():
        raise ValueError("报告目录不能是符号链接")
    root = output.resolve(strict=True)
    report = root / report_id
    if report.is_symlink() or not report.is_dir() or report.resolve(strict=True).parent != root:
        raise ValueError("扫描记录不存在或不是普通目录")
    return report


def report_signature(report):
    signature = []
    for name in ("report.json", "report.html"):
        info = (report / name).lstat()
        if not stat.S_ISREG(info.st_mode) or not info.st_size:
            raise ValueError("扫描报告尚未完整生成")
        signature.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
    try:
        info = (report / "library.html").lstat()
    except FileNotFoundError:
        signature.append(None)
    else:
        if not stat.S_ISREG(info.st_mode) or not info.st_size:
            raise ValueError("影片资料库尚未完整生成")
        signature.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
    return tuple(signature)


def summarize_report(report):
    """Read scan output only, never the source media named in the report."""
    directory = os.open(report, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open("report.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REPORT_BYTES:
                raise ValueError("扫描报告不是普通文件或过大")
            raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError("扫描报告过大")
        data = json.loads(raw)
    finally:
        os.close(directory)
    if not isinstance(data, dict):
        raise ValueError("扫描报告格式无效")
    roots, files = data.get("roots"), data.get("files")
    duplicates, issues, similar = data.get("duplicates"), data.get("issues"), data.get("similar")
    if (not isinstance(data.get("created_at"), str) or not isinstance(roots, list)
            or not all(isinstance(root, str) for root in roots)
            or not isinstance(files, list) or not all(isinstance(item, dict) for item in files)
            or not isinstance(duplicates, list) or not isinstance(issues, list)
            or not isinstance(similar, dict) or not isinstance(similar.get("pairs"), list)):
        raise ValueError("扫描报告缺少有效的结果数据")
    library = data.get("video_library", {})
    posters = library.get("poster_count", 0) if isinstance(library, dict) else 0
    if not isinstance(posters, int) or isinstance(posters, bool) or posters < 0:
        posters = 0
    library_page = report / "library.html"
    has_library = library_page.is_file() and not library_page.is_symlink()
    if "video_library" in data and not has_library:
        raise ValueError("影片资料库尚未完整生成")
    return {"id": report.name, "created_at": data["created_at"], "roots": roots,
            "has_library": has_library,
            "summary": {"files": len(files), "photos": sum(item.get("kind") == "照片" for item in files),
                        "videos": sum(item.get("kind") == "视频" for item in files),
                        "planned_files": sum(isinstance(item.get("suggested_path"), str)
                                             and bool(item["suggested_path"].strip()) for item in files),
                        "duplicate_groups": len(duplicates), "similar_pairs": len(similar["pairs"]),
                        "issues": len(issues), "poster_count": posters}}


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
    if sys.platform == "win32":
        import media_backend
        for name, enabled in (("image_probe", image_analysis), ("video_cover", image_analysis and video_covers)):
            if enabled and not scan.helper_available(media_backend.helper(name)):
                log("缺少图片或视频依赖；请运行 python -m pip install -r requirements-windows.txt。精确查重仍可继续。")
        return
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
        self.cancel_file = self.output / (".scan-cancel-" + secrets.token_hex(12))
        self.library_server = None
        self.library_report = None
        self.library_url = None
        self.report_servers = {}
        self.report_cache = {}
        self.dashboard_url = None

    def log(self, line):
        with self.lock:
            self.logs.append(str(line).rstrip())

    def _report_summary(self, report_id):
        report = selected_report(self.output, report_id)
        signature = report_signature(report)
        cached = self.report_cache.get(report_id)
        if cached is not None and cached[0] == signature:
            return report, cached[1]
        summary = summarize_report(report)
        if report_signature(report) != signature:
            raise ValueError("扫描报告正在更新，请稍后重试")
        self.report_cache[report_id] = (signature, summary)
        return report, summary

    def report_history(self):
        with self.lock:
            reports, skipped = [], 0
            candidates = sorted(self.output.glob("scan-*"), key=lambda path: path.name, reverse=True)
            has_more = False
            for candidate in candidates:
                try:
                    _, summary = self._report_summary(candidate.name)
                except (OSError, ValueError, UnicodeError):
                    skipped += 1
                    self.report_cache.pop(candidate.name, None)
                    continue
                if len(reports) >= MAX_REPORT_HISTORY:
                    has_more = True
                    break
                reports.append(summary)
            active_ids = {candidate.name for candidate in candidates}
            self.report_cache = {key: value for key, value in self.report_cache.items() if key in active_ids}
            return {"reports": reports, "skipped_reports": skipped, "has_more": has_more}

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
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or not path.is_dir():
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
                environment = dict(os.environ, PYTHONUTF8="1")
                if sys.platform == "win32":
                    environment["MEDIA_ORGANIZER_CANCEL_FILE"] = str(self.cancel_file.absolute())
                process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", *args], cwd=BASE,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", bufsize=1, env=environment)
                with self.lock:
                    self.process = process
                    if self.cancel_requested.is_set():
                        self._signal_scan(process)
                for line in process.stdout:
                    self.log(line)
                code = process.wait()
            with self.lock:
                if code == 0:
                    history = self.report_history()["reports"]
                    if history:
                        summary = history[0]["summary"]
                        self.status = (f"扫描完成：{summary['files']} 个媒体文件，"
                                       f"{summary['planned_files']} 项分类建议，"
                                       f"{summary['duplicate_groups']} 组精确重复。"
                                       "打开“文件管理与整理”查看原文件和建议目录。")
                    else:
                        self.status = "扫描已结束，但未找到完整结果；请查看下面的扫描记录。"
                elif code == 130 or self.cancel_requested.is_set():
                    self.status = "扫描已取消；原始媒体没有被修改。"
                else:
                    self.status = "扫描未完成；请查看下面的记录。"
        except (OSError, ValueError) as error:
            with self.lock:
                self.status = f"扫描无法启动：{error}"
            self.log(error)
        finally:
            self.cancel_file.unlink(missing_ok=True)
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
            self._signal_scan(process)
        return self.snapshot()

    def _signal_scan(self, process):
        if sys.platform == "win32":
            self.output.mkdir(parents=True, exist_ok=True)
            self.cancel_file.touch()
        else:
            process.send_signal(signal.SIGINT)

    def view_report(self, report_id, view="overview"):
        if not isinstance(view, str) or view not in REPORT_VIEWS:
            raise ValueError("报告视图无效")
        with self.lock:
            report, summary = self._report_summary(report_id)
            if view == "library" and not summary["has_library"]:
                raise ValueError("这次扫描没有影片资料库，请查看总览或重新扫描")
            if report_id not in self.report_servers:
                server, library_url = create_library_server(report, self.output, dashboard_url=self.dashboard_url)
                self.report_servers[report_id] = (server, library_url)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            server, library_url = self.report_servers[report_id]
            self.library_server, self.library_report, self.library_url = server, report, library_url
            return {"url": library_url.rsplit("/", 1)[0] + "/" + REPORT_VIEWS[view], "report_id": report_id}

    def open_library(self):
        history = self.report_history()
        report = next((item for item in history["reports"] if item["has_library"]), None)
        if report is None:
            raise ValueError("尚无资料库，请先完成一次扫描")
        return self.view_report(report["id"], "library")

    def close_library(self):
        for server, _ in self.report_servers.values():
            server.shutdown()
            server.server_close()
        self.report_servers.clear()
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
            try:
                path = urlsplit(self.path).path
            except ValueError:
                return None
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
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src http://127.0.0.1:*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.route()
            if route == "":
                self.respond(200, page, "text/html; charset=utf-8")
            elif route == "api/status":
                self.respond(200, state.snapshot())
            elif route == "api/reports":
                self.respond(200, state.report_history())
            else:
                self.send_error(404)

        def do_POST(self):
            route = self.route()
            if route not in {"api/folders/add", "api/folders/remove", "api/scan/start",
                             "api/scan/cancel", "api/library/open", "api/reports/open", "api/reports/view", "api/quit"}:
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
                if not isinstance(data, dict):
                    raise ValueError("请求内容必须是 JSON 对象")
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
                elif route == "api/reports/view":
                    result = state.view_report(data["report_id"], data.get("view", "overview"))
                elif route == "api/reports/open":
                    state.output.mkdir(parents=True, exist_ok=True, mode=0o700)
                    from system_integration import open_path
                    open_path(state.output)
                    result = {"ok": True}
                else:
                    from file_operations import shutdown_when_idle
                    def stop():
                        self.server.shutdown()
                        state.close()
                    shutdown_when_idle(stop)
                    result = {"ok": True}
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self.respond(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.dashboard_url = f"http://127.0.0.1:{server.server_port}/{token}/"
    return server, state, state.dashboard_url


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
