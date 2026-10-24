"""Validated, optional video facts; never infer quality from file names."""
import math
import re


def clean_video_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    duration = value.get('duration_seconds')
    if type(duration) in {int, float} and 0 < duration <= 31536000 and math.isfinite(duration):
        result['duration_seconds'] = duration
    width, height = value.get('width'), value.get('height')
    if all(type(n) is int and 0 < n <= 1000000 for n in (width, height)):
        result.update(width=width, height=height)
    return result


def parse_video_metadata(stderr):
    """Read FFmpeg's input description, excluding audio and cover-art streams."""
    text = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else stderr
    streams = [line for line in text.splitlines()
               if re.match(r'\s*Stream #0:\d+', line) and 'Video:' in line and '(attached pic)' not in line]
    if not streams:
        return {}
    result = {}
    duration = re.search(r'^\s*Duration: (\d+):([0-5]\d):([0-5]\d(?:\.\d+)?),', text, re.M)
    if duration:
        result['duration_seconds'] = sum(float(n) * scale for n, scale in zip(duration.groups(), (3600, 60, 1)))
    size = re.search(r',\s*(\d{1,7})x(\d{1,7})(?=[\s,\[])', streams[0])
    if size:
        result.update(width=int(size[1]), height=int(size[2]))
    return clean_video_metadata(result)
