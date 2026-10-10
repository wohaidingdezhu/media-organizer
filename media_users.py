"""Shared username categories. Metadata only; never open original media."""
import collections
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import unicodedata

from media_actions import media_id
from movie_grouping import source_path
from organization_plan import _target_error
from workspace_data import data_lock, read_json, write_json

NAME = 'media-users.json'
MAX_BYTES = 32 * 1024 * 1024


def clean_name(value):
    if not isinstance(value, str):
        raise ValueError('用户名须为文字')
    name = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(name) <= 60 or '/' in name or _target_error(name):
        raise ValueError('用户名须为 1–60 字，不能含路径分隔符、控制字符或 Windows 保留名称')
    return name


def validate_users(value):
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or not isinstance(value.get('users'), dict) or len(value['users']) > 1000
            or not isinstance(value.get('assignments'), dict) or len(value['assignments']) > 200000):
        raise ValueError('用户分类资料损坏或超过上限，请保留原文件后检查')
    names = set()
    for key, name in value['users'].items():
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key):
            raise ValueError('用户标识无效')
        if clean_name(name) != name or name.casefold() in names:
            raise ValueError('用户名未规范化或有重复名称')
        names.add(name.casefold())
    for key, owner in value['assignments'].items():
        if (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key)
                or not isinstance(owner, str) or owner not in value['users']):
            raise ValueError('媒体用户归属资料无效')
    return value


def units_from(document):
    records = {}
    for item in document.get('files', []):
        if not isinstance(item, dict) or item.get('kind') not in {'照片', '视频'}:
            continue
        path = item.get('path')
        if not isinstance(path, str) or not path or '\x00' in path or media_id(path) in records:
            raise ValueError('报告含无效或重复媒体路径，请重新扫描')
        records[media_id(path)] = item
    units, grouped = {}, set()
    library = document.get('video_library')
    for group in library.get('groups', []) if isinstance(library, dict) else []:
        if not isinstance(group, dict):
            raise ValueError('影片分组记录无效')
        key = group.get('work_key', group.get('tag_key', ''))
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            continue
        files = [records[media_id(file['path'])] for file in group.get('files', [])
                 if media_id(file['path']) in records and records[media_id(file['path'])]['kind'] == '视频']
        if not files:
            continue
        unit = units.setdefault('movie:' + key, {'id': 'movie:' + key, 'kind': '电影',
            'title': str(group.get('title', '未命名影片')), 'files': [], 'preview': group.get('poster', '')})
        edition = str(group.get('edition', ''))
        edition_key = hashlib.sha256(edition.encode('utf-8')).hexdigest()[:8]
        for file in files:
            identifier = media_id(file['path'])
            if identifier in grouped:
                raise ValueError('影片分组含重复文件，请重新扫描或核对分组')
            grouped.add(identifier)
            unit['files'].append({'id': identifier, 'path': file['path'], 'edition': edition,
                                  'edition_key': edition_key})
    previews = document.get('previews', {})
    if not isinstance(previews, dict):
        previews = {}
    for identifier, file in records.items():
        if identifier in grouped:
            continue
        photo = file['kind'] == '照片'
        key = ('photo:' if photo else 'video:') + identifier
        units[key] = {'id': key, 'kind': '照片' if photo else '电影', 'title': source_path(file['path']).name,
                      'files': [{'id': identifier, 'path': file['path'], 'edition': '', 'edition_key': ''}],
                      'preview': previews.get(file['path'], '') if photo else ''}
    return list(units.values())


