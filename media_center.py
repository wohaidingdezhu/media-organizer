"""Read-only media views and bounded, private browsing metadata for both platforms."""
import math
import re
import secrets
import unicodedata

from media_actions import media_id
from movie_grouping import source_path
from workspace_data import data_lock, read_json, write_json

NAME = 'media-center.json'
LIMIT = 4 * 1024 * 1024


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def validate_state(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('媒体中心资料格式无效')
    favorites, collections, recent = (value.get(key) for key in ('favorites', 'collections', 'recent'))
    if not isinstance(favorites, dict) or len(favorites) > 20000 or any(not valid_id(key) or type(flag) is not bool for key, flag in favorites.items()):
        raise ValueError('收藏资料无效')
    if not isinstance(recent, list) or len(recent) > 100 or any(not valid_id(key) for key in recent):
        raise ValueError('最近打开记录无效')
    labels = value.setdefault('labels', {})
    if not isinstance(labels, dict) or len(labels) > 20000:
        raise ValueError('标签资料无效')
    for key, tags in labels.items():
        if not valid_id(key) or not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 50 or any(ord(char) < 32 for char in tag) for tag in tags):
            raise ValueError('每份媒体最多20个标签，每个不超过50字')
    if not isinstance(collections, dict) or len(collections) > 500:
        raise ValueError('合集数量无效')
    names = set()
    for key, item in collections.items():
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key) or not isinstance(item, dict):
            raise ValueError('合集标识无效')
        name = item.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise ValueError('合集名称应为1到80字')
        folded = unicodedata.normalize('NFC', name).strip().casefold()
        if folded in names:
            raise ValueError('合集名称已存在')
        names.add(folded)
        ids = item.get('ids')
        if not isinstance(ids, list) or len(ids) > 10000 or any(not valid_id(identifier) for identifier in ids) or len(set(ids)) != len(ids):
            raise ValueError('合集媒体记录无效')
    return value


class CenterState:
    def __init__(self, root):
        self.root = root

    def snapshot(self):
        with data_lock(self.root):
            return validate_state(read_json(self.root, NAME, {'version': 1, 'favorites': {}, 'collections': {}, 'recent': []}, LIMIT))

    def update(self, payload, allowed):
        with data_lock(self.root):
            value = self.snapshot()
            action = payload.get('action')
            identifier = payload.get('id')
            if action in {'favorite', 'recent', 'labels'}:
                if not valid_id(identifier) or identifier not in allowed:
                    raise ValueError('媒体不属于当前资料库')
                if action == 'favorite':
                    if type(payload.get('value')) is not bool:
                        raise ValueError('请选择收藏状态')
                    value['favorites'][identifier] = payload['value']
                elif action == 'recent':
                    value['recent'] = [identifier] + [old for old in value['recent'] if old != identifier][:99]
                else:
                    value['labels'][identifier] = payload.get('tags')
            elif action in {'create', 'rename', 'delete_collection', 'members'}:
                if action != 'create' and identifier not in value['collections']:
                    raise ValueError('合集已不存在，请刷新')
                if action in {'create', 'rename'}:
                    name = payload.get('name')
                    if not isinstance(name, str):
                        raise ValueError('请输入合集名称')
                    if action == 'create':
                        identifier = secrets.token_hex(16)
                        value['collections'][identifier] = {'name': '', 'ids': []}
                    value['collections'][identifier]['name'] = unicodedata.normalize('NFC', name).strip()
                elif action == 'delete_collection':
                    del value['collections'][identifier]
                else:
                    ids = payload.get('ids')
                    if not isinstance(ids, list) or not ids or len(ids) > 200 or any(not valid_id(key) or key not in allowed for key in ids):
                        raise ValueError('一次请选择1到200个本库媒体')
                    if type(payload.get('remove', False)) is not bool:
                        raise ValueError('合集操作无效')
                    before = value['collections'][identifier]['ids']
                    value['collections'][identifier]['ids'] = [key for key in before if key not in ids] if payload.get('remove') else list(dict.fromkeys(before + ids))
            else:
                raise ValueError('媒体中心操作无效')
            validate_state(value)
            import json
            if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > LIMIT:
                raise ValueError('媒体中心资料过大')
            write_json(self.root, NAME, value)
            return value


