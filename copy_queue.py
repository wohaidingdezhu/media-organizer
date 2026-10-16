# coding: utf-8
"""One confirmed manifest, durable <=200-file batches and one held report lock."""
import json
import re
import secrets

MAX_QUEUE = 10000


def prepare(operations, job, preview):
    job.pop('planned_items', None)
    job.update(queue=True, batch_ids=[])
    batches = []
    for items in preview['_batches']:
        child = {'id': secrets.token_hex(12), 'queue_parent': job['id'], 'mode': 'copy', 'quick': True,
                 'bundle': False, 'bundle_kind': None, 'created_at': job['created_at'], 'status': 'queued',
                 'items': [], 'total': len(items), 'planned_items': [
                     {key: item[key] for key in ('path', 'target', 'bytes', 'role')} for item in items]}
        operations._save_job(child)
        job['batch_ids'].append(child['id'])
        batches.append((child, {**preview, 'items': items}))
    return batches


def expand(operations, job):
    if (job.get('queue') is not True or job.get('mode') != 'copy' or job.get('quick') is not True
            or job.get('bundle') or job.get('status') not in {'running', 'complete', 'stopped', 'cancelled'}
            or type(job.get('total')) is not int or not 201 <= job['total'] <= MAX_QUEUE
            or not isinstance(job.get('batch_ids'), list) or not 2 <= len(job['batch_ids']) <= MAX_QUEUE
            or len(set(job['batch_ids'])) != len(job['batch_ids'])
            or any(not isinstance(key, str) or not re.fullmatch('[0-9a-f]{24}', key) for key in job['batch_ids'])):
        raise ValueError('分批任务记录无效')
    result = json.loads(json.dumps(job))
    result['items'], result['planned_items'] = [], []
    done = 0
    for key in job['batch_ids']:
        child = operations._read_job(key, allow_queue=False)
        if (child.get('queue_parent') != job['id'] or child.get('queue') or not child.get('planned_items')
                or child.get('mode') != 'copy' or child.get('quick') is not True or child.get('bundle')):
            raise ValueError('分批任务与子批次不一致')
        result['items'].extend(child['items'])
        result['planned_items'].extend(child['planned_items'])
        done += child['status'] == 'complete'
        if child.get('error'):
            result['error'] = child['error']
    if (len(result['planned_items']) != job['total']
            or len({item['path'] for item in result['planned_items']}) != job['total']
            or job['status'] == 'complete' and done != len(job['batch_ids'])):
        raise ValueError('分批任务清单不完整或重复')
    result.update(batch_count=len(job['batch_ids']), completed_batches=done)
    return result


def run(operations, job, batches, descriptor):
    from file_operations import _ACTIVE_JOBS, _BATCH_LOCK
    try:
        for child, preview in batches:
            if operations._stop_requested(job['id']):
                break
            child['status'] = 'running'
            operations._save_job(child)
            operations._run(child, preview, None, release_batch=False, stop_id=job['id'])
            if child['status'] != 'complete':
                break
        statuses = [child['status'] for child, _ in batches]
        job['status'] = ('complete' if all(status == 'complete' for status in statuses) else
                         'cancelled' if operations._stop_requested(job['id']) and not any(status == 'stopped' for status in statuses) else 'stopped')
    except (OSError, ValueError) as error:
        job.update(status='stopped', error='分批任务已停止，请核对记录：' + str(error))
    finally:
        with operations.lock:
            try:
                operations._save_job(job)
            except (OSError, ValueError) as error:
                job.update(status='stopped', error='分批任务记录未确认：' + str(error))
            finally:
                _ACTIVE_JOBS.pop((operations.report_key, job['id']), None)
                from portable_fs import close
                close(descriptor)
                _BATCH_LOCK.release()
