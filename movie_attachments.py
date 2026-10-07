"""Report-only attachment inventory, shared by viewing and confirmed copies."""
from collections import defaultdict
import unicodedata

from movie_grouping import source_path

SUBTITLES = set('srt ass ssa sub idx vtt'.split())
LABELS = {'subtitle': '字幕', 'nfo': 'NFO', 'cover': '封面', 'still': '剧照'}


def normalized(value):
    return unicodedata.normalize('NFC', value).casefold()


def cover_candidates(groups, records):
    photos, folder_groups = defaultdict(list), defaultdict(set)
    for record in records:
        if record.get('kind') == '照片':
            path = source_path(record['path'])
            photos[(str(path.parent), normalized(path.stem))].append(record)
    for number, group in enumerate(groups):
        for file in group.get('files', []):
            folder_groups[str(source_path(file['path']).parent)].add(number)
    extension_order = {name: number for number, name in enumerate(('jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'tif', 'tiff'))}
    result = []
    for number, group in enumerate(groups):
        candidates, seen = [], set()
        folders = dict.fromkeys(str(source_path(file['path']).parent) for file in group.get('files', []))
        stems = [group['title']] if group.get('type') == '编号' else []
        stems += [source_path(file['path']).stem for file in group.get('files', [])]
        stems += [stem + suffix for stem in list(stems) for suffix in ('-poster', '-cover')]
        for folder in folders:
            names = stems + (['poster', 'folder', 'cover', '封面'] if folder_groups[folder] == {number} else [])
            for stem in names:
                for photo in sorted(photos.get((folder, normalized(stem)), []), key=lambda item: (extension_order.get(item.get('extension'), 99), item['path'])):
                    if photo['path'] not in seen:
                        candidates.append(photo)
                        seen.add(photo['path'])
        result.append(candidates)
    return result


def attachment_manifests(document):
    groups = document.get('video_library', {}).get('groups', [])
    records = document.get('files', [])
    covers = cover_candidates(groups, records)
    owners, art, sidecars = defaultdict(set), defaultdict(list), defaultdict(list)
    for number, group in enumerate(groups):
        for file in group.get('files', []):
            owners[str(source_path(file['path']).parent)].add(number)
    for record in records:
        if record.get('kind') == '照片':
            path = source_path(record['path'])
            owner = path.parent.parent if path.parent.name.casefold() == 'extrafanart' else path.parent
            art[str(owner)].append(record)
    for item in document.get('sidecars', []):
        if item.get('extension') in SUBTITLES | {'nfo'}:
            sidecars[str(source_path(item['path']).parent)].append(item)
    result = []
    for number, group in enumerate(groups):
        paths = {file['path'] for file in group.get('files', [])}
        folders = dict.fromkeys(str(source_path(path).parent) for path in sorted(paths))
        items, skipped = {}, {}
        def add(record, kind, relative=None):
            path = source_path(record['path'])
            expected = record.get('source_signature')
            items.setdefault(record['path'], {'path': record['path'], 'kind': kind, 'label': LABELS[kind],
                'relative': relative or path.name, 'bytes': record.get('bytes', 0),
                'identity_available': isinstance(expected, list) and len(expected) == 5 and all(type(value) is int for value in expected)})
        for folder in folders:
            for item in sidecars[folder]:
                matches = set(item.get('media_paths', []))
                if item.get('status') == '已关联' and matches and matches <= paths:
                    add(item, 'nfo' if item['extension'] == 'nfo' else 'subtitle')
                elif matches & paths or item.get('status') != '已关联':
                    skipped[item['path']] = {'path': item['path'], 'reason': '附件未唯一关联到本组，未加入复制'}
        for item in covers[number]:
            add(item, 'cover')
        for folder in folders:
            for record in art[folder]:
                path = source_path(record['path'])
                still = path.parent.name.casefold() == 'extrafanart' or path.stem.casefold() in {'fanart', 'backdrop'} or path.stem.casefold().endswith('-fanart')
                if still and owners[folder] == {number}:
                    add(record, 'still', 'extrafanart/' + path.name if path.parent.name.casefold() == 'extrafanart' else path.name)
                elif record['path'] not in items and (still or path.stem.casefold() in {'poster', 'folder', 'cover', '封面'} or path.stem.casefold().endswith(('-poster', '-cover'))):
                    skipped[record['path']] = {'path': record['path'], 'reason': '疑似封面或剧照未明确关联到本组，未加入复制'}
        ordered = sorted(items.values(), key=lambda item: (list(LABELS).index(item['kind']), item['path']))
        counts = {kind: sum(item['kind'] == kind for item in ordered) for kind in LABELS}
        result.append({'group_key': group.get('group_key', group.get('tag_key', '')), 'title': group.get('title', '影片'),
            'edition': group.get('edition', ''), 'videos': len(paths), 'items': ordered, 'counts': counts,
            'skipped': sorted(skipped.values(), key=lambda item: item['path']),
            'missing': [LABELS[kind] for kind, count in counts.items() if not count],
            'identity_missing': sum(not item['identity_available'] for item in ordered)})
    return result


def attach_summaries(document):
    manifests = attachment_manifests(document)
    for group, summary in zip(document.get('video_library', {}).get('groups', []), manifests):
        group['attachment_summary'] = summary
    return document
