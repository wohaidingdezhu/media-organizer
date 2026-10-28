"""Reuse decoding only. Discovery and full duplicate SHA-256 remain fresh."""
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import sys
import struct
import zlib

import portable_fs as fs
from media_actions import checked_stat, file_signature, media_id


def valid_thumbnail(body):
    """Bounded PNG structure/CRC checks; invalid cache falls back to decoding."""
    if not body.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    offset, header, data = 8, False, False
    while offset + 12 <= len(body):
        length = struct.unpack_from('>I', body, offset)[0]
        end = offset + 12 + length
        if end > len(body):
            return False
        kind = body[offset + 4:offset + 8]
        payload = body[offset + 8:end - 4]
        crc = struct.unpack_from('>I', body, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return False
        if not header:
            if kind != b'IHDR' or length != 13:
                return False
            width, height = struct.unpack_from('>II', payload)
            # Shared thumbnails fit 512; native macOS video fallback allows 600.
            if not 0 < width <= 600 or not 0 < height <= 600:
                return False
            header = True
        elif kind == b'IHDR':
            return False
        elif kind == b'IDAT':
            data = data or bool(length)
        elif kind == b'IEND':
            return length == 0 and data and end == len(body)
        offset = end
    return False


def analysis_fingerprint(image_helper, video_helper):
    values = ['decode-cache-v1', sys.platform, list(sys.version_info[:2])]
    for helper in (image_helper, video_helper):
        path = Path(helper)
        values.append(path.name)
        values.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing')
    for name in ('Pillow', 'pillow-heif', 'imageio-ffmpeg'):
        try:
            values.append(name + ':' + importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            values.append(name + ':missing')
    return hashlib.sha256(json.dumps(values).encode('utf-8')).hexdigest()


class ScanCache:
    def __init__(self, output, roots, fingerprint, enabled=True):
        self.output = Path(output)
        self.report = None
        self.files, self.pictures, self.source_reports = {}, {}, {}
        self.reports = []
        self.reused_metadata = self.reused_previews = self.reused_covers = self.reused_video_metadata = 0
        self.enabled = enabled
        if not enabled or not self.output.exists():
            return
        selected_roots = [Path(root) for root in roots]
        covered_roots = set()
        names = (name for name in fs.listdir(self.output) if re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', name))
        for name in sorted(names, reverse=True)[:100]:
            try:
                document = json.loads(fs.read_private_file(self.output, (name, 'report.json'), 128 * 1024 * 1024))
                # Only fully exported, local snapshots using the same decoders.
                checked_stat(self.output / name / 'report.html')
                prior_roots = document['roots']
                records = document['files']
                if (document.get('restored_snapshot') or not isinstance(prior_roots, list)
                        or not all(isinstance(path, str) and Path(path).is_absolute() for path in prior_roots)
                        or not isinstance(records, list)
                        or document['options'].get('analysis_fingerprint') != fingerprint):
                    continue
                previous = [Path(path) for path in prior_roots]
                if not any(root == old or root in old.parents or old in root.parents for root in selected_roots for old in previous):
                    continue
                prepared = {}
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get('path'), str):
                        raise ValueError('缓存报告文件记录无效')
                    path = Path(record['path'])
                    if not path.is_absolute() or '..' in path.parts:
                        raise ValueError('缓存报告文件路径无效')
                    if any(root in path.parents for root in selected_roots):
                        prepared[str(path)] = record
                previews = document.get('previews', {})
                groups = document.get('video_library', {}).get('groups', [])
                if not isinstance(previews, dict) or not isinstance(groups, list):
                    raise ValueError('缓存报告预览记录无效')
                pictures = {}
                for path, relative in previews.items():
                    if path in prepared:
                        pictures[('image', path)] = relative
                for group in groups:
                    if not isinstance(group, dict):
                        raise ValueError('缓存报告影片记录无效')
                    source = group.get('poster_source', '')
                    if not isinstance(source, str):
                        continue
                    role = 'video' if source.startswith('视频截帧：') else 'image'
                    source = source.removeprefix('视频截帧：')
                    if source in prepared and group.get('poster'):
                        pictures.setdefault((role, source), group['poster'])
                if self.report is None:
                    self.report = name
                self.reports.append(name)
                for path, record in prepared.items():
                    if path in self.files:
                        continue
                    self.files[path] = record
                    self.source_reports[path] = name
                    for role in ('image', 'video'):
                        if (role, path) in pictures:
                            self.pictures[(role, path)] = pictures[(role, path)]
                covered_roots.update(root for root in selected_roots if any(old == root or old in root.parents for old in previous))
                if len(covered_roots) == len(selected_roots):
                    break
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue

    def unchanged(self, record):
        old = self.files.get(record['path'])
        return bool(old and old.get('source_signature') == list(record['_signature'])
                    and file_signature(checked_stat(record['path'])) == list(record['_signature']))

    def metadata(self, record):
        try:
            if not self.unchanged(record):
                return None
            image = self.files[record['path']].get('image')
            if (not isinstance(image, dict) or not re.fullmatch(r'[0-9a-f]{16}', image.get('dhash', ''))
                    or type(image.get('width')) is not int or type(image.get('height')) is not int
                    or not 0 < image['width'] <= 1000000 or not 0 < image['height'] <= 1000000
                    or type(image.get('low_detail')) is not bool):
                return None
            result = {key: image[key] for key in ('width', 'height', 'dhash', 'low_detail')}
            for key in ('date_original', 'date_source'):
                if key in image:
                    if not isinstance(image[key], str) or len(image[key]) > 200:
                        return None
                    result[key] = image[key]
            result['path'] = record['path']
            self.reused_metadata += 1
            return result
        except (OSError, ValueError, TypeError):
            return None

    def video_metadata(self, record):
        from video_metadata import clean_video_metadata
        try:
            if self.unchanged(record):
                value = clean_video_metadata(self.files[record['path']].get('video'))
                if value:
                    self.reused_video_metadata += 1
                    return value
        except (OSError, ValueError, TypeError):
            pass
        return None

    def thumbnail(self, record, destination, role='image'):
        relative = self.pictures.get((role, record['path']))
        candidates = [relative]
        kind = self.files.get(record['path'], {}).get('kind')
        if (role, kind) in {('image', '照片'), ('video', '视频')}:
            candidates.append('covers/repair-' + media_id(record['path']) + '.png')
        report = self.source_reports.get(record['path'])
        if not report:
            return False
        created = False
        try:
            if not self.unchanged(record):
                return False
            body = None
            for candidate in dict.fromkeys(value for value in candidates if isinstance(value, str)):
                if not re.fullmatch(r'(previews|covers)/[A-Za-z0-9_-]+\.png', candidate):
                    continue
                try:
                    content = fs.read_private_file(self.output, (report, *PurePosixPath(candidate).parts), 2 * 1024 * 1024)
                except (OSError, ValueError):
                    continue
                if valid_thumbnail(content):
                    body = content
                    break
            if body is None:
                return False
            descriptor = fs.open(destination, fs.O_WRONLY | fs.O_CREAT | fs.O_EXCL | fs.O_NOFOLLOW, 0o600)
            created = True
            with fs.fdopen(descriptor, 'wb') as stream:
                stream.write(body)
            if not self.unchanged(record):
                raise ValueError('缓存预览期间来源变化')
            if role == 'video':
                self.reused_covers += 1
            else:
                self.reused_previews += 1
            return True
        except (OSError, ValueError, TypeError):
            if created:
                Path(destination).unlink(missing_ok=True)
            return False

    def summary(self):
        return {'enabled': self.enabled, 'previous_report': self.report or '', 'previous_reports': list(self.reports),
                'image_metadata': self.reused_metadata, 'image_previews': self.reused_previews,
                'video_covers': self.reused_covers, 'video_metadata': self.reused_video_metadata,
                'note': '已重新遍历来源并核对文件身份；仅复用未变化文件的解析和小图，可能重复的候选仍重新完整读取 SHA-256。'}