def directory_catalog(items, roots):
    """Build a directory tree solely from saved paths; never traverse the disk."""
    boundaries = [source_path(value) for value in roots if isinstance(value, str) and source_path(value).is_absolute()]
    nodes, seen = {}, {}

    def add(path, parent=''):
        key = str(path)
        if key not in nodes:
            nodes[key] = {'name': path.name or key, 'parent': parent, 'children': [], 'ids': []}
            seen[key] = set()
        return nodes[key]

    for root in boundaries:
        add(root)
    for item in items:
        for folder in dict.fromkeys(str(source_path(file['path']).parent) for file in item['files']):
            if folder not in nodes:
                path = source_path(folder)
                # Exact spelling keeps case and Unicode-equivalent report paths
                # distinct, including restored reports from another operating system.
                matches = [root for root in boundaries if type(root) is type(path) and path.parts[:len(root.parts)] == root.parts]
                boundary = max(matches, key=lambda root: len(root.parts)) if matches else path.parents[len(path.parents) - 1] if path.parents else path
                current = path
                while True:
                    parent = '' if str(current) == str(boundary) else str(current.parent)
                    add(current, parent)
                    if not parent:
                        break
                    current = current.parent
            if item['id'] not in seen[folder]:
                nodes[folder]['ids'].append(item['id'])
                seen[folder].add(item['id'])
    for path, node in nodes.items():
        if node['parent'] in nodes:
            nodes[node['parent']]['children'].append(path)
    return nodes


def center_catalog(document, state, notes=None):
    """Use scanned facts only. Missing duration/camera data stays unknown."""
    records = {item['path']: item for item in document.get('files', []) if isinstance(item, dict) and isinstance(item.get('path'), str)}
    previews = document.get('previews', {})
    result, covered, video_art = [], set(), {}
    notes = notes or {}

    def file_item(path):
        record = records[path]
        return {'id': media_id(path), 'path': path, 'name': source_path(path).name,
                'bytes': record.get('bytes', 0), 'mtime': record.get('mtime', 0)}

    def entry(identifier, kind, title, files, preview='', metadata=None, tags=None, tag_key=''):
        if not files or not valid_id(identifier):
            return
        metadata = metadata or {}
        tags = tags or []
        stamp = max((file['mtime'] for file in files if type(file['mtime']) in {int, float} and math.isfinite(file['mtime'])), default=0)
        result.append({'id': identifier, 'kind': kind, 'title': title, 'files': files, 'preview': preview,
                       'folder': str(source_path(files[0]['path']).parent), 'mtime': stamp,
                       'metadata': metadata, 'tags': list(dict.fromkeys(tags + state.get('labels', {}).get(identifier, []))), 'local_tags': state.get('labels', {}).get(identifier, []), 'tag_key': tag_key,
                       'personal': notes.get(tag_key, {'rating': 0, 'note': ''}),
                       'favorite': state['favorites'].get(identifier, '收藏' in tags),
                       'bytes': sum(file['bytes'] for file in files)})

    for group in document.get('video_library', {}).get('groups', []):
        files = [file_item(file['path']) for file in group.get('files', []) if file.get('path') in records]
        if not files:
            continue
        metadata = group.get('metadata') or {}
        for file in files:
            video_art[file['path']] = group.get('poster', '')
        camera = all(re.match(r'^(IMG|VID|MOV|DSC|PXL|MVI|GOPR|VIDEO)[-_ ]?\d', source_path(file['path']).stem, re.I) for file in files)
        movie = bool(metadata or group.get('type') == '编号' and not camera or re.search(r'电影|movies|films', str(source_path(files[0]['path']).parent), re.I))
        if movie:
            identifier = group.get('group_key') or group.get('tag_key')
            entry(identifier, 'movie', metadata.get('title') or group.get('title') or files[0]['name'], files,
                  group.get('poster', ''), metadata, group.get('tags', []), group.get('tag_key', ''))
            covered.update(file['path'] for file in files)
    for path, record in records.items():
        if path in covered or record.get('kind') not in {'照片', '视频'}:
            continue
        image = record.get('image') if isinstance(record.get('image'), dict) else {}
        entry(media_id(path), 'photo' if record['kind'] == '照片' else 'video', source_path(path).name,
              [file_item(path)], previews.get(path, '') if record['kind'] == '照片' else video_art.get(path, ''), image)
    by_id = {item['id']: item for item in result}
    folders = {}
    for item in result:
        for folder in dict.fromkeys(str(source_path(file['path']).parent) for file in item['files']):
            folders.setdefault(folder, []).append(item['id'])
    return {'items': result, 'state': state, 'folders': folders, 'directories': directory_catalog(result, document.get('roots', [])),
            'recent': [key for key in state['recent'] if key in by_id],
            'created_at': document.get('created_at', ''), 'restored': bool(document.get('restored_snapshot'))}
