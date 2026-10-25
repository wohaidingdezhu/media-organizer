"""Explicit, bounded cover regeneration; only report-local images are written."""
from pathlib import Path
import re
import secrets
import subprocess
import threading

import portable_fs as fs
from media_actions import file_signature, media_id
from scan_cache import valid_thumbnail

_DECODER_LOCK = threading.Lock()


class PreviewRepair:
    def __init__(self, report, media, document):
        self.report, self.media = Path(report), media
        self.previews = {}
        allowed = {media_id(file['path']) for file in document.get('files', [])
                   if isinstance(file, dict) and isinstance(file.get('path'), str) and file.get('kind') in {'照片', '视频'}}
        try:
            from file_operations import open_directory
            directory = open_directory(self.report / 'covers')
            try:
                names = fs.listdir(directory)
            finally:
                fs.close(directory)
            for name in names:
                match = re.fullmatch(r'repair-([0-9a-f]{64})\.png', name)
                if match and match[1] in allowed:
                    try:
                        if valid_thumbnail(fs.read_private_file(self.report, ('covers', name), 2*1024*1024)):
                            self.previews[match[1]] = 'covers/' + name
                    except (OSError, ValueError):
                        continue
        except (OSError, ValueError):
            pass

    def apply(self, catalog):
        for item in catalog['items']:
            # A repair may replace a nonempty but unreadable scan-time URL.
            preview = next((self.previews[file['id']] for file in item['files'] if file['id'] in self.previews), '')
            if preview:
                item['preview'] = preview
                item.pop('preview_status', None)
        for group in (group for key in ('exact', 'similar', 'dismissed_similar') for group in catalog.get('review', {}).get(key, [])):
            for file in group['files']:
                if file['id'] in self.previews:
                    file['preview'] = self.previews[file['id']]
        return catalog

    def generate(self, identifier):
        from media_scan import ImageProbeWorker, helper_available
        import media_backend
        if not _DECODER_LOCK.acquire(blocking=False):
            raise ValueError('正在补充另一张预览，请稍候再试')
        worker, temporary = None, None
        try:
            record, info = self.media.validate_for_view(identifier)
            if record['kind'] not in {'照片', '视频'}:
                raise ValueError('请选择本库的照片或视频')
            if identifier in self.previews:
                try:
                    body = fs.read_private_file(self.report, tuple(self.previews[identifier].split('/')), 2*1024*1024)
                    if valid_thumbnail(body):
                        return {'preview': self.previews[identifier]}
                except (OSError, ValueError):
                    pass
                self.previews.pop(identifier, None)
            helper = media_backend.helper('image_probe' if record['kind'] == '照片' else 'video_cover')
            if not helper_available(helper):
                raise ValueError('媒体解码组件未安装，请先安装项目声明的媒体依赖')
            covers = fs.ensure_private_directory(self.report / 'covers')
            temporary = covers / ('.repair-tmp-' + secrets.token_hex(12) + '.png')
            worker = ImageProbeWorker(helper)
            try:
                worker.request(record['path'], thumbnail=str(temporary), timeout=25)
            except subprocess.TimeoutExpired as error:
                raise ValueError('媒体预览生成超时，可稍后重试或打开原文件查看') from error
            _, after = self.media.validate_for_view(identifier)
            if file_signature(info) != file_signature(after):
                raise ValueError('媒体在生成预览时发生变化，请重新扫描')
            body = fs.read_private_file(covers, (temporary.name,), 2*1024*1024)
            if not valid_thumbnail(body):
                raise ValueError('未能生成有效预览，可打开原文件查看')
            name = 'repair-' + identifier + '.png'
            # Only this application-generated report image can be replaced.
            # The shared private-file writer rejects links and pins the parent.
            fs.write_private_file(covers, name, body)
            self.previews[identifier] = 'covers/' + name
            return {'preview': self.previews[identifier]}
        finally:
            if worker:
                worker.close()
            if temporary:
                temporary.unlink(missing_ok=True)
            _DECODER_LOCK.release()
