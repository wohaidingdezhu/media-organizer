# coding: utf-8
"""One native window on both desktops, using the shared loopback dashboard."""
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
from urllib.request import Request, urlopen

from system_integration import application_data_directory, hidden_process_options


def backend_command():
    if getattr(sys, 'frozen', False):
        executable = Path(__file__).resolve().parent / 'native' / ('media-backend.exe' if sys.platform == 'win32' else 'media-backend')
        return [str(executable)]
    return [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('desktop_backend.py'))]


def local_url(value):
    if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}/[A-Za-z0-9_-]{24,80}/', value):
        raise ValueError('本机服务地址无效')
    return value


def request_shutdown(url):
    url = local_url(url)
    origin = url.split('/', 3)[:3]
    request = Request(url + 'api/quit', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': '/'.join(origin)})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--ui-smoke-test', action='store_true')
    args = parser.parse_args(argv)
    if args.smoke_test:
        return subprocess.call([*backend_command(), '--smoke-test'], **hidden_process_options())
    import webview
    import tempfile
    import time
    temporary = tempfile.TemporaryDirectory() if args.ui_smoke_test else None
    output = Path(temporary.name).resolve() / 'reports' if temporary else args.output or application_data_directory()
    ui_result = [False]
    process = subprocess.Popen([*backend_command(), '--dashboard', '--output', str(output)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding='utf-8', **hidden_process_options())
    readiness = queue.Queue()
    diagnostics = []
    def read_output():
        for line in process.stdout:
            if line.startswith('READY '):
                readiness.put(line[6:].strip())
            else:
                diagnostics.append(line.rstrip())
        readiness.put(None)
    threading.Thread(target=read_output, daemon=True).start()
    try:
        ready_url = local_url(readiness.get(timeout=30))
    except (queue.Empty, ValueError, TypeError):
        # A backend which has not announced readiness cannot accept media jobs.
        process.terminate()
        process.wait(timeout=15)
        raise RuntimeError('本机服务启动失败：' + '\n'.join(diagnostics[-10:]))
    window = webview.create_window('媒体整理助手', url=ready_url, width=1280, height=900, min_size=(780, 600))
    url, allow_close, stopping = [ready_url], threading.Event(), threading.Event()
    def message(text):
        window.evaluate_js("document.getElementById('notice').textContent=" + json.dumps(text))
    def serve():
        process.wait()
        allow_close.set()
        window.destroy()
    def closing():
        if allow_close.is_set():
            return True
        if not url[0]:
            # Wait for startup instead of terminating a possibly active backend.
            message('应用仍在启动，请稍后关闭。')
            return False
        if not stopping.is_set():
            stopping.set()
            def stop():
                try:
                    request_shutdown(url[0])
                except Exception as error:
                    message('暂不能退出；请先安全停止正在进行的文件操作。' + str(error))
                    stopping.clear()
            threading.Thread(target=stop, daemon=True).start()
        return False
    if args.ui_smoke_test:
        def verify_ui():
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                time.sleep(.5)
                if url[0] and window.get_current_url() == url[0]:
                    try:
                        present = window.evaluate_js("!!document.getElementById('nav-catalog') && !!document.getElementById('restore-file') && !!document.getElementById('monitor-save')")
                        if not present:
                            continue
                        window.evaluate_js("document.getElementById('nav-catalog').click()")
                        time.sleep(1)
                        ui_result[0] = bool(window.evaluate_js("!document.getElementById('catalog-pane').hidden && document.getElementById('catalog-summary').textContent.includes('长期清单')"))
                        request_shutdown(url[0])
                        return
                    except Exception:
                        pass
            # The smoke backend has no source folders and cannot perform file jobs.
            process.terminate()
            allow_close.set()
            window.destroy()
        window.events.shown += lambda: threading.Thread(target=verify_ui, daemon=True).start()
    window.events.closing += closing
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.start(serve, private_mode=True, gui='edgechromium' if sys.platform == 'win32' else 'cocoa')
    if temporary:
        temporary.cleanup()
        return 0 if ui_result[0] else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
