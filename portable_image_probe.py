"""Pillow backend speaking the native image probe's JSON-lines protocol."""
import json
from pathlib import Path
import sys
import portable_fs as os


def probe(path, thumbnail=None):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    result = {"path": path}
    from file_operations import open_directory
    parent = open_directory(Path(path).absolute().parent)
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream, Image.open(stream) as original:
        original.seek(0)
        exif = original.getexif()
        try:
            nested = exif.get_ifd(0x8769)
        except (AttributeError, ValueError, KeyError):
            nested = {}
        date = nested.get(36867) or exif.get(36867)
        if date:
            result.update(date_original=str(date), date_source="exif_original")
        elif exif.get(306):
            result.update(date_original=str(exif[306]), date_source="tiff_datetime")
        raw_size = original.size
        if exif.get(274) in {5, 6, 7, 8}:
            raw_size = raw_size[::-1]
        original.thumbnail((2048, 2048))
        image = ImageOps.exif_transpose(original)
        result.update(width=raw_size[0], height=raw_size[1])
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, "white")
        canvas.alpha_composite(rgba)
        sample = canvas.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata())
        value = 0
        for row in range(8):
            for column in range(8):
                value = (value << 1) | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
        mean = sum(pixels) / len(pixels)
        variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
        result.update(dhash=f"{value:016x}", low_detail=variance < 64 or max(pixels) - min(pixels) < 20
                      or value in {0, (1 << 64) - 1})
        if thumbnail:
            image.thumbnail((512, 512))
            destination = Path(thumbnail)
            if destination.absolute() == Path(path).absolute():
                raise ValueError("预览路径不能与原图片相同")
            output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(output, "wb") as target:
                image.convert("RGB").save(target, format="PNG")
            result["thumbnail"] = str(destination)
    return result


def batch(function):
    for line in sys.stdin.buffer:
        path = ""
        try:
            if len(line) > 1024 * 1024:
                raise ValueError("请求过大")
            request = json.loads(line)
            path = request["path"]
            result = function(path, request.get("thumbnail"))
        except Exception as error:
            result = {"path": path, "error": str(error)}
        sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    batch(probe)
