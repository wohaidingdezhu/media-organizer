"""Bounded, checksummed application-data backups. Never include original media."""
import datetime as dt
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import zipfile

import portable_fs as fs
from workspace_data import data_lock, write_json
from file_operations import maintenance_when_idle, open_directory

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 10000
ROOT_FILES = {'library-tags.json', 'library-notes.json', 'library-index.json', 'workspace-settings.json', 'library-grouping.json', 'media-users.json'}
REPORT_FILES = {'report.json', 'report.html', 'library.html', 'inventory.csv', 'duplicates.csv', 'similar.csv',
                'video_groups.csv', 'sidecars.csv', 'folder_names.csv', 'classification.csv', 'issues.csv',
                'library_issues.csv', 'organization-plan.json', 'cleanup-basket.json'}


def allowed(name):
    parts = PurePosixPath(name).parts
    if str(PurePosixPath(name)) != name or '\\' in name or ':' in name or name.startswith('/') or any(part in {'.', '..'} for part in parts):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES
    if not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', parts[0]):
        return False
    if len(parts) == 2:
        return parts[1] in REPORT_FILES
    if len(parts) == 3:
        return bool((parts[1] in {'covers', 'previews', 'stills'} and re.fullmatch(r'[A-Za-z0-9_-]+\.(png|jpg|jpeg)', parts[2])) or
                    (parts[1] == 'operations' and re.fullmatch(r'[A-Za-z0-9_-]+\.json', parts[2])))
    return False


