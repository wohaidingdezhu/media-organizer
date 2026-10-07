"""Private application data and one interprocess lock for metadata maintenance."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading

import portable_fs as fs
import portable_lock as locking
from file_operations import open_directory

_locks = {}
_guard = threading.Lock()
_local = threading.local()


@contextmanager
def data_lock(root):
    root = fs.ensure_private_directory(root)
    key = str(root)
    with _guard:
        mutex = _locks.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_local, 'held', {})
        if key in held:
            yield
            return
        parent = open_directory(root)
        descriptor = None
        try:
            descriptor = fs.open_lock('.workspace-lock', parent)
            locking.flock(descriptor, locking.LOCK_EX)
            held[key] = descriptor
            _local.held = held
            yield
        finally:
            held.pop(key, None)
            if descriptor is not None:
                fs.close(descriptor)
            fs.close(parent)


def read_json(root, name, default=None, limit=128 * 1024 * 1024):
    try:
        return json.loads(fs.read_private_file(root, (name,), limit))
    except FileNotFoundError:
        return default


def write_json(root, name, document):
    body = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    fs.write_private_file(root, name, body)


def active_workspace(root):
    root = fs.private_data_path(root)
    if not root.exists():
        return root
    choice = read_json(root, 'active-workspace.json', {'workspace': ''}, 4096)
    if not isinstance(choice, dict) or not isinstance(choice.get('workspace'), str):
        raise ValueError('当前资料库配置损坏，请核对 active-workspace.json')
    name = choice['workspace']
    if not name:
        return root
    import re
    if not re.fullmatch(r'restored-[0-9]{8}-[0-9]{6}-[0-9a-f]{12}', name):
        raise ValueError('恢复资料库标识无效')
    descriptor = open_directory(root / name)
    fs.close(descriptor)
    return root / name
