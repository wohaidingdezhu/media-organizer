"""Reuse decoding only. Discovery and full duplicate SHA-256 remain fresh."""
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import sys

import portable_fs as fs
from media_actions import checked_stat, file_signature


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
        self.files, self.pictures = {}, {}
        self.reused_metadata = self.reused_previews = self.reused_covers = 0
        self.enabled = enabled
        if not enabled or not self.output.exists():
            return
        for name in sorted(fs.listdir(self.output), reverse=True)[:100]:
            if not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', name):
                continue
            try:
                document = json.loads(fs.read_private_file(self.output, (name, 'report.json'), 128 * 1024 * 1024))
                # Only fully exported, local snapshots using the same decoders.
                checked_stat(self.output / name / 'report.html')
                if (document.get('restored_snapshot') or sorted(document['roots']) != sorted(map(str, roots))
                        or document['options'].get('analysis_fingerprint') != fingerprint):
                    continue
                self.files = {record['path']: record for record in document['files']}
                self.report = name
                for path, relative in document.get('previews', {}).items():
                    self.pictures[('image', path)] = relative
                for group in document.get('video_library', {}).get('groups', []):
                    source = group.get('poster_source', '')
                    role = 'video' if source.startswith('视频截帧：') else 'image'
                    source = source.removeprefix('视频截帧：')
                    if source and group.get('poster'):
                        self.pictures.setdefault((role, source), group['poster'])
                break
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.files, self.pictures, self.report = {}, {}, None

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

    def thumbnail(self, record, destination, role='image'):
        relative = self.pictures.get((role, record['path']))
        if not isinstance(relative, str) or not re.fullmatch(r'(previews|covers)/[A-Za-z0-9_-]+\.png', relative):
            return False
        created = False
        try:
            if not self.unchanged(record):
                return False
            body = fs.read_private_file(self.output, (self.report, *PurePosixPath(relative).parts), 2 * 1024 * 1024)
            if len(body) < 24 or not body.startswith(b'\x89PNG\r\n\x1a\n'):
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
        return {'enabled': self.enabled, 'previous_report': self.report or '',
                'image_metadata': self.reused_metadata, 'image_previews': self.reused_previews,
                'video_covers': self.reused_covers,
                'note': '已重新遍历来源并核对文件身份；仅复用未变化文件的解析和小图，同大小查重候选仍重新完整读取 SHA-256。'}
