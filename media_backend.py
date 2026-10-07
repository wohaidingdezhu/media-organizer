"""Shared media workers on both desktops; native Mac fallback without packages."""
import importlib.util
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent


def helper(name):
    if name not in {"image_probe", "video_cover"}:
        raise ValueError("未知媒体组件")
    portable = BASE / ("portable_image_probe.py" if name == "image_probe" else "portable_video_cover.py")
    if portable_available(portable):
        return portable
    native = BASE / "native" / name
    if sys.platform == "darwin" and native.is_file() and not native.is_symlink():
        return native
    return portable


def portable_available(path):
    if path.name == "portable_image_probe.py":
        return importlib.util.find_spec("PIL") is not None
    if path.name == "portable_video_cover.py":
        return importlib.util.find_spec("imageio_ffmpeg") is not None
    return True


def command(path):
    if path.suffix == ".py" and getattr(sys, "frozen", False):
        return [sys.executable, "--worker", path.stem]
    return [sys.executable, "-X", "utf8", str(path)] if path.suffix == ".py" else [str(path)]