class UserCatalog:
    def __init__(self, root, document):
        self.root, self.document = root, document

    def _read(self):
        return validate_users(read_json(self.root, NAME, {'version': 1, 'users': {}, 'assignments': {}}, MAX_BYTES))

    def _snapshot(self, values):
        units = units_from(self.document())
        context = [(unit['id'], unit['title'], unit['files']) for unit in units]
        revision = hashlib.sha256(json.dumps([values, context], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        counts = collections.Counter(values['assignments'].values())
        current = collections.Counter()
        for unit in units:
            owners = {values['assignments'].get(file['id'], '') for file in unit['files']}
            unit['user_id'] = next(iter(owners)) if len(owners) == 1 else 'mixed'
            unit['username'] = values['users'].get(unit['user_id'], '归属不一致' if len(owners) > 1 else '未分配')
            current[unit['user_id']] += 1
        users = [{'id': key, 'name': name, 'media_count': current[key], 'file_count': counts[key]}
                 for key, name in values['users'].items()]
        return {'revision': revision, 'users': sorted(users, key=lambda item: item['name'].casefold()),
                'items': units, 'unassigned': current[''], 'mixed': current['mixed']}

    def snapshot(self):
        with data_lock(self.root):
            return self._snapshot(self._read())

    @staticmethod
    def _check_revision(snapshot, expected):
        if not isinstance(expected, str) or expected != snapshot['revision']:
            raise ValueError('用户名、归属或影片分组已变化，请刷新后重试；本次未保存')

    @staticmethod
    def _selected(snapshot, ids):
        available = {item['id']: item for item in snapshot['items']}
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 200
                or any(not isinstance(key, str) or key not in available for key in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('请选择 1–200 项当前报告的照片或电影')
        return [available[key] for key in ids]

    def update(self, payload):
        with data_lock(self.root):
            values = self._read()
            snapshot = self._snapshot(values)
            self._check_revision(snapshot, payload.get('revision'))
            action, owner = payload.get('action'), payload.get('user_id')
            if action in {'create', 'rename'}:
                name = clean_name(payload.get('name'))
                if action == 'rename' and (not isinstance(owner, str) or owner not in values['users']):
                    raise ValueError('用户名不存在，请刷新')
                if any(key != owner and old.casefold() == name.casefold() for key, old in values['users'].items()):
                    raise ValueError('用户名已存在（大小写或 Unicode 等价也视为重名）')
                if action == 'create':
                    owner = secrets.token_hex(16)
                values['users'][owner] = name
            elif action == 'delete':
                if not isinstance(owner, str) or owner not in values['users']:
                    raise ValueError('用户名不存在，请刷新')
                del values['users'][owner]
                values['assignments'] = {key: user for key, user in values['assignments'].items() if user != owner}
            elif action == 'assign':
                if not isinstance(owner, str) or owner and owner not in values['users']:
                    raise ValueError('请选择已有用户名或未分配')
                for unit in self._selected(snapshot, payload.get('ids')):
                    for file in unit['files']:
                        if owner:
                            values['assignments'][file['id']] = owner
                        else:
                            values['assignments'].pop(file['id'], None)
            else:
                raise ValueError('用户管理操作无效')
            validate_users(values)
            if len(json.dumps(values, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
                raise ValueError('用户分类资料超过大小上限')
            write_json(self.root, NAME, values)
            return self._snapshot(values)

    def plan(self, organization, payload, *, apply=False):
        from media_scan import safe_segment
        with data_lock(self.root):
            snapshot = self._snapshot(self._read())
            self._check_revision(snapshot, payload.get('revision'))
            items = self._selected(snapshot, payload.get('ids'))
            current = {item['id']: item for item in organization.snapshot()['items']}
            targets = {}
            for item in items:
                if not item['user_id'] or item['user_id'] == 'mixed':
                    raise ValueError('请先为所选照片和整部电影分配一个用户名')
                for file in item['files']:
                    record = current[file['id']]
                    if item['kind'] == '照片':
                        old = record['suggested_path']
                        if _target_error(old):
                            raise ValueError('照片原建议路径无效，请先修正')
                        target = PurePosixPath('用户', item['username'], '照片', PurePosixPath(old).name)
                    else:
                        work = safe_segment(item['title']) + '-' + item['id'].split(':')[1][:8]
                        folder = PurePosixPath('用户', item['username'], '电影', work)
                        if file['edition']:
                            folder /= safe_segment(file['edition']) + '-' + file['edition_key']
                        target = folder / source_path(file['path']).name
                    targets[file['id']] = str(target)
            return organization.update_targets(targets, payload.get('plan_revision'), apply=apply)
