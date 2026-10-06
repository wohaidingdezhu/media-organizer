"""Native desktop integration, kept separate from scanning and report data."""
import os
from pathlib import Path
import subprocess
import sys


def open_path(path, reveal=False):
    path = str(Path(path).absolute())
    try:
        if sys.platform == "win32":
            if reveal:
                # Separate argv; never invoke cmd.exe or interpolate a shell command.
                subprocess.Popen([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe"),
                                  "/select,", path])
            else:
                os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["/usr/bin/open", *(["-R"] if reveal else []), path], check=True, timeout=15)
        else:
            raise ValueError("系统打开功能支持 macOS 和 Windows")
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法打开文件，请检查系统默认播放器或查看程序") from error


def choose_directory(prompt):
    if sys.platform == "darwin":
        script = 'POSIX path of (choose folder with prompt "' + prompt.replace('"', '') + '")'
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=180)
        if result.returncode:
            if "-128" in result.stderr:
                return None
            raise ValueError("无法选择文件夹")
        return result.stdout.strip()
    if sys.platform == "win32":
        # Python's bundled Tcl/Tk picker runs in its own main thread/process.
        code = ('import sys,tkinter as tk; from tkinter import filedialog; '
                'root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True); '
                'path=filedialog.askdirectory(title=sys.argv[1],mustexist=True); '
                'root.destroy(); print(path)')
        try:
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, prompt],
                                    capture_output=True, text=True, encoding="utf-8", timeout=180)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("无法选择文件夹，请使用含 Tcl/Tk 的 Python 安装") from error
        if result.returncode:
            raise ValueError("无法选择文件夹，请安装 Python 的 Tcl/Tk 组件")
        return result.stdout.strip() or None
    raise ValueError("目录选择支持 macOS 和 Windows；命令行可直接提供路径")


def recycle_available():
    try:
        # Require modern IFileOperation. Never fall back to SHFileOperation.
        from send2trash.win import modern  # noqa: F401
    except ImportError as error:
        raise ValueError("Windows 回收站功能需要依赖：python -m pip install -r requirements-windows.txt") from error


def recycle_file(path, expected):
    recycle_available()
    from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
    import pythoncom
    import pywintypes
    from win32com.shell import shell, shellcon
    from media_actions import checked_stat, file_signature
    from file_operations import OutcomeUnknown

    class CheckedSink(FileOperationProgressSink):
        def PreDeleteItem(self, flags, item):
            try:
                same = file_signature(checked_stat(path)) == expected
            except (OSError, ValueError):
                same = False
            # Reject operations that the Shell would permanently delete.
            if not same or not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                raise pythoncom.com_error(0x80004005, "文件已变化或无法放入回收站", None, None)
            return 0

    pythoncom.CoInitialize()
    sink = CheckedSink()
    try:
        fileop = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                                           pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
        fileop.SetOperationFlags(shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI |
                                 shellcon.FOF_SILENT | shellcon.FOFX_EARLYFAILURE |
                                 0x20000000 | 0x00080000)
        item = shell.SHCreateItemFromParsingName(path, None, shell.IID_IShellItem)
        fileop.DeleteItem(item, pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink))
        result = fileop.PerformOperations()
        if result or fileop.GetAnyOperationsAborted() or not sink.newItem:
            raise OSError("系统未确认文件已进入回收站")
        return {"trashed_path": sink.newItem}
    except (OSError, pywintypes.com_error) as error:
        try:
            unchanged = file_signature(checked_stat(path)) == expected
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            raise OutcomeUnknown("系统操作结果未确认，请在资源管理器和回收站核对后再处理") from error
        raise ValueError("无法放入回收站，原文件保留：" + str(error)) from error
    finally:
        pythoncom.CoUninitialize()
