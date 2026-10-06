"""Portable fixtures. These helpers never access personal media."""
import os
from pathlib import Path
import unittest


def make_symlink(link, target, **options):
    try:
        link.symlink_to(target, **options)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            raise unittest.SkipTest("Windows 未启用符号链接权限；目录联接另有专门测试") from error
        raise


def sample_path(value):
    return str(Path("C:" + value)) if os.name == "nt" else value
