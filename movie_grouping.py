"""Shared filename clues and persistent, report-only grouping corrections."""
import copy
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
import unicodedata

from media_actions import media_id
from workspace_data import data_lock, read_json, write_json

NAME = 'library-grouping.json'


def source_path(value):
    return PureWindowsPath(value) if PureWindowsPath(value).is_absolute() else PurePosixPath(value)


def key_for(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode('utf-8')).hexdigest()


def clues(stem):
    tokens = re.findall(r'(?i)(?<![a-z0-9])(2160p|1080p|720p|4k|uhd|bluray|bdrip|webrip|web-dl|x264|x265|h264|h265|hevc|av1|hdr|dv)(?![a-z0-9])', stem)
    aliases = {'4k': '2160p', 'uhd': '2160p', 'h264': 'x264', 'h265': 'x265', 'hevc': 'x265'}
    edition = ' / '.join(sorted({aliases.get(token.lower(), token.lower()) for token in tokens}))
    if re.search(r'(?i)(?:[-_. ](?:C|CH))(?=$|[-_. ])', stem):
        edition = ' / '.join(filter(None, (edition, '字幕变体标记')))
    match = re.search(r'(?i)(?:^|[-_. \[（(])(?:CD|DISC|PART|PT)[-_. ]?(\d{1,3})(?=$|[-_. \]）)])', stem)
    return edition, int(match.group(1)) if match else 0


def clean_assignment(value):
    if not isinstance(value, dict):
        raise ValueError('分组修正内容无效')
    work_key, title, edition, part = [value.get(name) for name in ('work_key', 'title', 'edition', 'part')]
    if (not isinstance(work_key, str) or not re.fullmatch(r'[0-9a-f]{64}', work_key)
            or not isinstance(title, str) or not title.strip() or len(title) > 200
            or not isinstance(edition, str) or len(edition) > 100
            or any(ord(char) < 32 for char in title + edition)
            or type(part) is not int or not 0 <= part <= 999):
        raise ValueError('作品名限 200 字，版本限 100 字，分段须为 0–999（0 为未指定）')
    return {'work_key': work_key, 'title': title.strip(), 'edition': edition.strip(), 'part': part}


def load_assignments(root):
    value = read_json(root, NAME, {'version': 1, 'files': {}})
    return validate_assignments(value)


def validate_assignments(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('files'), dict):
        raise ValueError('影片分组修正文件损坏，保留原记录')
    result = {}
    for key, item in value['files'].items():
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('影片分组修正标识无效')
        result[key] = clean_assignment(item)
    return result


def apply_grouping(library, assignments, art_library=None):
    result = copy.deepcopy(library)
    groups = {}
    for original in result.get('groups', []):
        for file in original['files']:
            edition, part = clues(source_path(file['path']).stem)
            saved = assignments.get(media_id(file['path']))
            work_key = saved['work_key'] if saved else original['tag_key']
            title = saved['title'] if saved else original['title']
            edition = saved['edition'] if saved else edition
            part = saved['part'] if saved else part
            key = (work_key, unicodedata.normalize('NFC', edition).casefold())
            if key not in groups:
                groups[key] = {**original, 'title': title, 'work_key': work_key, 'tag_key': work_key,
                               'edition': edition, 'group_key': key_for(key), 'files': [],
                               'poster': '', 'poster_source': '', 'stills': [], 'metadata': {}}
            file.update(edition=edition, part=part, grouping_manual=bool(saved))
            groups[key]['files'].append(file)
    for group in groups.values():
        group['files'].sort(key=lambda file: (file['part'] == 0, file['part'], file['path']))
        group['needs_review'] = any(file['issues'] or file['duplicate_group'] for file in group['files'])
        group['has_sidecars'] = any(file['sidecars'] for file in group['files'])
        paths = {file['path'] for file in group['files']}
        # Keep report-local art only when its original group was not split.
        contributors = [old for old in (art_library or library).get('groups', []) if {file['path'] for file in old['files']} <= paths]
        if len(contributors) == 1:
            old = contributors[0]
            group['needs_review'] |= bool(old.get('needs_review'))
            for name in ('poster', 'poster_source', 'stills', 'metadata'):
                if name in old:
                    group[name] = copy.deepcopy(old[name])
        elif len(contributors) > 1:
            group['needs_review'] = True
        parts = [file['part'] for file in group['files'] if file['part']]
        group['grouping_warnings'] = []
        if len(parts) != len(set(parts)):
            group['grouping_warnings'].append('同一版本有重复分段编号，请核对副本或版本')
        if parts and sorted(set(parts)) != list(range(1, max(parts) + 1)):
            group['grouping_warnings'].append('分段编号不连续，可能缺段；仅依据文件名或人工设置')
        group['needs_review'] |= bool(group['grouping_warnings'])
        result.setdefault('issues', []).extend({'path': '；'.join(file['path'] for file in group['files']),
            'reason': warning, 'type': '影片分组'} for warning in group['grouping_warnings'])
    result['groups'] = sorted(groups.values(), key=lambda group: (group['title'].casefold(), group['edition'].casefold(), group['group_key']))
    result['poster_count'] = sum(bool(group.get('poster')) for group in result['groups'])
    result['frame_count'] = sum(group.get('poster_source', '').startswith('视频截帧：') for group in result['groups'])
    result['still_count'] = sum(len(group.get('stills', [])) for group in result['groups'])
    return result


def update_assignments(root, document, edits):
    files = {media_id(file['path']): file for group in document['video_library']['groups'] for file in group['files']}
    allowed = {group.get('work_key', group['tag_key']): group['title'] for group in document['video_library']['groups']}
    if not isinstance(edits, list) or not 1 <= len(edits) <= 200:
        raise ValueError('每次修正 1–200 个视频')
    prepared = {}
    for edit in edits:
        if not isinstance(edit, dict) or edit.get('id') not in files or edit['id'] in prepared:
            raise ValueError('分组修正仅接受当前报告的视频，不能重复提交')
        if edit.get('reset') is True:
            prepared[edit['id']] = None
            continue
        edit = dict(edit)
        if edit.get('work_key') == 'new' and isinstance(edit.get('title'), str):
            edit['work_key'] = key_for(['manual', unicodedata.normalize('NFC', edit['title'].strip()).casefold()])
        assignment = clean_assignment(edit)
        if assignment['work_key'] in allowed and assignment['title'] != allowed[assignment['work_key']]:
            raise ValueError('所选作品名称已变化，请刷新后重新修正')
        if assignment['work_key'] not in allowed:
            # A new work has a deterministic identifier tied to the supplied title.
            if assignment['work_key'] != key_for(['manual', unicodedata.normalize('NFC', assignment['title']).casefold()]):
                raise ValueError('请选择当前作品或提供有效的新作品')
        prepared[edit['id']] = assignment
    with data_lock(root):
        values = load_assignments(root)
        for key, value in prepared.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        write_json(root, NAME, {'version': 1, 'files': values})
    return values
