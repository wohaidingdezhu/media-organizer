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
                                'ids': [item['id'] for item in username['items']], 'revision': username['revision']})
        proposal = {'ids': [item['id'] for item in username['items']], 'revision': username['revision'],
                    'photo_layout': 'month', 'resolve_names': True}
        user_plan = users.plan(plan, proposal)
        users.plan(plan, {**proposal, 'plan_revision': user_plan['revision'], 'targets_digest':user_plan['targets_digest']}, apply=True)
        assert user_plan['items'][0]['after'].startswith('用户/样例用户/电影/')
        assert all(item['after'].startswith('用户/样例用户/照片/') and item['date_source']
                   for item in user_plan['items'][1:])
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
                assert b'function createWallUsers' in page and b'@@WALL_USERS@@' not in page
            with urlopen(library_url, timeout=10) as response:
                page = response.read()
                assert b'function createWallUsers' in page and b'movieUsers.matches' in page
            with urlopen(library_url.rsplit('/', 1)[0] + '/users.html', timeout=10) as response:
                page = response.read()
                assert b'function filteredUserItems' in page and b'@@MEDIA_USERS@@' not in page
                assert b'photo-layout' in page and b'targets_digest' in page
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
        # A separate generated photo album exercises the identical frozen Mac /
        # Windows bundle engine, including actual Pillow and FFmpeg media.
        photo_source, photo_output, photo_destination = [base / name for name in ('照片配对样例', 'photo-reports', '照片副本')]
        photo_source.mkdir()
        photo_destination.mkdir()
        Image.new('RGB', (48, 32), '#289571').save(photo_source / 'IMG.JPG')
        (photo_source / 'IMG.MOV').write_bytes(video.read_bytes())
        # Synthetic RAW bytes verify preservation without requiring a RAW decoder.
        (photo_source / 'IMG.DNG').write_bytes(b'generated RAW preservation sample')
        (photo_source / 'IMG.xmp').write_text('<xmp>generated edits</xmp>', encoding='utf-8')
        (photo_source / 'IMG.AAE').write_text('<plist>generated edits</plist>', encoding='utf-8')
        photo_before = {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                        for path in photo_source.iterdir()}
        assert media_scan.main([str(photo_source), '--output', str(photo_output)]) == 0
        photo_report = next(photo_output.glob('scan-*/report.json'))
        photo_document = json.loads(photo_report.read_text(encoding='utf-8'))
        photo_plan = OrganizationPlan(photo_report.parent, photo_document)
        photo_key = media_id(str(photo_source / 'IMG.JPG'))
        photo_plan.set_states([photo_key], 'include')
        photo_ops = FileOperations(photo_report.parent, MediaActions(photo_document), lambda: photo_plan, document=photo_document)
        photo_preview = photo_ops.preview('copy', [photo_key], str(photo_destination), photo_bundle=True)
        assert len(photo_preview['items']) == 5 and photo_preview['bundle_kind'] == 'photo'
        photo_job = photo_ops.start(photo_preview['token'])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            photo_result = photo_ops.snapshot(photo_job['id'])
            if photo_result['status'] != 'running':
                break
            time.sleep(.02)
        assert photo_result['status'] == 'complete', photo_result
        assert all(hashlib.sha256(Path(item['target']).read_bytes()).hexdigest() == photo_before[item['path']][0]
                   for item in photo_preview['items'])
        assert photo_before == {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                                for path in photo_source.iterdir()}
        photo_viewer, photo_url = create_library_server(photo_report.parent, photo_output)
        threading.Thread(target=photo_viewer.serve_forever, daemon=True).start()
        try:
            prefix = photo_url.rsplit('/', 1)[0] + '/'
            with urlopen(prefix + 'api/photo-bundles/' + photo_key, timeout=10) as response:
                audit = json.load(response)
                assert len(audit['items']) == 5 and len(audit['photo_ids']) == 2
                assert {item['path'] for item in audit['items']} == {item['path'] for item in photo_preview['items']}
                assert not audit['identity_missing'] and not audit['restored_snapshot']
            with urlopen(prefix + 'photos.html', timeout=10) as response:
                page = response.read()
                assert b'function openPhotoBundle' in page and b'photo-bundle-dialog' in page
        finally:
            photo_viewer.shutdown()
            photo_viewer.server_close()
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
        print('PACKAGED_SMOKE_OK: scan, reuse, FFmpeg frame cache, grouping, attachment audit, batch classification, shared photo interface, film and photo bundle copies, NFO, poster, still, catalog, backup, restore, HTTP, originals unchanged', flush=True)
