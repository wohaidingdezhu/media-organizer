"""Choose native macOS helpers or portable Python workers."""
import importlib.util
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent


def helper(name):
    native = BASE / "native" / name
    if sys.platform == "darwin" and native.is_file() and not native.is_symlink():
        return native
    return BASE / ("portable_image_probe.py" if name == "image_probe" else "portable_video_cover.py")


def portable_available(path):
    if path.name == "portable_image_probe.py":
        return importlib.util.find_spec("PIL") is not None
    if path.name == "portable_video_cover.py":
        return importlib.util.find_spec("imageio_ffmpeg") is not None
    return True


def command(path):
    return [sys.executable, "-X", "utf8", str(path)] if path.suffix == ".py" else [str(path)]
