# coding: utf-8
"""Installed-backend checks using only generated temporary media."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from urllib.request import urlopen


def run():
    from PIL import Image
    import imageio_ffmpeg
    import media_scan
    import media_gui
    from library_backup import export_backup, validate_backup, restore_backup
    from library_index import catalog
    from media_actions import MediaActions
    from movie_grouping import update_assignments
    from organization_plan import OrganizationPlan
    from media_actions import media_id
    from file_operations import FileOperations
    import time
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        source, output = base / '样例媒体', base / 'reports'
        source.mkdir()
        (source / 'extrafanart').mkdir()
        Image.new('RGB', (64, 48), '#218265').save(source / 'poster.png')
        Image.new('RGB', (96, 64), '#af5921').save(source / 'extrafanart' / 'scene.png')
        video = source / 'sample.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:d=1',
                        '-c:v', 'mpeg4', str(video)], check=True, capture_output=True)
        (source / 'sample.nfo').write_text('<movie><title>样例电影</title><year>2026</year><plot>离线影片资料。</plot><actor><name>样例演员</name></actor></movie>', encoding='utf-8')
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob('*') if path.is_file()}
        if media_scan.main([str(source), '--output', str(output)]) != 0:
            raise AssertionError('Packaged scan failed')
        report = next(output.glob('scan-*/report.json'))
        document = json.loads(report.read_text(encoding='utf-8'))
        group, = document['video_library']['groups']
        assert group['metadata']['title'] == '样例电影'
        assert group['poster'] and group['stills']
        update_assignments(output, document, [{'id': media_id(str(video)), 'work_key': group['work_key'],
            'title': group['title'], 'edition': '本机版本', 'part': 1}])
        assert media_scan.main([str(source), '--output', str(output)]) == 0
        report = max(output.glob('scan-*/report.json'))
        document = json.loads(report.read_text(encoding='utf-8'))
        assert document['scan_reuse']['image_metadata'] == 2
        assert document['video_library']['groups'][0]['edition'] == '本机版本'
        destination = base / '副本'
        destination.mkdir()
        plan = OrganizationPlan(report.parent, document)
        from media_users import UserCatalog
        users = UserCatalog(output, lambda: document)
        username = users.update({'action': 'create', 'name': '样例用户', 'revision': users.snapshot()['revision']})
        movie = next(item for item in username['items'] if item['kind'] == '电影')
        username = users.update({'action': 'assign', 'user_id': username['users'][0]['id'],
                                'ids': [movie['id']], 'revision': username['revision']})
        proposal = {'ids': [movie['id']], 'revision': username['revision']}
        user_plan = users.plan(plan, proposal)
        users.plan(plan, {**proposal, 'plan_revision': user_plan['revision']}, apply=True)
        assert user_plan['items'][0]['after'].startswith('用户/样例用户/电影/')
        classification = plan.preview_folder([media_id(str(video))], '视频/本机样例')
        assert classification['changed_count'] == 1
        adjusted = plan.apply_folder([media_id(str(video))], classification['folder'], classification['revision'])
        assert next(item for item in adjusted['items'] if item['path'] == str(video))['state'] == 'pending'
        plan.set_states([media_id(str(video))], 'include')
        operations = FileOperations(report.parent, MediaActions(document), lambda: plan, document=document)
        preview = operations.preview('copy', [media_id(str(video))], str(destination), bundle=True)
        assert len(preview['items']) == 4
        job = operations.start(preview['token'])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = operations.snapshot(job['id'])
            if result['status'] != 'running':
                break
            time.sleep(.02)
        assert result['status'] == 'complete', result
        assert all(Path(item['target']).read_bytes() == Path(item['path']).read_bytes() for item in preview['items'])
        assert preview['bundles'][0]['counts'] == {'subtitle': 0, 'nfo': 1, 'cover': 1, 'still': 1}
        assert result['bundle'] and {item['role'] for item in result['planned_items']} == {'影片', 'NFO', '封面', '剧照'}
        from library_server import create_library_server
        viewer, library_url = create_library_server(report.parent, output)
        threading.Thread(target=viewer.serve_forever, daemon=True).start()
        try:
            with urlopen(library_url.rsplit('/', 1)[0] + '/photos.html', timeout=10) as response:
                page = response.read()
                assert b'function sortManagementItems' in page and b'@@MANAGEMENT' not in page
                assert '批量调整分类目录'.encode('utf-8') in page
            with urlopen(library_url.rsplit('/', 1)[0] + '/users.html', timeout=10) as response:
                page = response.read()
                assert b'function filteredUserItems' in page and b'@@MEDIA_USERS@@' not in page
        finally:
            viewer.shutdown()
            viewer.server_close()
        # A second generated library has no local poster, exercising actual FFmpeg
        # extraction and its cached PNG inside the frozen application as well.
        frame_source, frame_output = base / '截帧样例', base / 'frame-reports'
        frame_source.mkdir()
        frame_video = frame_source / 'frame.mp4'
        frame_video.write_bytes(video.read_bytes())
        frame_before = hashlib.sha256(frame_video.read_bytes()).hexdigest()
        for number in range(2):
            assert media_scan.main([str(frame_source), '--output', str(frame_output)]) == 0
            frame_report = max(frame_output.glob('scan-*/report.json'))
            frame_document = json.loads(frame_report.read_text(encoding='utf-8'))
            assert frame_document['video_library']['frame_count'] == 1
            assert frame_document['scan_reuse']['video_covers'] == number
        assert hashlib.sha256(frame_video.read_bytes()).hexdigest() == frame_before
        assert len(catalog(output)['items']) == 3
        files, _ = validate_backup(export_backup(output))
        restored = restore_backup(output, files)
        restored_document = json.loads((restored / report.parent.name / 'report.json').read_text(encoding='utf-8'))
        assert not MediaActions(restored_document).records
        assert len(catalog(restored)['items']) == 3
        server, state, url = media_gui.create_dashboard_server(output)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(url + 'api/status', timeout=10) as response:
                assert 'monitor' in json.load(response)
            with urlopen(url, timeout=10) as response:
                assert '长期资料库'.encode('utf-8') in response.read()
        finally:
            server.shutdown()
            state.close()
            server.server_close()
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob('*') if path.is_file()}
        assert before == after
        print('PACKAGED_SMOKE_OK: scan, reuse, FFmpeg frame cache, grouping, attachment audit, batch classification, shared photo interface, bundle copy, NFO, poster, still, catalog, backup, restore, HTTP, originals unchanged', flush=True)
