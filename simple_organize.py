"""Read-only proposals for the explicitly confirmed simple copy workflow."""
from collections import defaultdict
import hashlib
import datetime as dt
import json
from pathlib import PurePosixPath
import re
import unicodedata

from media_actions import media_id
from movie_grouping import source_path
from movie_attachments import attachment_manifests
from photo_bundle import photo_manifests
from organization_plan import _target_error, _canonical_target

REGIONS = {
    '国产': '国产 中国 中国大陆 中国内地 大陆 内地 中國 中國大陸 香港 中国香港 中國香港 台湾 台灣 中国台湾 中國台灣 澳门 澳門 中国澳门 china cn chn prc mainland-china hong-kong taiwan macao macau'.split(),
    '日韩': '日韩 日本 韩国 韓國 朝鲜 japan jp jpn korea south-korea kr kor'.split(),
    '欧美': '欧美 美国 美國 英国 英國 法国 德国 意大利 西班牙 加拿大 澳大利亚 新西兰 俄罗斯 俄羅斯 瑞典 挪威 丹麦 芬兰 荷兰 比利时 奥地利 瑞士 爱尔兰 葡萄牙 波兰 捷克 匈牙利 希腊 usa us uk france germany italy spain canada australia russia sweden norway denmark finland netherlands belgium austria switzerland ireland portugal poland czechia hungary greece'.split(),
}
ALIASES = {unicodedata.normalize('NFC', value).casefold(): label for label, values in REGIONS.items() for value in values}
ALIASES.update({'united states': '欧美', 'united states of america': '欧美', 'united kingdom': '欧美',
                'south korea': '日韩', 'north korea': '日韩', 'hong kong': '国产',
                **{name+'电影': name for name in REGIONS}, '日本电影': '日韩', '韩国电影': '日韩'})


def movie_region(groups):
    countries = [country for group in groups for country in (group.get('metadata') or {}).get('countries', [])
                 if isinstance(country, str) and country.strip()]
    if countries:
        values = [part.strip().casefold() for country in countries for part in re.split(r'[/,;|、，；&]+', country) if part.strip()]
        labels = {ALIASES.get(unicodedata.normalize('NFC', value), '其他') for value in values}
        return (next(iter(labels)), '本地 NFO 国家 / 地区') if len(labels) == 1 else ('待确认地区', '本地 NFO 地区跨大类，待确认')
    parts = [unicodedata.normalize('NFC', part).casefold() for group in groups for file in group['files']
             for part in source_path(file['path']).parent.parts]
    labels = {ALIASES[part] for part in parts if part in ALIASES}
    return (next(iter(labels)), '明确的原目录名称') if len(labels) == 1 else ('待确认地区', '本地资料与目录没有唯一地区依据')


