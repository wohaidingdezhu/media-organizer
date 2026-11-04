"""Explicit, bounded cover regeneration; only report-local images are written."""
from pathlib import Path
import hashlib
import json
import re
import secrets
import subprocess
import threading

import portable_fs as fs
from media_actions import file_signature, media_id
from scan_cache import valid_thumbnail, analysis_fingerprint

_DECODER_LOCK = threading.Lock()


class PreviewRepair:
    def __init__(self, report, media, document):
        self.report, self.media = Path(report), media
        self.previews = {}
        self.cache_root = self.report.parent / 'preview-cache' if document.get('unified_library') else None
        self.cached_routes = {}
        self.fingerprint = None
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
        if self.cache_root:
            self._load_cache()

    def _cache_name(self, identifier, record, signature=None):
        signature = record.get('source_signature') if signature is None else signature
        if (not self.cache_root or not isinstance(signature, list) or len(signature) != 5
                or any(type(value) is not int for value in signature)):
            return None
        if self.fingerprint is None:
            import media_backend
            self.fingerprint = analysis_fingerprint(media_backend.helper('image_probe'), media_backend.helper('video_cover'))
        key = [record['path'], signature, record['kind'], record.get('sha256', ''), self.fingerprint]
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode('utf-8')).hexdigest()
        return 'repair-' + identifier + '-' + digest + '.png'

    def _load_cache(self):
        try:
            from file_operations import open_directory
            directory = open_directory(self.cache_root)
            try:
                names = set(fs.listdir(directory))
            finally:
                fs.close(directory)
        except (OSError, ValueError):
            return  # Optional cache cannot block browsing existing reports.
        ids = {match[1] for name in names if (match := re.fullmatch(r'repair-([0-9a-f]{64})-[0-9a-f]{64}\.png', name))}
        for identifier in ids & self.media.records.keys():
            if identifier not in self.previews:
                self._reuse_cached(identifier, self.media.records[identifier], names=names)

    def _reuse_cached(self, identifier, record, *, signature=None, names=None):
        try:
            name = self._cache_name(identifier, record, signature)
            if not name or (names is not None and name not in names):
                return False
            body = fs.read_private_file(self.cache_root, (name,), 2*1024*1024)
            if not valid_thumbnail(body):
                return False
        except (OSError, ValueError):
            return False
        relative = 'covers/cached-' + name[-68:]
        self.cached_routes[relative] = name
        self.previews[identifier] = relative
        return True

    def read_cached(self, relative):
        """Only identity-matched cache entries are exposed as report assets."""
        name = self.cached_routes.get(relative)
        if not name:
            raise FileNotFoundError('预览不在当前资料库')
        body = fs.read_private_file(self.cache_root, (name,), 2*1024*1024)
        if not valid_thumbnail(body):
            raise ValueError('预览缓存已损坏')
        return body

    def apply(self, catalog):
        for item in catalog['items']:
            # A repair may replace a nonempty but unreadable scan-time URL.
            preview = next((self.previews[file['id']] for file in item['files'] if not file.get('browse_only') and file['id'] in self.previews), '')
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
                    relative = self.previews[identifier]
                    body = (self.read_cached(relative) if relative in self.cached_routes else
                            fs.read_private_file(self.report, tuple(relative.split('/')), 2*1024*1024))
                    if valid_thumbnail(body):
                        return {'preview': self.previews[identifier]}
                except (OSError, ValueError):
                    pass
                self.previews.pop(identifier, None)
            if self.cache_root and self._reuse_cached(identifier, record, signature=file_signature(info)):
                return {'preview': self.previews[identifier]}
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
            try:
                fs.write_private_file(covers, name, body)
            except (OSError, ValueError) as error:
                raise ValueError('预览无法保存，请检查报告目录权限及同名文件是否为普通文件') from error
            self.previews[identifier] = 'covers/' + name
            result = {'preview': self.previews[identifier]}
            if self.cache_root:
                try:
                    cache_name = self._cache_name(identifier, record, file_signature(info))
                    if not cache_name:
                        return result
                    fs.ensure_private_directory(self.cache_root)
                    fs.write_private_file(self.cache_root, cache_name, body)
                except (OSError, ValueError) as error:
                    # The valid report-local preview remains usable even when
                    # the optional shared cache is unavailable.
                    result['warning'] = '预览已生成，但复用缓存未保存：' + str(error)
            return result
        finally:
            if worker:
                worker.close()
            if temporary:
                temporary.unlink(missing_ok=True)
            _DECODER_LOCK.release()
