"""Explicit copy-only film bundles; uncertain attachments are never included."""
from pathlib import Path, PurePosixPath

from media_actions import checked_stat, file_signature, media_id
from movie_grouping import source_path
from organization_plan import _target_error, _canonical_target

SIDECARS = set('srt ass ssa sub idx vtt nfo'.split())


def movie_bundle_audit(document, identifier, plan_items=()):
    """Review one current edition using report records, never original media."""
    from movie_attachments import attachment_manifests
    if not isinstance(identifier, str) or len(identifier) != 64 or any(char not in '0123456789abcdef' for char in identifier):
        raise ValueError('视频标识无效')
    records = {item['path']: item for item in document.get('files', [])}
    video = next((item for item in records.values() if item.get('kind') == '视频' and media_id(item['path']) == identifier), None)
    if video is None:
        raise ValueError('视频不属于本次扫描报告')
    library = document.get('video_library', {})
    if not isinstance(library, dict):
        raise ValueError('视频缺少影片资料库，请重新扫描')
    groups = library.get('groups', [])
    matches = [(number, group) for number, group in enumerate(groups)
               if any(file['path'] == video['path'] for file in group.get('files', []))]
    if len(matches) != 1:
        raise ValueError('视频缺少唯一影片分组，请重新扫描或修正分组')
    number, group = matches[0]
    paths = [item['path'] for item in group['files']]
    if len(set(paths)) != len(paths) or any(records.get(path, {}).get('kind') != '视频' for path in paths):
        raise ValueError('影片组的视频清单无效，请重新扫描')
    summary = attachment_manifests(document)[number]
    plans = {item['id']: item for item in plan_items}
    records.update({item['path']: item for item in document.get('sidecars', [])})
    videos = [{'path': item['path'], 'relative': source_path(item['path']).name, 'label': '视频',
               'bytes': records[item['path']].get('bytes', 0)} for item in group['files']]
    items = []
    for item in videos + summary['items']:
        key = media_id(item['path'])
        signature = records[item['path']].get('source_signature')
        identity = isinstance(signature, list) and len(signature) == 5 and all(type(value) is int for value in signature)
        plan = plans.get(key)
        items.append({**item, 'id': key, 'identity_available': identity,
                      'plan': {'state': plan['state'], 'target': plan['suggested_path'],
                               'blocked_reason': plan['blocked_reason']} if plan else None})
    labels = {'subtitle': '字幕', 'nfo': 'NFO', 'cover': '封面', 'still': '剧照'}
    return {'title': group.get('title', '影片'), 'edition': group.get('edition', ''),
            'group_key': summary['group_key'], 'grouping_revision': library.get('grouping_revision', ''),
            'media_kind': '视频', 'media_ids': [media_id(item['path']) for item in videos],
            'items': items, 'counts': {'视频': len(videos), **{labels[key]: value for key, value in summary['counts'].items()}},
            'skipped': summary['skipped'], 'missing': summary['missing'],
            'identity_missing': sum(not item['identity_available'] for item in items),
            'restored_snapshot': bool(document.get('restored_snapshot')),
            'message': '只显示本次扫描记录、当前影片分组及分类计划，不检查原文件是否仍存在或视频能否播放。只核对当前作品版本，附件缺少不表示影片损坏。'}


class BundleActions:
    def __init__(self, media, document, sidecars=SIDECARS, label='影片附件'):
        self.media = media
        self.records = dict(media.records)
        self.roots = media.roots
        if document.get('restored_snapshot'):
            return
        for item in document.get('sidecars', []):
            path = Path(item['path'])
            expected = item.get('source_signature')
            if (item.get('extension') in sidecars and path.is_absolute() and '..' not in path.parts
                    and any(root in path.parents for root in self.roots)
                    and isinstance(expected, list) and len(expected) == 5 and all(type(value) is int for value in expected)
                    and type(item.get('bytes')) is int and item['bytes'] >= 0):
                self.records[media_id(str(path))] = {**item, 'kind': label}

    def validate(self, identifier):
        if identifier in self.media.records:
            return self.media.validate(identifier)
        record = self.records.get(identifier)
        if record is None:
            raise ValueError('附件缺少本次扫描的身份记录，请重新扫描')
        info = checked_stat(record['path'])
        if file_signature(info) != record['source_signature']:
            raise ValueError(record['kind'] + '自扫描后变化，请重新扫描')
        return record, info


def bundle_selection(document, ids, current, media):
    from movie_attachments import attachment_manifests
    groups = document.get('video_library', {}).get('groups', [])
    selected = {media.records[key]['path'] for key in ids if key in media.records}
    if len(selected) != len(ids) or any(media.records[key]['kind'] != '视频' for key in ids):
        raise ValueError('整组复制请只勾选视频，封面和附件将在预览中展开')
    manifests = attachment_manifests(document)
    main, companions, skipped = {}, {}, []
    for number, group in enumerate(groups):
        paths = {file['path'] for file in group['files']}
        if not paths & selected:
            continue
        parents = set()
        for file in group['files']:
            path = file['path']
            key = media_id(path)
            item = current.get(key)
            if not item or item['state'] != 'include' or not item['selectable']:
                raise ValueError('整组中的视频须先纳入分类计划：' + path)
            if media.records[key].get('source_signature') is None:
                raise ValueError('整组复制需要新扫描的文件身份记录')
            if PurePosixPath(item['suggested_path']).stem != Path(path).stem:
                raise ValueError('整组复制须保留视频原文件名，以保持字幕关联；请先恢复文件名：' + path)
            target_parent = PurePosixPath(item['suggested_path']).parent
            main[key] = str(target_parent / Path(path).name)
            parents.add(str(target_parent))
        if len(parents) != 1:
            raise ValueError('同一影片版本的分类目录不一致，请先调整到同一目录')
        parent, = parents
        skipped.extend(manifests[number]['skipped'])
        for item in manifests[number]['items']:
            path, name = item['path'], item['relative']
            key = media_id(path)
            target = str(PurePosixPath(parent) / name)
            if key in companions and companions[key] != target:
                raise ValueError('附件对应多个目标，请分别复制影片组：' + path)
            companions[key] = target
    if not main:
        raise ValueError('所选视频缺少影片分组，请重新扫描')
    actions = BundleActions(media, document)
    for key in companions:
        record, _ = actions.validate(key)
        if record.get('source_signature') is None:
            raise ValueError('封面缺少文件身份记录，请重新扫描')
    targets = {**main, **companions}
    if len(targets) > 200:
        raise ValueError('整组展开后超过 200 项，请分组处理')
    normalized = []
    for target in targets.values():
        if _target_error(target):
            raise ValueError('影片或附件名称不满足双平台规则：' + target)
        canonical = _canonical_target(target)
        if any(canonical == old or canonical.startswith(old + '/') or old.startswith(canonical + '/') for old in normalized):
            raise ValueError('整组目标存在重名或文件与目录冲突：' + target)
        normalized.append(canonical)
    return actions, targets, set(companions), list({item['path']: item for item in skipped}.values())
