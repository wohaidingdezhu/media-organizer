"""Shared, opt-in progress protocol; percentages describe one measured phase."""
import json
import os

PREFIX = '@@MEDIA_PROGRESS@@'
LABELS = {'prepare': '准备扫描', 'discover': '清点文件', 'hash': '校验精确重复',
          'hash_file': '完整校验当前文件', 'photos': '读取照片信息', 'videos': '读取视频信息',
          'similar': '比较相似照片', 'metadata': '整理本地影片资料', 'previews': '生成照片预览',
          'posters': '读取本地封面', 'frames': '生成视频封面', 'export': '保存资料库',
          'index': '更新媒体索引', 'done': '扫描完成'}


def progress(stage, completed=0, total=None, current=''):
    if (stage not in LABELS or type(completed) is not int or completed < 0
            or total is not None and (type(total) is not int or total < completed or total < 0)
            or not isinstance(current, str)):
        raise ValueError('扫描进度无效')
    return {'stage': stage, 'label': LABELS[stage], 'completed': completed, 'total': total,
            'percent': round(completed * 100 / total, 1) if total else None, 'current': current[:200]}


def emit(stage, completed=0, total=None, current=''):
    if os.environ.get('MEDIA_ORGANIZER_PROGRESS') == '1':
        print(PREFIX + json.dumps(progress(stage, completed, total, current), ensure_ascii=True), flush=True)


def parse(line):
    if not line.startswith(PREFIX) or len(line) > 4096:
        return None
    try:
        value = json.loads(line[len(PREFIX):])
        return progress(value['stage'], value['completed'], value['total'], value.get('current', ''))
    except (ValueError, KeyError, TypeError):
        return None
