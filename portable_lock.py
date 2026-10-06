"""fcntl.flock-compatible advisory locking on POSIX and Windows.

The Windows lock occupies a byte past the journal header, leaving its job ID
readable by other processes. LockFileEx releases ownership when a handle closes.
"""
import sys

if sys.platform != "win32":
    from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
else:
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt

    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    class Overlapped(c.Structure):
        _fields_ = [("internal", c.c_size_t), ("internal_high", c.c_size_t),
                    ("offset", w.DWORD), ("offset_high", w.DWORD), ("event", w.HANDLE)]

    _kernel = c.WinDLL("kernel32", use_last_error=True)
    _kernel.LockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)]
    _kernel.LockFileEx.restype = w.BOOL
    _kernel.UnlockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)]
    _kernel.UnlockFileEx.restype = w.BOOL

    def flock(fd, operation):
        handle = msvcrt.get_osfhandle(fd)
        overlapped = Overlapped(offset=0x7fffffff)
        if operation & LOCK_UN:
            ok = _kernel.UnlockFileEx(handle, 0, 1, 0, c.byref(overlapped))
        else:
            ok = _kernel.LockFileEx(handle, 2 | (1 if operation & LOCK_NB else 0), 0, 1, 0, c.byref(overlapped))
        if not ok:
            error = c.get_last_error()
            if error == 33:
                raise BlockingIOError(11, "文件锁正被另一服务持有")
            raise c.WinError(error)
