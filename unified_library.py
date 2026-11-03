"""Materialize immutable, report-only views of all currently indexed media.

Source identities are retained verbatim. This never opens original media and
never treats discovery-only records as permission to operate on a file.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import portable_fs as fs
from library_index import sync_index
from media_actions import media_id
from workspace_data import data_lock, read_json, write_json

PENDING = 'scan-pending.json'


def build_library(output, pending=None):
    with data_lock(output):
        index, warnings = sync_index(output, complete_only=True)
        identity = {'scans': index['scans'], 'pending': pending, 'warnings': warnings, 'schema': 3}
        revision = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        directory = fs.ensure_private_directory(Path(output) / ('library-' + revision))
        cached = read_json(directory, 'report.json')
        if cached is not None:
            return directory, cached
        selected = {entry['path']: entry for entry in index['items'].values() if entry['state'] != 'not_seen'}
        reports = {}
        for name in sorted({entry['report_id'] for entry in selected.values()}):
            if not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', name):
                raise ValueError('资料库来源报告标识无效')
            reports[name] = json.loads(fs.read_private_file(output, (name, 'report.json')))
        files, roots, groups, previews, assets, pairs = {}, set(), {}, {}, {}, []

        def asset(report, relative, identifier):
            candidates = [('covers', 'repair-' + identifier + '.png')]
            if isinstance(relative, str) and re.fullmatch(r'(?:previews|covers)/[A-Za-z0-9_-]+\.png', relative):
                candidates.append(tuple(relative.split('/')))
            key = 'previews/u-' + hashlib.sha256((report + identifier + str(relative)).encode()).hexdigest() + '.png'
            assets[key] = [[report, *parts] for parts in candidates]
            return key

        for name, doc in reports.items():
            for record in doc['files']:
                path = record['path']
                if path not in selected or selected[path]['report_id'] != name:
                    continue
                photo_enabled = doc.get('image_inspection', {}).get('enabled') is not False
                auto_preview = photo_enabled if record['kind'] == '照片' else doc.get('options', {}).get('video_covers_enabled', photo_enabled) is not False
                files[path] = {**record, 'source_report': name, 'browse_only': bool(doc.get('restored_snapshot')),
                               'auto_preview': auto_preview}
                roots.add(record.get('root') or selected[path]['root'])
                if record['kind'] == '照片':
                    previews[path] = asset(name, doc.get('previews', {}).get(path, ''), media_id(path))
            for group in doc.get('video_library', {}).get('groups', []):
                members = [member for member in group.get('files', []) if member.get('path') in files and files[member['path']]['source_report'] == name]
                if not members:
                    continue
                key = group.get('group_key') or group.get('tag_key')
                if not key:
                    continue
                poster = asset(name, group.get('poster', ''), media_id(members[0]['path']))
                prior = groups.get(key)
                groups[key] = {**group, 'files': (prior['files'] if prior else []) + members, 'poster': poster}
            for pair in (doc.get('similar') or {}).get('pairs', []):
                if all(path in selected and selected[path]['report_id'] == name for path in (pair.get('left'), pair.get('right'))):
                    pairs.append(pair)
        # In-progress discovery adds new/changed files without declaring old
        # files absent or discarding their known metadata before completion.
        if pending:
            for record in pending.get('files', []):
                prior = files.get(record['path'])
                if prior and prior.get('source_signature') == record.get('source_signature'):
                    continue
                files[record['path']] = {**record, 'browse_only': True, 'source_report': ''}
                previews.pop(record['path'], None)
            roots.update(pending.get('roots', []))
        pairs = [pair for pair in pairs if all(not files[path].get("browse_only") for path in (pair["left"], pair["right"]))]
        valid_groups = []
        for group in groups.values():
            members = [member for member in group['files'] if files[member['path']].get('source_report')]
            if members:
                valid_groups.append({**group, 'files': members})
        hashed = defaultdict(list)
        for record in files.values():
            digest = record.get('sha256', '')
            if not record.get('browse_only') and re.fullmatch(r'[0-9a-f]{64}', digest):
                hashed[(record['bytes'], digest)].append(record['path'])
        duplicates = [{'sha256': digest, 'bytes_each': size, 'paths': paths, 'redundant_logical_bytes': size * (len(paths)-1)}
                      for (size, digest), paths in hashed.items() if len(paths) > 1]
        document = {'version': 10, 'unified_library': True, 'revision': revision,
                    'created_at': max((doc.get('created_at', '') for doc in reports.values()), default=''),
                    'roots': sorted(root for root in roots if root), 'files': list(files.values()),
                    'previews': previews, 'preview_sources': assets, 'duplicates': duplicates,
                    'similar': {'pairs': pairs,
                                'enabled': any(doc.get('similar', {}).get('enabled') is not False for doc in reports.values()),
                                'truncated': any(doc.get('similar', {}).get('truncated') for doc in reports.values())},
                    'video_library': {'groups': valid_groups},
                    'issues': [{'path': '', 'reason': warning} for warning in warnings],
                    'sidecars': [], 'folder_groups': [], 'options': {},
                    'pending_scan': bool(pending), 'source_reports': sorted(reports),
                    'restored_snapshot': bool(reports) and all(doc.get('restored_snapshot') for doc in reports.values())}
        write_json(directory, 'report.json', document)
        return directory, document


def publish_discovery(output, roots, records):
    """Atomically publish facts for browsing, before decoding or full hashing."""
    files = [{**{key: value for key, value in item.items() if not key.startswith('_')},
              'source_signature': list(item['_signature']), 'browse_only': True} for item in records]
    with data_lock(output):
        write_json(output, PENDING, {'roots': [str(root) for root in roots], 'files': files})
