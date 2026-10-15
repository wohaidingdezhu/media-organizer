# coding: utf-8
"""Source-scoped, offline organization preferences; never edit original media."""
import hashlib
import json
from pathlib import Path
import re

from media_actions import media_id
from workspace_data import data_lock, read_json, write_json

NAME = 'organize-settings.json'
REGIONS = {'国产', '日韩', '欧美', '其他', '待确认地区'}


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class OrganizeSettings:
    def __init__(self, root, document):
        self.root, self.document = Path(root), document

    def _context(self):
        document = self.document() if callable(self.document) else self.document
        roots = sorted(set(str(item['root']) for item in document.get('files', []) if item.get('root')))
        return document, revision(roots)

    def _load(self):
        value = read_json(self.root, NAME, {'version': 1, 'profiles': {}}, 16*1024*1024)
        validate_settings(value)
        return value

    def _profile(self, value, key):
        profile = value['profiles'].get(key, {'destination': '', 'all_batches': True, 'corrections': {}})
        return self.validate_profile(profile)

    @staticmethod
    def validate_profile(profile):
        if (not isinstance(profile, dict) or not isinstance(profile.get('destination'), str)
                or type(profile.get('all_batches')) is not bool or not isinstance(profile.get('corrections'), dict)):
            raise ValueError('整理设置无效')
        for path, correction in profile['corrections'].items():
            if (not isinstance(path, str) or not isinstance(correction, dict)
                    or set(correction)-{'month', 'region', 'category'}
                    or 'region' in correction and (not isinstance(correction['region'], str) or correction['region'] not in REGIONS)
                    or 'category' in correction and (not isinstance(correction['category'], str) or correction['category'] not in {'movie', 'home_video'})
                    or 'month' in correction and (not isinstance(correction['month'], str) or not re.fullmatch(r'[1-9][0-9]{3}/(?:0[1-9]|1[0-2])', correction['month']))):
                raise ValueError('分类修正记录无效')
        return profile

    def snapshot(self):
        document, key = self._context()
        with data_lock(self.root):
            profile = self._profile(self._load(), key)
        paths = {item['path'] for item in document.get('files', [])}
        return {**profile, 'corrections': {path: value for path, value in profile['corrections'].items() if path in paths},
                'revision': revision(profile)}

    def update(self, payload):
        document, key = self._context()
        if document.get('restored_snapshot'):
            raise ValueError('恢复快照请先重新扫描，再修改整理设置')
        with data_lock(self.root):
            values = self._load()
            profile = self._profile(values, key)
            if payload.get('revision') != revision(profile):
                raise ValueError('整理设置已变化，请重新读取后保存')
            profile = json.loads(json.dumps(profile))
            action = payload.get('action')
            if action == 'preferences':
                destination = payload.get('destination')
                if (not isinstance(destination, str) or len(destination) > 4096 or '\x00' in destination
                        or destination and (not Path(destination).is_absolute() or '..' in Path(destination).parts)
                        or type(payload.get('all_batches')) is not bool):
                    raise ValueError('请填写目标绝对路径并选择有效的分批方式')
                profile.update(destination=destination, all_batches=payload['all_batches'])
            elif action == 'correct':
                ids, field, value = payload.get('ids'), payload.get('field'), payload.get('value')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
                    raise ValueError('请勾选 1–200 项后修正分类')
                records = {media_id(item['path']): item for item in document['files']}
                if any(identifier not in records for identifier in ids):
                    raise ValueError('勾选文件不属于本次扫描')
                selected = {records[identifier]['path'] for identifier in ids}
                if field in {'region', 'category', 'reset'}:
                    if field != 'reset' and any(records[identifier]['kind'] != '视频' for identifier in ids):
                        raise ValueError('地区或视频类型修正请只勾选视频')
                    groups = document.get('video_library', {}).get('groups', [])
                    works = {group.get('work_key', group.get('tag_key')) for group in groups if any(file['path'] in selected for file in group['files'])}
                    selected.update(file['path'] for group in groups if group.get('work_key', group.get('tag_key')) in works for file in group['files'])
                if field in {'month', 'reset'}:
                    from photo_bundle import photo_manifests
                    photos = {records[identifier]['path'] for identifier in ids if records[identifier]['kind'] == '照片'}
                    for group in photo_manifests(document, photos):
                        selected.update(group['photos'])
                if field not in {'month', 'region', 'category', 'reset'}:
                    raise ValueError('不支持此分类修正')
                if field == 'region' and value not in REGIONS or field == 'category' and value not in {'movie', 'home_video'}:
                    raise ValueError('分类值无效')
                if field == 'month' and (not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{3}/(?:0[1-9]|1[0-2])', value)):
                    raise ValueError('年月应为 YYYY/MM，例如 2026/10')
                for path in selected:
                    if field == 'reset':
                        profile['corrections'].pop(path, None)
                    else:
                        correction = profile['corrections'].setdefault(path, {})
                        correction[field] = value
                        if field == 'region':
                            correction['category'] = 'movie'
            else:
                raise ValueError('整理设置操作无效')
            values['profiles'][key] = profile
            self._profile(values, key)
            write_json(self.root, NAME, values)
        return self.snapshot()


def validate_settings(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('profiles'), dict):
        raise ValueError('整理设置损坏，请核对 organize-settings.json')
    for key, profile in value['profiles'].items():
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('整理来源标识无效')
        OrganizeSettings.validate_profile(profile)
    return value
