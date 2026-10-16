# coding: utf-8
"""Browse only successful recorded copies; open only unchanged media copies."""
from media_actions import checked_stat, file_signature, media_id
from movie_grouping import source_path


def catalog(operations):
    history = operations._history()
    if history['warnings']:
        raise ValueError('操作记录无法完整读取，请先核对记录')
    values = {}
    records = {record['path']: record for record in operations.media.records.values()}
    for job in history['jobs']:
        if job['mode'] != 'copy':
            continue
        for item in job['items']:
            if item['status'] != 'success':
                continue
            target = source_path(item['target'])
            identifier = media_id(job['id']+'\n'+item['path']+'\n'+item['target'])
            record = records.get(item['path'], {})
            signature = item.get('target_signature')
            can_open = (record.get('kind') in {'照片', '视频'} and isinstance(signature, list)
                        and len(signature) == 5 and all(type(number) is int for number in signature))
            if str(target) in values:
                continue
            destination = source_path(job.get('destination') or str(target.parent))
            try:
                relative = target.parent.relative_to(destination)
                result_parts = [str(destination), *relative.parts]
            except ValueError:
                result_parts = [str(target.parent)]
            values[str(target)] = {'id': identifier, 'job_id': job['id'], 'path': item['path'], 'name': target.name, 'target': str(target),
                                   'folder': str(target.parent), 'kind': record.get('kind', item.get('role', '附件')),
                                   'result_parts': result_parts, 'bytes': item.get('bytes', 0), 'can_open': can_open, '_signature': signature}
    return sorted(values.values(), key=lambda item: (item['folder'], item['name']))


def perform(operations, identifier, action):
    if action not in {'open', 'reveal'} or not isinstance(identifier, str):
        raise ValueError('仅支持查看副本或定位副本文件夹')
    item = next((value for value in catalog(operations) if value['id'] == identifier), None)
    if item is None or not item['can_open']:
        raise ValueError('副本缺少身份记录，不能直接打开，请重新扫描目标目录')
    if file_signature(checked_stat(item['target'])) != item['_signature']:
        raise ValueError('副本已变化或被替换，请重新扫描目标目录')
    from system_integration import open_path
    open_path(item['target'], reveal=action == 'reveal')
    return {'opened': True}
