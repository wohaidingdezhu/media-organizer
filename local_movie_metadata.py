# coding: utf-8
"""Bounded offline NFO text and scanned local still previews; no URL fetching."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import portable_fs as fs

MAX_NFO = 1024 * 1024


def parse_nfo(body):
    if len(body) > MAX_NFO:
        raise ValueError('NFO 超过 1 MB')
    text = body.decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('NFO 不允许 DTD 或外部实体')
    root = ET.fromstring(text)
    if root.tag not in {'movie', 'episodedetails'} or sum(1 for _ in root.iter()) > 2000:
        raise ValueError('NFO 类型不支持或字段过多')
    def clean(value, maximum=500):
        return ''.join(char for char in str(value or '') if char >= ' ' or char in '\n\t').strip()[:maximum]
    def values(name):
        return list(dict.fromkeys(clean(item.text) for item in root.findall(name) if clean(item.text)))[:50]
    return {'title': clean(root.findtext('title')), 'year': clean(root.findtext('year'), 4),
            'plot': clean(root.findtext('plot'), 6000), 'actors': values('actor/name'),
            'genres': values('genre'), 'tags': values('tag'), 'directors': values('director'),
            'studio': clean(root.findtext('studio')), 'source': '本地 NFO'}


def enrich_library(groups, sidecars, issues):
    import media_scan as scan
    for group in groups:
        paths = {file['path'] for file in group['files']}
        candidates = [item for item in sidecars if item['extension'] == 'nfo' and item['status'] == '已关联'
                      and set(item['media_paths']) & paths]
        group['metadata'] = {}
        if len(candidates) > 1:
            group['needs_review'] = True
            scan.issue(issues, candidates[0]['path'], '多个本地 NFO 对应同一影片组，未自动选择资料')
            continue
        if not candidates:
            continue
        item, = candidates
        path = Path(item['path'])
        try:
            before = fs.stat(path, follow_symlinks=False)
            if scan.signature(before) != item.get('_signature'):
                raise ValueError('NFO 自扫描开始后变化')
            body = fs.read_private_file(path.parent, (path.name,), MAX_NFO)
            if scan.signature(fs.stat(path, follow_symlinks=False)) != item['_signature']:
                raise ValueError('NFO 读取过程中变化')
            group['metadata'] = parse_nfo(body)
            group['metadata']['path'] = str(path)
        except (OSError, ValueError, UnicodeError, ET.ParseError) as error:
            group['needs_review'] = True
            scan.issue(issues, path, '本地影片资料未读取：' + str(error))


def export_stills(directory, groups, records, previews, helper, issues, enabled=True, limit=200):
    import media_scan as scan
    folder_groups = {}
    for number, group in enumerate(groups):
        group['stills'] = []
        for file in group['files']:
            folder_groups.setdefault(Path(file['path']).parent, set()).add(number)
    if not enabled or not scan.helper_available(helper):
        return 0
    count, worker = 0, None
    try:
        for number, group in enumerate(groups):
            candidates = []
            for record in records:
                if record['kind'] != '照片':
                    continue
                path = Path(record['path'])
                parent = path.parent.parent if path.parent.name.casefold() == 'extrafanart' else path.parent
                if folder_groups.get(parent) == {number} and (path.parent.name.casefold() == 'extrafanart' or path.stem.casefold() in {'fanart', 'backdrop'} or path.stem.casefold().endswith('-fanart')):
                    candidates.append(record)
            for record in sorted(candidates, key=lambda item: item['path'])[:8]:
                if count >= limit:
                    return count
                relative = previews.get(record['path'])
                if not relative:
                    try:
                        if scan.signature(fs.stat(record['path'], follow_symlinks=False)) != record['_signature']:
                            raise ValueError('剧照已变化')
                        if worker is None:
                            worker = scan.ImageProbeWorker(helper)
                            (directory / 'stills').mkdir(mode=0o700)
                        target = directory / 'stills' / f'still-{count:05d}.png'
                        worker.request(record['path'], thumbnail=str(target))
                        if scan.signature(fs.stat(record['path'], follow_symlinks=False)) != record['_signature'] or not target.is_file():
                            target.unlink(missing_ok=True)
                            raise ValueError('剧照导出时变化')
                        relative = 'stills/' + target.name
                    except (OSError, ValueError) as error:
                        scan.issue(issues, record['path'], '剧照预览未生成：' + str(error))
                        continue
                group['stills'].append(relative)
                count += 1
        return count
    finally:
        if worker is not None:
            worker.close()