def propose_simple_copy(document, snapshot, history, corrections=None):
    """Never stat/open media or persist plan changes here."""
    from media_scan import safe_segment
    corrections = corrections or {}
    if document.get('restored_snapshot'):
        raise ValueError('历史快照不能省心整理，请重新扫描本机媒体')
    if history.get('warnings') or any(job['status'] in {'running', 'external_running'} for job in history['jobs']):
        raise ValueError('请等待文件操作完成，或先核对无法读取的操作记录')
    records = {item['path']: item for item in document['files']}
    all_records = {**records, **{item['path']: item for item in document.get('sidecars', [])}}
    plans = {item['path']: item for item in snapshot['items']}
    completed, uncertain = set(), set()
    for job in history['jobs']:
        completed.update(item['path'] for item in job['items'] if item['status'] == 'success')
        uncertain.update(item['path'] for item in job['items'] if item['status'] in {'failed', 'unknown', 'processing'})
        if job.get('error') or job['status'] == 'interrupted':
            uncertain.update(item['path'] for item in job.get('planned_items', []))
    if not isinstance(document.get('video_library', {}), dict):
        raise ValueError('影片分组缺失，请重新扫描')
    groups = document.get('video_library', {}).get('groups', [])
    manifests = attachment_manifests(document)
    art = {item['path'] for summary in manifests for item in summary['items'] + summary['skipped'] if records.get(item['path'], {}).get('kind') == '照片'}
    # A phone's IMG.jpg + IMG.mov is also a same-stem movie-cover candidate.
    # Prefer the photo association unless subtitles/NFO, an identified movie
    # group or explicit poster name provides movie evidence. Camera prefixes
    # are already excluded from numbered film groups by the shared scanner.
    strong_art = {item['path'] for group, summary in zip(groups, manifests)
                  if group.get('type') == '编号' or summary['counts']['nfo'] or summary['counts']['subtitle']
                  for item in summary['items'] if item['kind'] in {'cover', 'still'}}
    for path in list(art - strong_art):
        pure = source_path(path)
        explicit = (pure.parent.name.casefold() == 'extrafanart' or pure.stem.casefold() in
                    {'poster', 'folder', 'cover', '封面', 'fanart', 'backdrop'} or
                    pure.stem.casefold().endswith(('-poster', '-cover', '-fanart')))
        if not explicit:
            art.remove(path)
    photos = {path for path, item in records.items() if item['kind'] == '照片' and path not in art}
    units, skipped, reserved = [], [], set()
    def skip(paths, reason, category='review'):
        skipped.extend({'path': path, 'reason': reason, 'category': category} for path in sorted(set(paths)))
    for group in photo_manifests(document, photos):
        group['items'] = [item for item in group['items'] if not (
            records.get(item['path'], {}).get('kind') == '视频' and
            corrections.get(item['path'], {}).get('category') in {'movie', 'home_video'})]
        paths = [item['path'] for item in group['items']]
        if group['skipped']:
            skip(paths, '照片配套关系有歧义，请通过配套清单手动核对')
            skipped.extend(group['skipped'])
            reserved.update(paths + [item['path'] for item in group['skipped']])
            continue
        anchor = min((records[path] for path in group['photos']), key=lambda item:
                     (item.get('date_source') != 'EXIF 拍摄日期', item.get('extension') not in {'jpg', 'jpeg'}, item['path']))
        match = re.match(r'^照片/([1-9][0-9]{3}/(?:0[1-9]|1[0-2]))/', anchor.get('suggested_path', ''))
        month = match.group(1) if match else '待确认日期'
        fixed = {corrections[path]['month'] for path in group['photos'] if corrections.get(path, {}).get('month')}
        if len(fixed) > 1:
            skip(paths, '照片配套成员的手动年月不同，请先统一修正')
            reserved.update(paths)
            continue
        if fixed:
            month = next(iter(fixed))
        parent = '照片/' + month
        notes = ([] if anchor.get('date_source') == 'EXIF 拍摄日期' else
                 ['照片年月来自文件名或修改时间，非已确认拍摄日期']) if match else ['缺少有效年月，请在待确认日期目录核对']
        if fixed:
            notes = ['年月来自手动分类修正，不改原始日期']
        if group['counts']['配对视频候选']:
            notes.append('同名视频只按文件名关联，不能证明是 Live Photo，请在确认清单核对')
        units.append({'type': 'photo', 'title': group['title'], 'notes': notes,
                      'items': [{**item, 'id': media_id(item['path']), 'target': parent+'/'+item['relative'], 'role': item['label']} for item in group['items']]})
        reserved.update(paths)
    works = defaultdict(list)
    for group, summary in zip(groups, manifests):
        work = group.get('work_key') or group.get('tag_key') or group.get('group_key')
        if not isinstance(work, str) or not work:
            work = media_id(group['files'][0]['path'])
        works[work].append((group, summary))
    for work, entries in works.items():
        videos = [file for group, _ in entries for file in group['files']]
        if any(file['path'] in reserved for file in videos):
            skip([file['path'] for file in videos if file['path'] not in reserved], '影片与照片配套视频范围重叠，请手动核对')
            continue
        region, reason = movie_region([group for group, _ in entries])
        regions = {corrections[file['path']]['region'] for file in videos if corrections.get(file['path'], {}).get('region')}
        categories = {corrections[file['path']]['category'] for file in videos if corrections.get(file['path'], {}).get('category')}
        if len(regions) > 1 or len(categories) > 1:
            skip([file['path'] for file in videos], '同一作品的手动分类不一致，请统一修正')
            continue
        if regions:
            region, reason = next(iter(regions)), '手动确认地区'
        evidence = any(group.get('type') == '编号' or summary['counts']['nfo'] or summary['counts']['subtitle'] for group, summary in entries)
        camera = all(re.match(r'^(?:IMG|DSC|DSCF|DSCN|MOV|VID|PXL|MVI|GOPR|VIDEO)[-_ ]?\d', source_path(file['path']).stem, re.I) for file in videos)
        home = next(iter(categories)) == 'home_video' if categories else camera and not evidence
        if home:
            for file in videos:
                record = records[file['path']]
                month = corrections.get(file['path'], {}).get('month')
                if not month:
                    # Use UTC for mtime fallback so a report has the same target
                    # month on both platforms and after time-zone changes.
                    stamp = re.search(r'(?<!\d)((?:19|20)\d{2})[-_]?(\d{2})[-_]?(\d{2})(?!\d)|(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?=\d{6}(?:\D|$))', source_path(file['path']).stem)
                    try:
                        parts = (stamp.groups()[:3] if stamp.group(1) else stamp.groups()[3:]) if stamp else None
                        month = dt.date(*map(int, parts)).strftime('%Y/%m') if parts else dt.datetime.fromtimestamp(record['mtime'], dt.timezone.utc).strftime('%Y/%m')
                    except (ValueError, OverflowError, OSError, TypeError):
                        month = '待确认日期'
                units.append({'type': 'home_video', 'title': source_path(file['path']).name,
                              'notes': ['家庭视频：根据手动选择或相机文件名识别；年月为手动修正、文件名日期或 UTC 修改时间，非已验证拍摄日期'],
                              'items': [{'path': file['path'], 'id': media_id(file['path']), 'relative': source_path(file['path']).name,
                                         'target': '家庭视频/'+month+'/'+source_path(file['path']).name, 'role': '家庭视频'}]})
            for _, summary in entries:
                skip([item['path'] for item in summary['items'] if item['path'] not in reserved], '家庭视频的疑似影片附件未自动关联，请手动核对')
            continue
        title = entries[0][0].get('title', '影片')
        parent = f'电影/{region}/{safe_segment(title)}-{work[:12] or media_id(videos[0]["path"])[:12]}'
        values = {}
        for file in videos:
            values[file['path']] = {'path': file['path'], 'relative': source_path(file['path']).name, 'role': '影片'}
        for _, summary in entries:
            for item in summary['items']:
                if item['path'] not in reserved:
                    values[item['path']] = {**item, 'role': item['label']}
            skipped.extend(summary['skipped'])
        units.append({'type': 'movie', 'title': title, 'notes': [region+'：'+reason],
                      'items': [{**item, 'id': media_id(item['path']), 'target': parent+'/'+item['relative']} for item in values.values()]})
    # Add a stable folder only where flat year/month names would collide. Names
    # of RAW/JPEG and editing companions remain intact.
    owners = defaultdict(set)
    for index, unit in enumerate(units):
        for item in unit['items']:
            owners[_canonical_target(item['target'])].add(index)
    colliding = set().union(*(indexes for indexes in owners.values() if len(indexes) > 1)) if owners else set()
    for index in colliding:
        unit = units[index]
        if unit['type'] in {'photo', 'home_video'}:
            first = min(item['path'] for item in unit['items'])
            folder = safe_segment(source_path(first).parent.name)[:30]+'-'+media_id(first)[:12]
            for item in unit['items']:
                target = PurePosixPath(item['target'])
                item['target'] = str(target.parent/folder/target.name)
            unit['notes'].append('同名照片使用来源标识子目录区分，保留原文件名')
    # Reject all owners of an overlapping source or cross-unit target. Check
    # directory spellings too: Foo/a and foo/b cannot form two portable folders.
    sources, targets, spellings = defaultdict(set), defaultdict(set), defaultdict(lambda: defaultdict(set))
    for index, unit in enumerate(units):
        for item in unit['items']:
            sources[item['path']].add(index)
            target = PurePosixPath(item['target'])
            targets[_canonical_target(str(target))].add(index)
            for path in (target, *target.parents):
                spellings[_canonical_target(str(path))][str(path)].add(index)
    conflicts = set()
    for indexes in list(sources.values()) + list(targets.values()):
        if len(indexes) > 1:
            conflicts.update(indexes)
    for variants in spellings.values():
        if len(variants) > 1:
            conflicts.update(set().union(*variants.values()))
    for target, indexes in targets.items():
        for parent in PurePosixPath(target).parents:
            if str(parent) in targets:
                conflicts.update(indexes | targets[str(parent)])
    proposed, claimed = [], set()
    for index, unit in sorted(enumerate(units), key=lambda pair: min(item['path'] for item in pair[1]['items'])):
        paths = [item['path'] for item in unit['items']]
        reason = None
        category = 'review'
        if index in conflicts:
            reason = '分组存在来源重叠、目标重名或大小写冲突，请手动核对'
        elif any(path in uncertain for path in paths):
            reason = '曾失败或结果未确认，请先手动核对；不会自动重试'
        elif any(path in completed for path in paths):
            if all(path in completed for path in paths):
                reason = '本组全部文件已有成功处理记录，本次不重复处理；当前文件状态需重新扫描确认'
                category = 'handled'
            else:
                reason = '本组仅部分文件有成功处理记录，请逐项核对；不会自动补复制'
        elif any(plans.get(path, {}).get('state') == 'hold' for path in paths):
            reason = '本组媒体已暂缓'
            category = 'held'
        elif any(plans.get(path, {}).get('target_edited') for path in paths):
            reason = '本组已有手动分类位置，请按原计划手动核对'
        elif len(unit['items']) > 200:
            reason = '本组展开超过 200 项，请分组手动核对'
        elif any(path not in all_records or not isinstance(all_records[path].get('source_signature'), list)
                 or len(all_records[path]['source_signature']) != 5
                 or any(type(value) is not int for value in all_records[path]['source_signature']) for path in paths):
            reason = '本组缺少扫描身份记录，请重新扫描'
        elif any(_target_error(item['target']) for item in unit['items']):
            reason = '本组原文件名不满足双平台规则，请手动核对'
        canonical = [_canonical_target(item['target']) for item in unit['items']]
        if len(set(canonical)) != len(canonical) or any(value.startswith(other+'/') for value in canonical for other in canonical if value != other):
            reason = '本组存在目标重名或文件与目录冲突，请手动核对'
            category = 'review'
        if reason:
            skip(paths, reason, category)
        else:
            for item in unit['items']:
                item['bytes'] = all_records[item['path']]['bytes']
            proposed.append(unit)
        claimed.update(paths)
    skip(set(all_records)-claimed-reserved, '未明确关联的附件或媒体，留待手动核对')
    skipped = list({item['path']: {'category': 'review', **item} for item in skipped}.values())
    digest = hashlib.sha256(json.dumps({'units': proposed, 'skipped': skipped, 'revision': snapshot['revision']},
                                      sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {'units': proposed, 'skipped': skipped, 'digest': digest}
