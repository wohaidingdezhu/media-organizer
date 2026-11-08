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


def with_pending(document, pending):
    """Overlay browse-only scan facts without opening media or writing snapshots."""
    if not pending or pending.get('finished') and pending.get('report') in document.get('source_reports', []):
        return {**document, 'scan_revision': ''}
    files = {record['path']: record for record in document['files']}
    previews = dict(document.get('previews', {}))
    assets = dict(document.get('preview_sources', {}))
    for record in pending.get('files', []):
        prior = files.get(record['path'])
        same = prior and prior.get('source_signature') == record.get('source_signature')
        files[record['path']] = {**(prior if same else {}), **record,
                                 'browse_only': bool(prior.get('browse_only')) if same else True,
                                 'source_report': prior.get('source_report', '') if same else '',
                                 'auto_preview': prior.get('auto_preview', True) if same else False}
        if same:
            for key in ('sha256', 'hash_status', 'suggested_path', 'date_source', 'reason'):
                if not record.get(key):
                    files[record['path']][key] = prior.get(key, '')
        if not same:
            previews.pop(record['path'], None)
    report = pending.get('report', '')

    def asset(relative):
        if (not re.fullmatch(r'scan-[A-Za-z0-9_-]{1,120}', report)
                or not isinstance(relative, str) or not re.fullmatch(r'(?:previews|covers)/[A-Za-z0-9_-]+\.png', relative)):
            return ''
        route = 'previews/live-' + hashlib.sha256((report + '/' + relative).encode()).hexdigest() + '.png'
        assets[route] = [[report, *relative.split('/')]]
        return route

    for path, relative in pending.get('previews', {}).items():
        if path in files:
            route = asset(relative)
            if route:
                previews[path] = route
    staged_groups = pending.get('groups', [])
    covered = {file['path'] for group in staged_groups for file in group.get('files', [])}
    groups = {}
    known_groups = {group.get('group_key') or group.get('tag_key'): group
                    for group in document.get('video_library', {}).get('groups', [])
                    if any(files[file['path']].get('source_report') for file in group['files'])}
    for group in document.get('video_library', {}).get('groups', []):
        members = [member for member in group['files'] if member['path'] not in covered and files[member['path']].get('source_report')]
        if members:
            groups[group.get('group_key') or group.get('tag_key')] = {**group, 'files': members}
    for group in staged_groups:
        members = [file for file in group.get('files', []) if file['path'] in files]
        if members:
            key = group.get('group_key') or group.get('tag_key')
            prior = known_groups.get(key, {})
            metadata = {**prior.get('metadata', {}), **{name: value for name, value in group.get('metadata', {}).items() if value}}
            groups[key] = {**prior, **group, 'metadata': metadata,
                           'tags': list(dict.fromkeys(prior.get('tags', []) + group.get('tags', []))),
                           'files': groups.get(key, {}).get('files', []) + members,
                           'poster': asset(group.get('poster', '')) or prior.get('poster', '')}
    safe = {path for path, record in files.items() if not record.get('browse_only')}
    return {**document, 'files': list(files.values()), 'roots': sorted(set(document['roots']) | set(pending.get('roots', []))),
            'previews': previews, 'preview_sources': assets, 'pending_scan': not pending.get('finished', False),
            'scan_complete': bool(pending.get('finished')),
            'scan_revision': pending.get('revision', ''),
            'duplicates': [group for group in document.get('duplicates', []) if all(path in safe for path in group['paths'])],
            'similar': {**document.get('similar', {}), 'pairs': [pair for pair in document.get('similar', {}).get('pairs', [])
                                                             if pair['left'] in safe and pair['right'] in safe]},
            'video_library': {**document.get('video_library', {}), 'groups': list(groups.values())}}


def build_library(output, pending=None):
    with data_lock(output):
        index, warnings = sync_index(output, complete_only=True)
        identity = {'scans': index['scans'], 'pending': pending, 'warnings': warnings, 'schema': 4}
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
                if prior:
                    # A later part/edition often has no local NFO or artwork.
                    # Empty facts must not erase another part's known details.
                    metadata = {**(prior.get('metadata') or {}),
                                **{key: value for key, value in (group.get('metadata') or {}).items() if value}}
                    if not re.fullmatch(r'(?:previews|covers)/[A-Za-z0-9_-]+\.png', group.get('poster', '')):
                        poster = prior['poster']
                    group = {**group, 'metadata': metadata,
                             'tags': list(dict.fromkeys(prior.get('tags', []) + group.get('tags', [])))}
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