def export_backup(root):
    root = fs.private_data_path(root)
    with maintenance_when_idle(root), data_lock(root):
        names = [name for name in sorted(fs.listdir(root)) if name in ROOT_FILES]
        for report in sorted(fs.listdir(root)):
            if not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', report):
                continue
            descriptor = open_directory(root / report)
            try:
                members = fs.listdir(descriptor)
            finally:
                fs.close(descriptor)
            if not {'report.json', 'report.html'}.issubset(members):
                continue  # An interrupted export is not a restorable snapshot.
            for name in sorted(members):
                if name in REPORT_FILES:
                    names.append(report + '/' + name)
                if name in {'covers', 'previews', 'stills', 'operations'}:
                    child = open_directory(root / report / name)
                    try:
                        names.extend(report + '/' + name + '/' + member for member in sorted(fs.listdir(child))
                                     if allowed(report + '/' + name + '/' + member))
                    finally:
                        fs.close(child)
        if not any(name.endswith('/report.json') for name in names):
            raise ValueError('尚无完整扫描报告，请先完成一次扫描再备份')
        if len(names) > MAX_FILES:
            raise ValueError('备份文件数量超过 10000，请分开保存资料库')
        output, manifest, total = io.BytesIO(), {}, 0
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                body = fs.read_private_file(root, PurePosixPath(name).parts)
                total += len(body)
                if total > MAX_TOTAL:
                    raise ValueError('本次备份超过 512 MB，请分开保存资料库')
                archive.writestr(name, body)
                manifest[name] = {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            archive.writestr('manifest.json', json.dumps({'version': 1, 'created_at': dt.datetime.now().astimezone().isoformat(),
                                                         'files': manifest}, ensure_ascii=False))
        if output.tell() > MAX_ARCHIVE:
            raise ValueError('压缩备份超过 64 MB，请分开保存资料库')
        return output.getvalue()


def validate_backup(body):
    if not isinstance(body, bytes) or len(body) > MAX_ARCHIVE:
        raise ValueError('备份压缩文件最大 64 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1 or len({info.filename for info in infos}) != len(infos):
                raise ValueError('备份含重复文件或文件数量过多')
            if sum(info.file_size for info in infos) > MAX_TOTAL or any(info.flag_bits & 1 for info in infos):
                raise ValueError('备份解压过大或经过加密')
            manifest_info = archive.getinfo('manifest.json')
            if manifest_info.file_size > 4 * 1024 * 1024:
                raise ValueError('备份清单过大')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('备份清单无效')
            names = set(manifest['files'])
            if names != {info.filename for info in infos} - {'manifest.json'} or not all(allowed(name) for name in names):
                raise ValueError('备份含未允许的文件或路径')
            files = {}
            for name in sorted(names):
                info = archive.getinfo(name)
                if info.is_dir() or info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError('备份不能包含链接或目录项')
                payload = archive.read(name)
                expected = manifest['files'][name]
                if (not isinstance(expected, dict) or expected.get('bytes') != len(payload) or
                        expected.get('sha256') != hashlib.sha256(payload).hexdigest()):
                    raise ValueError('备份内容校验不一致')
                files[name] = payload
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
        raise ValueError('备份文件无效或不完整') from error
    # Do not trust HTML, source identities or an imported index. Regenerate the
    # pages and require a new local scan before any source file action.
    from media_scan import render_report, render_video_library
    reports = sorted({name.split('/')[0] for name in files if name.endswith('/report.json')})
    if not reports:
        raise ValueError('备份没有扫描报告')
    for report in reports:
        try:
            document = json.loads(files[report + '/report.json'])
            if not isinstance(document, dict) or not isinstance(document.get('files'), list):
                raise ValueError('扫描报告无效')
            document['restored_snapshot'] = True
            document.setdefault('video_library', {'groups': [], 'issues': [], 'video_files': 0, 'duplicate_files': 0, 'poster_count': 0})
            for group in document['video_library'].get('groups', []):
                poster = group.get('poster', '')
                group['poster'] = poster if isinstance(poster, str) and re.fullmatch(r'covers/[A-Za-z0-9_-]+\.png', poster) else ''
                group['stills'] = [name for name in group.get('stills', []) if isinstance(name, str) and re.fullmatch(r'(stills|previews)/[A-Za-z0-9_-]+\.png', name)]
            document['previews'] = {path: name for path, name in document.get('previews', {}).items() if isinstance(name, str) and re.fullmatch(r'previews/[A-Za-z0-9_-]+\.png', name)}
            files[report + '/report.json'] = json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf-8')
            files[report + '/report.html'] = render_report(document).encode('utf-8')
            files[report + '/library.html'] = render_video_library(document).encode('utf-8')
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f'{report} 无法安全恢复，请核对备份版本') from error
    files.pop('library-index.json', None)
    if 'media-users.json' in files:
        from media_users import MAX_BYTES, validate_users
        if len(files['media-users.json']) > MAX_BYTES:
            raise ValueError('用户分类资料超过大小上限')
        validate_users(json.loads(files['media-users.json']))
    for name, limit in [('library-tags.json', 1024 * 1024), ('library-notes.json', 4 * 1024 * 1024), ('workspace-settings.json', 65536), ('library-grouping.json', 16 * 1024 * 1024)]:
        if name in files and len(files[name]) > limit:
            raise ValueError(name + ' 超过应用资料大小上限')
    if 'library-grouping.json' in files:
        from movie_grouping import validate_assignments
        validate_assignments(json.loads(files['library-grouping.json']))
    if 'library-tags.json' in files:
        from library_server import clean_tags
        values = json.loads(files['library-tags.json'])
        if not isinstance(values, dict) or values.get('version') != 1 or not isinstance(values.get('groups'), dict):
            raise ValueError('标签备份无效')
        for key, tags in values['groups'].items():
            if not re.fullmatch('[0-9a-f]{64}', key):
                raise ValueError('标签标识无效')
            clean_tags(tags)
    if 'library-notes.json' in files:
        from media_catalog import clean_note
        values = json.loads(files['library-notes.json'])
        if not isinstance(values, dict) or values.get('version') != 1 or not isinstance(values.get('groups'), dict):
            raise ValueError('备注备份无效')
        for key, note in values['groups'].items():
            if not re.fullmatch('[0-9a-f]{64}', key):
                raise ValueError('备注标识无效')
            clean_note(note)
    if any('/' in name and name.split('/')[0] not in reports for name in files):
        raise ValueError('备份含没有报告的孤立资源')
    return files, {'reports': len(reports), 'files': len(files), 'bytes': sum(map(len, files.values())),
                   'message': '恢复为独立资料库副本，不覆盖当前资料，也不改动原媒体。恢复后须重新扫描才能操作原文件。'}


def restore_backup(base, files):
    base = fs.ensure_private_directory(base)
    name = dt.datetime.now().strftime('restored-%Y%m%d-%H%M%S-') + secrets.token_hex(6)
    target = base / name
    fs.ensure_private_directory(target)
    try:
        for relative, body in files.items():
            parts = PurePosixPath(relative).parts
            parent = fs.ensure_private_directory(target.joinpath(*parts[:-1]))
            fs.write_private_file(parent, parts[-1], body)
        # Automatic scans are always disabled after importing from another host.
        write_json(target, 'workspace-settings.json', {'version': 1, 'folders': [], 'monitor': {'enabled': False, 'minutes': 15}})
    except BaseException:
        shutil.rmtree(target)
        raise
    return target
