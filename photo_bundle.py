"""Conservative, report-only photo associations for explicit copy previews."""
from collections import defaultdict
from pathlib import PurePosixPath

from media_actions import media_id
from movie_attachments import normalized
from movie_grouping import source_path
from movie_bundle import BundleActions
from organization_plan import _canonical_target, _target_error

RAW = set('dng cr2 cr3 nef arw raf orf rw2 pef srw'.split())
JPEG = {'jpg', 'jpeg'}
PAIRED_VIDEO = {'mov', 'mp4', 'm4v'}


def photo_manifests(document, selected):
    """Only a unique RAW/JPEG pair forms a multi-photo group; names are hints."""
    photos, videos, sidecars = defaultdict(list), defaultdict(list), defaultdict(list)
    by_path = {}
    for item in document.get('files', []):
        path = source_path(item['path'])
        key = (str(path.parent), normalized(path.stem))
        if item.get('kind') == '照片':
            photos[key].append(item)
            by_path[item['path']] = key
        elif item.get('kind') == '视频':
            videos[key].append(item)
    for item in document.get('sidecars', []):
        if item.get('extension') not in {'xmp', 'aae'}:
            continue
        path = source_path(item['path'])
        stem = path.stem
        # IMG.dng.xmp names a particular photo, whereas IMG.xmp names the pair.
        embedded = source_path(stem).suffix.lstrip('.').casefold()
        if embedded in RAW | JPEG | {'png', 'heic', 'heif', 'tif', 'tiff', 'gif', 'bmp', 'webp', 'avif'}:
            stem = source_path(stem).stem
        else:
            embedded = ''
        sidecars[(str(path.parent), normalized(stem))].append((item, embedded))
    result, seen = [], set()
    for selected_path in sorted(selected):
        if selected_path not in by_path:
            raise ValueError('整组复制请只勾选照片，配套文件将在预览中展开')
        key = by_path[selected_path]
        candidates = photos[key]
        raw = [item for item in candidates if item.get('extension') in RAW]
        jpeg = [item for item in candidates if item.get('extension') in JPEG]
        paired = len(candidates) == 2 and len(raw) == len(jpeg) == 1
        members = candidates if paired else [next(item for item in candidates if item['path'] == selected_path)]
        paths = frozenset(item['path'] for item in members)
        if paths in seen:
            continue
        seen.add(paths)
        items, skipped = [], []
        def add(item, label):
            items.append({'path': item['path'], 'relative': source_path(item['path']).name,
                          'label': label, 'bytes': item.get('bytes', 0)})
        for item in members:
            add(item, 'RAW' if item.get('extension') in RAW else '照片')
        if len(candidates) != len(members):
            skipped.extend({'path': item['path'], 'reason': '同名照片不是唯一 RAW＋JPEG 配对，未自动加入'}
                           for item in candidates if item['path'] not in paths)
        for item in videos[key]:
            if len(videos[key]) == 1 and len(candidates) == len(members) and item.get('extension') in PAIRED_VIDEO:
                add(item, '配对视频候选')
            else:
                skipped.append({'path': item['path'], 'reason': '同名视频格式或配对不明确，未自动加入'})
        for item, embedded in sidecars[key]:
            matches = {photo['path'] for photo in candidates if not embedded or photo.get('extension') == embedded}
            if matches and matches <= paths:
                add(item, item['extension'].upper())
            elif matches & paths:
                skipped.append({'path': item['path'], 'reason': '附属文件对应多张同名照片，未自动加入'})
        result.append({'title': source_path(selected_path).stem, 'photos': sorted(paths), 'items': items,
                       'counts': {label: sum(item['label'] == label for item in items)
                                  for label in ('照片', 'RAW', '配对视频候选', 'XMP', 'AAE')},
                       'skipped': sorted(skipped, key=lambda item: item['path'])})
    return result


def photo_bundle_selection(document, ids, current, media):
    selected = {media.records[key]['path'] for key in ids if key in media.records}
    if len(selected) != len(ids) or any(media.records[key]['kind'] != '照片' for key in ids):
        raise ValueError('整组复制请只勾选照片，配套文件将在预览中展开')
    if document.get('restored_snapshot'):
        raise ValueError('恢复的快照不能操作原文件，请重新扫描')
    manifests = photo_manifests(document, selected)
    actions = BundleActions(media, document, {'xmp', 'aae'}, '照片附件')
    targets, skipped = {}, []
    for group in manifests:
        parents = set()
        for path in selected & set(group['photos']):
            item = current.get(media_id(path))
            if not item or item['state'] != 'include' or not item['selectable']:
                raise ValueError('勾选照片须先核对并纳入分类计划：' + path)
            suggested, original = PurePosixPath(item['suggested_path']), source_path(path)
            if suggested.stem != original.stem or suggested.suffix.casefold() != original.suffix.casefold():
                raise ValueError('照片整组复制须保留原文件名以保持配对；请恢复文件名或关闭同名后缀建议：' + path)
            parents.add(str(PurePosixPath(item['suggested_path']).parent))
        if len(parents) != 1:
            raise ValueError('同一 RAW＋JPEG 配对的已勾选照片须使用同一分类目录')
        parent, = parents
        for item in group['items']:
            key = media_id(item['path'])
            companion = current.get(key) if key not in ids else None
            if companion and companion['state'] == 'hold':
                raise ValueError('配套媒体已暂缓，请先核对：' + item['path'])
            if companion and (companion['target_edited'] or companion['state'] == 'include'):
                target, original = PurePosixPath(companion['suggested_path']), source_path(item['path'])
                if str(target.parent) != parent or target.stem != original.stem or target.suffix.casefold() != original.suffix.casefold():
                    raise ValueError('配套媒体已有另一分类位置，请先统一计划：' + item['path'])
            record, _ = actions.validate(key)
            if record.get('source_signature') is None:
                raise ValueError('照片整组复制需要本次扫描的文件身份记录，请重新扫描')
            targets[key] = str(PurePosixPath(parent) / item['relative'])
        skipped.extend(group['skipped'])
    if len(targets) > 200:
        raise ValueError('整组展开后超过 200 项，请分组处理')
    canonical = []
    for target in targets.values():
        if _target_error(target):
            raise ValueError('照片或配套文件名称不满足双平台规则：' + target)
        value = _canonical_target(target)
        if any(value == old or value.startswith(old + '/') or old.startswith(value + '/') for old in canonical):
            raise ValueError('整组目标存在重名或文件与目录冲突：' + target)
        canonical.append(value)
    skipped = list({item['path']: item for item in skipped if media_id(item['path']) not in targets}.values())
    return actions, targets, set(targets) - set(ids), skipped, manifests
