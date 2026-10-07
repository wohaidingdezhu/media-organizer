"""Explicit copy-only film bundles; uncertain attachments are never included."""
from pathlib import Path, PurePosixPath

from media_actions import checked_stat, file_signature, media_id
from organization_plan import _target_error, _canonical_target

SIDECARS = set('srt ass ssa sub idx vtt nfo'.split())


class BundleActions:
    def __init__(self, media, document):
        self.media = media
        self.records = dict(media.records)
        self.roots = media.roots
        if document.get('restored_snapshot'):
            return
        for item in document.get('sidecars', []):
            path = Path(item['path'])
            expected = item.get('source_signature')
            if (item.get('extension') in SIDECARS and path.is_absolute() and '..' not in path.parts
                    and any(root in path.parents for root in self.roots)
                    and isinstance(expected, list) and len(expected) == 5 and all(type(value) is int for value in expected)
                    and type(item.get('bytes')) is int and item['bytes'] >= 0):
                self.records[media_id(str(path))] = {**item, 'kind': '影片附件'}

    def validate(self, identifier):
        if identifier in self.media.records:
            return self.media.validate(identifier)
        record = self.records.get(identifier)
        if record is None:
            raise ValueError('附件缺少本次扫描的身份记录，请重新扫描')
        info = checked_stat(record['path'])
        if file_signature(info) != record['source_signature']:
            raise ValueError('影片附件自扫描后变化，请重新扫描')
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
