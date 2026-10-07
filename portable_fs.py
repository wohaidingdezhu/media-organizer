"""OS facade for the scanner's handle-relative filesystem operations.

POSIX calls pass through unchanged. On Windows, directory handles pin directories
against replacement; NtCreateFile opens one component relative to those handles,
rejecting all reparse points (including junctions). No process-wide monkeypatches.
"""
import os as _os
import sys
from contextlib import contextmanager
from pathlib import Path


def __getattr__(name):
    return getattr(_os, name)


def private_data_path(path):
    """Expand only macOS system aliases; reject user-controlled links later."""
    path = Path(path).absolute()
    if sys.platform == 'darwin' and len(path.parts) > 1 and path.parts[1] in {'var', 'tmp'}:
        path = Path('/private').joinpath(*path.parts[1:])
    return path


def ensure_private_directory(path):
    """Create ordinary data directories using pinned parents on either OS."""
    from file_operations import open_directory
    api = sys.modules[__name__]
    path = private_data_path(path)
    if '..' in path.parts:
        raise ValueError('数据目录不能包含上级路径')
    parent = open_directory(path.anchor)
    try:
        for part in path.parts[1:]:
            try:
                child = api.open(part, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                try:
                    api.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = api.open(part, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW, dir_fd=parent)
            api.close(parent)
            parent = child
    finally:
        api.close(parent)
    return path


def read_private_file(root, parts, limit=128 * 1024 * 1024):
    import stat as file_stat
    from file_operations import open_directory
    api = sys.modules[__name__]
    parts = tuple(parts)
    if not parts or any(not part or part in {'.', '..'} or '/' in part or '\\' in part for part in parts):
        raise ValueError('资料路径无效')
    parent = open_directory(private_data_path(root))
    try:
        for part in parts[:-1]:
            child = api.open(part, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW, dir_fd=parent)
            api.close(parent)
            parent = child
        descriptor = api.open(parts[-1], api.O_RDONLY | api.O_NOFOLLOW | api.O_NONBLOCK, dir_fd=parent)
        with api.fdopen(descriptor, 'rb') as stream:
            before = api.fstat(stream.fileno())
            if not file_stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('资料不是普通文件或过大')
            body = stream.read(limit + 1)
            after = api.fstat(stream.fileno())
            if len(body) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('资料过大或正在变化')
            return body
    finally:
        api.close(parent)


def write_private_file(root, name, body):
    import secrets
    import stat as file_stat
    from file_operations import open_directory
    api = sys.modules[__name__]
    if not name or name in {'.', '..'} or '/' in name or '\\' in name:
        raise ValueError('资料名称无效')
    parent = open_directory(private_data_path(root))
    temporary = '.data-' + secrets.token_hex(16)
    try:
        try:
            info = api.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not file_stat.S_ISREG(info.st_mode):
                raise ValueError('拒绝覆盖链接或非普通资料文件')
        descriptor = api.open(temporary, api.O_WRONLY | api.O_CREAT | api.O_EXCL, 0o600, dir_fd=parent)
        with api.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            api.fsync(stream.fileno())
        api.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            api.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        api.close(parent)


def open_lock(name, directory):
    """Create once, then open the existing lock without truncating its header.

    Separate exclusive creation from opening: concurrent O_CREAT opens can
    transiently fail with ENOENT on macOS. Both paths reject links/reparse points.
    """
    if _os.name == "nt":
        flags, opener = _os.O_RDWR | O_NOFOLLOW | O_NONBLOCK, open
    else:
        flags, opener = _os.O_RDWR | _os.O_NOFOLLOW | _os.O_NONBLOCK, _os.open
    try:
        return opener(name, flags | _os.O_CREAT | _os.O_EXCL, 0o600, dir_fd=directory)
    except FileExistsError:
        return opener(name, flags, dir_fd=directory)


def publish(source, target, directory):
    if _os.name == "nt":
        _rename(source, target, directory, directory, False)
    else:
        _os.link(source, target, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)


def free_bytes(directory):
    if _os.name == "nt":
        import shutil
        return shutil.disk_usage(_directory_path(directory)).free
    info = _os.fstatvfs(directory)
    return info.f_bavail * info.f_frsize


@contextmanager
def pinned_source(path):
    """Keep FFmpeg's path stable on Windows; use an inherited fd on POSIX."""
    if _os.name == "nt":
        directories = []
        descriptor = None
        try:
            path = Path(_os.path.abspath(path))
            parent = Directory(_nt_open(_nt_root(path.anchor), None, 0xa0, directory=True))
            directories.append(parent)
            for part in path.parts[1:-1]:
                parent = Directory(_nt_open(part, parent, 0xa0, directory=True))
                directories.append(parent)
            # Disallow writes, renames and replacement until FFmpeg finishes.
            handle = _nt_open(path.name, parent, 0x81, share=1)
            try:
                descriptor = msvcrt.open_osfhandle(handle, _os.O_RDONLY | _os.O_BINARY)
            except BaseException:
                _kernel.CloseHandle(handle)
                raise
            yield descriptor, str(path), {}
        finally:
            if descriptor is not None:
                close(descriptor)
            for parent in reversed(directories):
                close(parent)
    else:
        from file_operations import open_directory
        parent = open_directory(Path(path).absolute().parent)
        descriptor = None
        try:
            descriptor = _os.open(Path(path).name, _os.O_RDONLY | _os.O_NOFOLLOW, dir_fd=parent)
            yield descriptor, f"/dev/fd/{descriptor}", {"pass_fds": (descriptor,)}
        finally:
            if descriptor is not None:
                _os.close(descriptor)
            _os.close(parent)


if sys.platform == "win32":
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    from pathlib import Path
    from types import SimpleNamespace

    O_NOFOLLOW, O_DIRECTORY, O_NONBLOCK = 1 << 27, 1 << 28, 1 << 26
    _kernel = c.WinDLL("kernel32", use_last_error=True)
    _nt = c.WinDLL("ntdll")

    class _Unicode(c.Structure):
        _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]

    class _Attributes(c.Structure):
        _fields_ = [("Length", w.ULONG), ("RootDirectory", w.HANDLE),
                    ("ObjectName", c.POINTER(_Unicode)), ("Attributes", w.ULONG),
                    ("SecurityDescriptor", w.LPVOID), ("SecurityQualityOfService", w.LPVOID)]

    class _Status(c.Structure):
        _fields_ = [("Status", c.c_void_p), ("Information", c.c_size_t)]

    class _Info(c.Structure):
        _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME),
                    ("accessed", w.FILETIME), ("modified", w.FILETIME),
                    ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                    ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]

    _kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, c.POINTER(_Info)]
    _kernel.GetFileInformationByHandle.restype = w.BOOL
    _kernel.CloseHandle.argtypes = [w.HANDLE]
    _kernel.CloseHandle.restype = w.BOOL
    _kernel.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    _kernel.GetFinalPathNameByHandleW.restype = w.DWORD
    _nt.NtCreateFile.argtypes = [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(_Attributes),
                               c.POINTER(_Status), c.c_void_p, w.ULONG, w.ULONG,
                               w.ULONG, w.ULONG, c.c_void_p, w.ULONG]
    _nt.NtCreateFile.restype = c.c_long
    _nt.NtSetInformationFile.argtypes = [w.HANDLE, c.POINTER(_Status), w.LPVOID, w.ULONG, w.ULONG]
    _nt.NtSetInformationFile.restype = c.c_long
    _nt.RtlNtStatusToDosError.argtypes = [c.c_long]
    _nt.RtlNtStatusToDosError.restype = w.ULONG

    class Directory:
        def __init__(self, handle):
            self.handle = handle

    def _check(status):
        if status < 0:
            raise c.WinError(_nt.RtlNtStatusToDosError(status))

    def _info(handle):
        info = _Info()
        if not _kernel.GetFileInformationByHandle(handle, c.byref(info)):
            raise c.WinError(c.get_last_error())
        return info

    def _component(name):
        name = _os.fspath(name)
        if not name or name in {".", ".."} or any(ch in name for ch in '\\/:\x00'):
            raise ValueError("无效的相对文件名")
        return name

    def _nt_open(name, parent, access, disposition=1, directory=False, share=None):
        if parent is not None:
            name = _component(name)
        buffer = c.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        string = _Unicode(length, length + 2, c.cast(buffer, w.LPWSTR))
        attributes = _Attributes(c.sizeof(_Attributes), parent.handle if parent else None,
                                 c.pointer(string), 0x40, None, None)
        handle, status = w.HANDLE(), _Status()
        # Directories deny delete sharing, so paths cannot be redirected while held.
        options = 0x200000 | 0x20 | (1 if directory else 0x40)
        _check(_nt.NtCreateFile(c.byref(handle), access | 0x100000, c.byref(attributes),
                               c.byref(status), None, 0x80, share if share is not None else 3 if directory else 7,
                               disposition, options, None, 0))
        try:
            if _info(handle).attributes & 0x400:
                raise OSError("拒绝符号链接、目录联接或云端重解析点")
        except BaseException:
            _kernel.CloseHandle(handle)
            raise
        return handle.value

    def _nt_root(anchor):
        if anchor.startswith("\\\\?\\"):
            anchor = anchor[4:]
            return "\\??\\" + anchor
        return "\\??\\UNC\\" + anchor[2:] if anchor.startswith("\\\\") else "\\??\\" + anchor

    def _absolute_parent(path):
        path = Path(_os.path.abspath(path))
        directory = Directory(_nt_open(_nt_root(path.anchor), None, 0xa0, directory=True))
        try:
            for part in path.parts[1:-1]:
                child = Directory(_nt_open(part, directory, 0xa0, directory=True))
                close(directory)
                directory = child
            return directory, path.name
        except BaseException:
            close(directory)
            raise

    def open(path, flags, mode=0o777, *, dir_fd=None):
        directory = bool(flags & O_DIRECTORY)
        if dir_fd is None:
            absolute = Path(_os.path.abspath(path))
            if directory and absolute == Path(absolute.anchor):
                return Directory(_nt_open(_nt_root(absolute.anchor), None, 0xa0, directory=True))
            parent, name = _absolute_parent(path)
        else:
            parent, name = dir_fd, _component(path)
        try:
            access = 0xa0 if directory else 0x81
            if flags & (_os.O_WRONLY | _os.O_RDWR):
                access |= 0x116  # Write data/attributes and append.
            disposition = 2 if flags & _os.O_EXCL else 3 if flags & _os.O_CREAT else 1
            if flags & _os.O_TRUNC:
                disposition = 5 if flags & _os.O_CREAT else 4
            handle = _nt_open(name, parent, access, disposition, directory)
            if directory:
                return Directory(handle)
            crt_flags = (flags & (_os.O_WRONLY | _os.O_RDWR | _os.O_APPEND)) | _os.O_BINARY
            try:
                return msvcrt.open_osfhandle(handle, crt_flags)
            except BaseException:
                _kernel.CloseHandle(handle)
                raise
        finally:
            if dir_fd is None:
                close(parent)

    def close(fd):
        if isinstance(fd, Directory):
            if fd.handle is not None:
                _kernel.CloseHandle(fd.handle)
                fd.handle = None
        else:
            _os.close(fd)

    def fstat(fd):
        if not isinstance(fd, Directory):
            return _os.fstat(fd)
        info = _info(fd.handle)
        return SimpleNamespace(st_dev=info.volume, st_ino=(info.index_high << 32) | info.index_low,
                               st_mode=0o40700, st_size=0)

    def stat(path, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is None:
            return _os.stat(path, follow_symlinks=follow_symlinks)
        fd = open(path, O_NOFOLLOW, dir_fd=dir_fd)
        try:
            return fstat(fd)
        finally:
            close(fd)

    def _directory_path(directory):
        buffer = c.create_unicode_buffer(32768)
        count = _kernel.GetFinalPathNameByHandleW(directory.handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise c.WinError(c.get_last_error())
        return buffer.value

    def listdir(path="."):
        if isinstance(path, Directory):
            # Enumeration is read-only; mutations use handle-relative native calls.
            return _os.listdir(_directory_path(path))
        return _os.listdir(path)

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            return _os.mkdir(path, mode)
        handle = _nt_open(path, dir_fd, 0x81, 2, True)
        _kernel.CloseHandle(handle)

    def fchmod(fd, mode):
        # Windows access control is inherited from the user's chosen directory.
        # POSIX permission bits do not represent NTFS ACLs.
        pass

    def utime(path, *, ns=None, dir_fd=None, follow_symlinks=True):
        if isinstance(path, int):
            _kernel.SetFileTime.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(w.FILETIME), c.POINTER(w.FILETIME)]
            _kernel.SetFileTime.restype = w.BOOL
            times = [value // 100 + 116444736000000000 for value in ns]
            access, modified = [w.FILETIME(value & 0xffffffff, value >> 32) for value in times]
            if not _kernel.SetFileTime(msvcrt.get_osfhandle(path), None, c.byref(access), c.byref(modified)):
                raise c.WinError(c.get_last_error())
        else:
            _os.utime(path, ns=ns, follow_symlinks=follow_symlinks)

    def pread(fd, amount, offset):
        position = _os.lseek(fd, 0, _os.SEEK_CUR)
        try:
            _os.lseek(fd, offset, _os.SEEK_SET)
            return _os.read(fd, amount)
        finally:
            _os.lseek(fd, position, _os.SEEK_SET)

    def _rename(source, target, source_directory, target_directory, replace):
        class Rename(c.Structure):
            _fields_ = [("replace", w.BOOLEAN), ("root", w.HANDLE),
                        ("length", w.ULONG), ("name", w.WCHAR * 1)]
        encoded = _component(target).encode("utf-16-le")
        buffer = c.create_string_buffer(max(c.sizeof(Rename), Rename.name.offset + len(encoded)))
        info = Rename.from_buffer(buffer)
        info.replace, info.root, info.length = replace, target_directory.handle, len(encoded)
        c.memmove(c.addressof(buffer) + Rename.name.offset, encoded, len(encoded))
        handle = _nt_open(source, source_directory, 0x10080)
        try:
            status = _Status()
            _check(_nt.NtSetInformationFile(handle, c.byref(status), buffer, len(buffer), 10))
        finally:
            _kernel.CloseHandle(handle)

    def replace(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        parents = []
        try:
            if src_dir_fd is None:
                src_dir_fd, source = _absolute_parent(source)
                parents.append(src_dir_fd)
            if dst_dir_fd is None:
                dst_dir_fd, target = _absolute_parent(target)
                parents.append(dst_dir_fd)
            # Refuse reparse points at the destination instead of replacing them.
            try:
                fd = open(target, O_NOFOLLOW, dir_fd=dst_dir_fd)
            except FileNotFoundError:
                pass
            else:
                close(fd)
            _rename(source, target, src_dir_fd, dst_dir_fd, True)
        finally:
            for parent in parents:
                close(parent)

    def unlink(path, *, dir_fd=None):
        if dir_fd is None:
            parent, path = _absolute_parent(path)
        else:
            parent = dir_fd
        try:
            handle = _nt_open(path, parent, 0x10080)
            try:
                status, delete = _Status(), w.BOOLEAN(True)
                _check(_nt.NtSetInformationFile(handle, c.byref(status), c.byref(delete), 1, 13))
            finally:
                _kernel.CloseHandle(handle)
        finally:
            if dir_fd is None:
                close(parent)
