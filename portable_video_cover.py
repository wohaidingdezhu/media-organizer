"""Bounded FFmpeg still-frame worker. Writes only report-local thumbnails."""
import re
import io
import time
import subprocess
from pathlib import Path
from portable_image_probe import batch
import portable_fs as os
from video_metadata import parse_video_metadata
from system_integration import hidden_process_options


def dark_frame(frame):
    """Try another timestamp for near-black frames, retaining a valid fallback."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return False
    try:
        with Image.open(io.BytesIO(frame)) as image:
            sample = image.convert("L")
            sample.thumbnail((64, 64))
            return ImageStat.Stat(sample).mean[0] < 8 and sample.getextrema()[1] < 24
    except (OSError, ValueError):
        return False


def probe(path, thumbnail=None):
    import imageio_ffmpeg
    if thumbnail and Path(path).absolute() == Path(thumbnail).absolute():
        raise ValueError("视频截帧需要独立的预览路径")
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    # Keep a verified source open throughout probing and frame extraction.
    with os.pinned_source(path) as (source, input_path, options):
        before = os.fstat(source)
        deadline = time.monotonic() + 23
        metadata = subprocess.run([executable, "-nostdin", "-hide_banner", "-i", input_path],
                                  capture_output=True, timeout=8, **options, **hidden_process_options())
        facts = parse_video_metadata(metadata.stderr)
        if not thumbnail:
            from media_actions import checked_stat, file_signature
            if file_signature(before) != file_signature(checked_stat(path)):
                raise ValueError("视频在读取信息时发生变化")
            if not facts:
                raise ValueError("无法读取视频时长或尺寸；仍可浏览和查重")
            return {"path": path, **facts}
        match = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", metadata.stderr)
        seek = sum(float(value) * scale for value, scale in zip(match.groups(), (3600, 60, 1))) * .1 if match else 0
        frame = None
        # Prefer an informative frame. Some streams have black introductions
        # or fail after seeking; try the middle and then the beginning.
        # Bound the combined metadata/extraction budget, while retaining the
        # existing per-frame timeout for slower codecs.
        for seconds in ((seek, seek * 5, 0) if seek > 0 else (0,)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            os.lseek(source, 0, os.SEEK_SET)
            try:
                result = subprocess.run([executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                                         *(["-ss", str(seconds)] if seconds > 0 else []),
                                         "-i", input_path, "-frames:v", "1", "-an",
                                         "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
                                         "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                                        capture_output=True, timeout=min(7, remaining), **options, **hidden_process_options())
            except subprocess.TimeoutExpired:
                continue
            if not result.returncode and result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                if frame is None:
                    frame = result.stdout
                if not dark_frame(result.stdout):
                    frame = result.stdout
                    break
        if frame is None:
            raise ValueError("视频无法解码或截帧")
        from media_actions import checked_stat, file_signature
        if file_signature(before) != file_signature(checked_stat(path)):
            raise ValueError("视频在截帧时发生变化")
        output = os.open(thumbnail, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(output, "wb") as stream:
            stream.write(frame)
        return {"path": path, "thumbnail": thumbnail}


if __name__ == "__main__":
    batch(probe)
