"""Bounded FFmpeg still-frame worker. Writes only report-local thumbnails."""
import re
import subprocess
from pathlib import Path
from portable_image_probe import batch
import portable_fs as os


def probe(path, thumbnail=None):
    import imageio_ffmpeg
    if not thumbnail or Path(path).absolute() == Path(thumbnail).absolute():
        raise ValueError("视频截帧需要独立的预览路径")
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    # Keep a verified source open throughout probing and frame extraction.
    with os.pinned_source(path) as (source, input_path, options):
        before = os.fstat(source)
        metadata = subprocess.run([executable, "-nostdin", "-hide_banner", "-i", input_path],
                                  capture_output=True, timeout=8, **options)
        match = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", metadata.stderr)
        seek = sum(float(value) * scale for value, scale in zip(match.groups(), (3600, 60, 1))) * .1 if match else 0
        os.lseek(source, 0, os.SEEK_SET)
        result = subprocess.run([executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                                 "-ss", str(seek), "-i", input_path, "-frames:v", "1", "-an",
                                 "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
                                 "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                                capture_output=True, timeout=15, **options)
        if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("视频无法解码或截帧")
        from media_actions import checked_stat, file_signature
        if file_signature(before) != file_signature(checked_stat(path)):
            raise ValueError("视频在截帧时发生变化")
        output = os.open(thumbnail, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(output, "wb") as stream:
            stream.write(result.stdout)
        return {"path": path, "thumbnail": thumbnail}


if __name__ == "__main__":
    batch(probe)
