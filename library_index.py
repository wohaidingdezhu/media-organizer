"""Persistent report-only catalog. File operations always use a fresh report check."""
import hashlib
import json
from pathlib import Path, PureWindowsPath, PurePosixPath
import re

import portable_fs as fs
from workspace_data import data_lock, read_json, write_json
from media_actions import media_id

INDEX = 'library-index.json'

def source_path(value):
    return PureWindowsPath(value) if PureWindowsPath(value).is_absolute() else PurePosixPath(value)


def sync_index(root):
    root = fs.private_data_path(root)
    with data_lock(root):
        index = read_json(root, INDEX, {'version': 1, 'scans': {}, 'items': {}})
        if (not isinstance(index, dict) or index.get('version') != 1 or
                not isinstance(index.get('scans'), dict) or not isinstance(index.get('items'), dict)):
            raise ValueError('长期资料库索引损坏；保留原文件，请从备份恢复')
        if not all(isinstance(item, dict) and all(key in item for key in ('path','folder','root','report_id','kind','bytes','state')) for item in index['items'].values()):
            raise ValueError('长期资料库索引项目损坏')
        warnings, changed = [], False
        for name in sorted(fs.listdir(root)):
            if not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', name):
                continue
            try:
                from file_operations import open_directory
                parent = open_directory(Path(root) / name)
                try:
                    info = fs.stat('report.json', dir_fd=parent, follow_symlinks=False)
                    stamp = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
                finally:
                    fs.close(parent)
                previous = index['scans'].get(name)
                if isinstance(previous, dict) and previous.get('stamp') == stamp:
                    continue
                raw = fs.read_private_file(root, (name, 'report.json'))
                digest = hashlib.sha256(raw).hexdigest()
                document = json.loads(raw)
                roots, records = document['roots'], document['files']
                if not isinstance(roots, list) or not all(isinstance(value, str) for value in roots) or not isinstance(records, list):
                    raise ValueError('报告清单无效')
                prepared = {}
                for record in records:
                    path = record.get('path') if isinstance(record, dict) else None
                    if not isinstance(path, str) or not source_path(path).is_absolute() or '..' in source_path(path).parts or '\x00' in path:
                        raise ValueError('报告路径无效')
                    if record.get('kind') not in {'照片', '视频'} or type(record.get('bytes')) is not int or record['bytes'] < 0:
                        raise ValueError('报告媒体类型或大小无效')
                    key = media_id(path)
                    if key in prepared:
                        raise ValueError('报告路径重复')
                    prepared[key] = {'id': key, 'path': path, 'name': source_path(path).name, 'folder': str(source_path(path).parent),
                        'root': record.get('root', next((value for value in roots if source_path(value) in source_path(path).parents), '')),
                        'kind': record['kind'], 'bytes': record['bytes'], 'report_id': name, 'state': 'new' if key not in index['items'] else 'changed' if (index['items'][key].get('sha256') != record.get('sha256') or index['items'][key].get('bytes') != record['bytes'] or index['items'][key].get('mtime') != record.get('mtime')) else 'seen',
                        'mtime': record.get('mtime'),
                        'created_at': document.get('created_at', ''), 'sha256': record.get('sha256', ''),
                        'preview': document.get('previews', {}).get(path, ''), 'restored': bool(document.get('restored_snapshot'))}
                # Keep the newest snapshot for each path, including disks not currently attached.
                for key, old in index['items'].items():
                    if (old.get('root') in roots and old.get('report_id', '') < name and key not in prepared):
                        old['state'] = 'not_seen'
                for key, item in prepared.items():
                    if key not in index['items'] or index['items'][key].get('report_id', '') <= name:
                        index['items'][key] = item
                index['scans'][name] = {'sha256': digest, 'stamp': stamp}
                changed = True
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                warnings.append(f'{name} 未纳入长期索引：{error}')
        if changed:
            write_json(root, INDEX, index)
        return index, warnings


def catalog(root, query='', folder='', kind='', state='', page=0):
    if type(page) is not int or page < 0 or not all(isinstance(value, str) for value in (query, folder, kind, state)):
        raise ValueError('资料库筛选无效')
    index, warnings = sync_index(root)
    items = list(index['items'].values())
    query = query.strip().casefold()
    selected = [item for item in items if (not kind or item['kind'] == kind) and
                (not state or item['state'] == state) and
                (not folder or source_path(folder) == source_path(item['folder']) or source_path(folder) in source_path(item['folder']).parents) and
                (not query or query in item['path'].casefold())]
    selected.sort(key=lambda item: (item['path'].casefold(), item['path']))
    pages = max(1, (len(selected) + 49) // 50)
    page = min(page, pages - 1)
    # Folder cards show immediate children, retaining full paths for same-name folders.
    parents = {source_path(item['folder']) for item in items}
    roots = sorted({item['root'] for item in items if item['root']})
    children = set()
    if folder:
        base = source_path(folder)
        for parent in parents:
            if base in parent.parents:
                children.add(str(base / parent.relative_to(base).parts[0]))
    else:
        children.update(roots)
    return {'items': selected[page * 50:page * 50 + 50], 'total': len(selected), 'page': page, 'pages': pages,
            'all_count': len(items), 'scans': len(index['scans']), 'warnings': warnings,
            'folders': [{'path': child, 'name': source_path(child).name or child,
                         'count': sum(source_path(child) == source_path(item['folder']) or source_path(child) in source_path(item['folder']).parents for item in items)}
                        for child in sorted(children)],
            'message': '长期清单来自扫描快照；未扫描到不表示删除，操作原文件前仍核对扫描身份。'}
