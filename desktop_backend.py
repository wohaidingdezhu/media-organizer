# coding: utf-8
"""Frozen backend dispatch: keep worker stdout separate from the native shell."""
import sys


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--scan':
        import media_scan
        return media_scan.main(argv[1:])
    if argv and argv[0] == '--worker':
        if len(argv) not in {2, 3} or argv[1] not in {'portable_image_probe', 'portable_video_cover'}:
            return 2
        if len(argv) == 3 and argv[2] != '--batch':
            return 2
        import portable_image_probe
        if argv[1] == 'portable_image_probe':
            portable_image_probe.batch(portable_image_probe.probe)
        else:
            import portable_video_cover
            portable_image_probe.batch(portable_video_cover.probe)
        return 0
    if argv and argv[0] == '--choose-directory':
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(title=argv[1] if len(argv) > 1 else '选择目录', mustexist=True)
        root.destroy()
        print(path, flush=True)
        return 0
    if argv == ['--smoke-test']:
        from desktop_smoke import run
        run()
        return 0
    import argparse
    from pathlib import Path
    import media_gui
    parser = argparse.ArgumentParser()
    parser.add_argument('--dashboard', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    server, state, url = media_gui.create_dashboard_server(args.output)
    print('READY ' + url, flush=True)
    try:
        server.serve_forever()
    finally:
        state.close()
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
