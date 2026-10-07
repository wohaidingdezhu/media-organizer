"""Film bundles, fresh hash verification and grouping; generated sources only."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

import media_scan as scan
import movie_grouping as grouping
import library_server
import library_backup
from file_operations import FileOperations
from media_actions import MediaActions, media_id
from organization_plan import OrganizationPlan
from test_report_history import MemoryServer, request
from test_support import make_symlink


class MovieUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source, self.output, self.destination = [self.base / name for name in ('source', 'reports', 'destination')]
        self.source.mkdir()
        self.destination.mkdir()

    def generated(self, name, body=b'generated media'):
        path = self.source / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
        return path

    def run_scan(self, images=False, *extra):
        args = [str(self.source), '--output', str(self.output), *extra]
        if not images:
            args.append('--no-image-metadata')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scan.main(args), 0)
        report = max(self.output.glob('scan-*'))
        return report, json.loads((report / 'report.json').read_text(encoding='utf-8'))

    def operations(self, document, report):
        media = MediaActions(document)
        plan = OrganizationPlan(report, document)
        plan.set_states([media_id(file['path']) for file in document['files'] if file['kind'] == '视频'], 'include')
        return FileOperations(report, media, lambda: plan, document=lambda: document), plan

    def wait_job(self, ops, job):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = ops.snapshot(job['id'])
            if result['status'] != 'running':
                return result
            time.sleep(.01)
        self.fail('generated copy batch did not finish')

    def bundle_sample(self):
        movies = [self.generated(f'ABC-123.1080p.CD{part}.mp4', bytes([part]) * (30 + part)) for part in (1, 2)]
        subtitle = self.generated('ABC-123.1080p.CD1.zh-CN.srt', b'1\n00:00:00,000 --> 00:00:01,000\nsample')
        self.generated('ABC-123.1080p.CD1.nfo', b'<movie><title>Generated</title></movie>')
        self.generated('poster.png', b'generated cover bytes')
        self.generated('extrafanart/still.png', b'generated still bytes')
        self.generated('orphan.srt', b'not uniquely associated')
        report, document = self.run_scan()
        ops, plan = self.operations(document, report)
        return movies, subtitle, report, document, ops, plan

    def test_bundle_expands_parts_and_attachments_and_preserves_every_original(self):
        movies, _, _, _, ops, _ = self.bundle_sample()
        originals = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.source.rglob('*') if path.is_file()}
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        self.assertEqual(len(preview['items']), 6)
        self.assertEqual(sum(item['role'] in {'字幕', 'NFO', '封面', '剧照'} for item in preview['items']), 4)
        self.assertEqual([Path(item['path']).name for item in preview['skipped']], ['orphan.srt'])
        self.assertFalse(list(self.destination.iterdir()), 'preview must not create directories')
        result = self.wait_job(ops, ops.start(preview['token']))
        self.assertEqual(result['status'], 'complete', result)
        for item in preview['items']:
            self.assertEqual(Path(item['target']).read_bytes(), originals[item['path']][0])
        for path, before in originals.items():
            self.assertEqual((Path(path).read_bytes(), Path(path).stat().st_mtime_ns), before)

    def test_bundle_refuses_attachment_change_after_preview(self):
        movies, subtitle, _, _, ops, _ = self.bundle_sample()
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        subtitle.write_bytes(b'replaced generated subtitle')
        with self.assertRaisesRegex(ValueError, '附件.*变化'):
            ops.start(preview['token'])
        self.assertFalse(list(self.destination.iterdir()))

    def test_bundle_rejects_old_attachment_identities_and_never_expands_trash(self):
        movies, _, _, document, ops, _ = self.bundle_sample()
        document['sidecars'][0].pop('source_signature')
        with self.assertRaises(ValueError):
            ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        with self.assertRaisesRegex(ValueError, '只支持复制'):
            ops.preview('trash', [media_id(str(movies[0]))], bundle=True)

    def test_bundle_preserves_uppercase_extension_and_refuses_renamed_video(self):
        movie = self.generated('ABC-123.MP4')
        self.generated('ABC-123.MP4.en.srt')
        report, document = self.run_scan()
        ops, plan = self.operations(document, report)
        preview = ops.preview('copy', [media_id(str(movie))], str(self.destination), bundle=True)
        self.assertTrue(preview['items'][0]['relative'].endswith('ABC-123.MP4'))
        plan.set_target(media_id(str(movie)), '视频/编号/ABC-123/renamed.mp4')
        plan.set_states([media_id(str(movie))], 'include')
        with self.assertRaisesRegex(ValueError, '保持字幕关联'):
            ops.preview('copy', [media_id(str(movie))], str(self.destination), bundle=True)

    def test_bundle_refuses_plan_and_grouping_changes_and_unnamed_parts(self):
        movies, _, _, document, ops, plan = self.bundle_sample()
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        plan.set_target(media_id(str(movies[1])), 'other/part2.mp4')
        with self.assertRaises(ValueError):
            ops.start(preview['token'])
        plan.set_target(media_id(str(movies[1])), '视频/编号/ABC-123/ABC-123.1080p.CD2.mp4')
        plan.set_states([media_id(str(movies[1]))], 'include')
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        document['video_library']['groups'][0]['files'].pop()
        with self.assertRaisesRegex(ValueError, '已变化'):
            ops.start(preview['token'])

    def test_bundle_target_collision_and_source_symlink_are_refused(self):
        movies, subtitle, _, _, ops, _ = self.bundle_sample()
        target = self.destination / '视频' / '编号' / 'ABC-123'
        target.mkdir(parents=True)
        collision = target / subtitle.name.upper()
        collision.write_bytes(b'existing target stays')
        with self.assertRaisesRegex(ValueError, '不会覆盖'):
            ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        collision.unlink()
        subtitle.unlink()
        make_symlink(subtitle, movies[0])
        with self.assertRaises((ValueError, OSError)):
            ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)

    def test_ambiguous_subtitle_and_shared_generic_cover_are_not_copied(self):
        first = self.generated('ABC-123.mp4')
        self.generated('ABC-123.mkv', b'different generated bytes')
        self.generated('ABC-123.srt')
        self.generated('OTHER-456.mp4', b'other generated work')
        self.generated('poster.png')
        report, document = self.run_scan()
        ops, _ = self.operations(document, report)
        preview = ops.preview('copy', [media_id(str(first))], str(self.destination), bundle=True)
        self.assertEqual(len(preview['items']), 2)
        self.assertTrue(any(Path(item['path']).name == 'ABC-123.srt' for item in preview['skipped']))

    def test_automatic_versions_parts_and_foreign_paths(self):
        for name in ('ABC-123.1080p.CD2.mp4', 'ABC-123.1080p.CD1.mp4', 'ABC-123.4K.CD1.mp4', 'ABC-123-C.mp4'):
            self.generated(name)
        _, document = self.run_scan()
        groups = document['video_library']['groups']
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({group['work_key'] for group in groups}), 1)
        self.assertEqual([file['part'] for file in next(group for group in groups if group['edition'] == '1080p')['files']], [1, 2])
        self.assertNotEqual(len(document['duplicates']), 0, 'versions must not suppress byte duplicates')
        for path in ('C:\\电影\\ABC-123.1080p.CD2.mp4', '\\\\server\\share\\ABC-123.1080p.CD2.mp4', '/电影/ABC-123.1080p.CD2.mp4'):
            self.assertEqual(grouping.clues(grouping.source_path(path).stem), ('1080p', 2))

    def test_manual_split_merge_reset_persist_after_rescan_and_backup(self):
        movies = [self.generated(name) for name in ('ABC-123.CD1.mp4', 'ABC-123.CD2.mp4', 'OTHER-456.mp4')]
        report, document = self.run_scan()
        group = next(group for group in document['video_library']['groups'] if group['title'] == 'ABC-123')
        edits = [{'id': media_id(str(movie)), 'work_key': group['work_key'], 'title': group['title'], 'edition': '人工版本', 'part': index}
                 for index, movie in enumerate(movies, 1)]
        values = grouping.update_assignments(self.output, document, edits)
        self.assertEqual(len(values), 3)
        _, rescanned = self.run_scan()
        self.assertEqual(len(rescanned['video_library']['groups']), 1)
        self.assertEqual(rescanned['video_library']['groups'][0]['edition'], '人工版本')
        body = library_backup.export_backup(self.output)
        files, _ = library_backup.validate_backup(body)
        self.assertIn('library-grouping.json', files)
        grouping.update_assignments(self.output, rescanned, [{'id': media_id(str(movies[2])), 'reset': True}])
        _, reset = self.run_scan()
        self.assertEqual(len(reset['video_library']['groups']), 2)
        split = grouping.update_assignments(self.output, reset, [dict(edits[1], edition='不同版本')])
        self.assertEqual(split[media_id(str(movies[1]))]['edition'], '不同版本')
        _, separated = self.run_scan()
        self.assertEqual(len(separated['video_library']['groups']), 3)

    def test_new_work_unknown_ids_and_corrupted_assignments(self):
        movie = self.generated('ABC-123.mp4')
        _, document = self.run_scan()
        edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': '我的影片', 'edition': '', 'part': 0}
        grouping.update_assignments(self.output, document, [edit])
        _, updated = self.run_scan()
        self.assertEqual(updated['video_library']['groups'][0]['title'], '我的影片')
        for invalid in (dict(edit, id='f' * 64), dict(edit, part=True), dict(edit, work_key='bad'), dict(edit, title='')):
            with self.assertRaises(ValueError):
                grouping.update_assignments(self.output, document, [invalid])
        before = (self.output / grouping.NAME).read_bytes()
        with self.assertRaises(ValueError):
            grouping.update_assignments(self.output, document, [edit, edit])
        self.assertEqual((self.output / grouping.NAME).read_bytes(), before)
        (self.output / grouping.NAME).write_bytes(b'broken generated config')
        with self.assertRaises(ValueError):
            grouping.load_assignments(self.output)

    def test_grouping_warns_missing_and_duplicate_parts(self):
        for name in ('ABC-123.CD2.mp4', 'ABC-123.PART2.mp4'):
            self.generated(name)
        _, document = self.run_scan()
        group = document['video_library']['groups'][0]
        self.assertEqual(len(group['grouping_warnings']), 2)
        self.assertTrue(group['needs_review'])

    def test_http_grouping_requires_origin_and_changes_actual_served_page(self):
        movie = self.generated('ABC-123.mp4')
        report, document = self.run_scan()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': '人工修正作品', 'edition': '导演剪辑', 'part': 1}
        headers, _ = request(server, prefix + 'api/grouping', 'POST', {'edits': [edit]}, origin='http://outside.invalid')
        self.assertIn('403', headers)
        headers, _ = request(server, prefix + 'api/grouping', 'POST', {'edits': [edit], 'revision': document['video_library']['grouping_revision']})
        self.assertIn('200', headers)
        headers, page = request(server, prefix + 'library.html')
        self.assertIn('200', headers)
        self.assertIn('人工修正作品'.encode(), page)
        self.assertIn('导演剪辑'.encode(), page)
        headers, body = request(server, prefix + 'api/tags')
        key = grouping.load_assignments(self.output)[media_id(str(movie))]['work_key']
        self.assertIn(key, json.loads(body)['groups'])

    def make_pngs(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow absent locally; both-platform CI installs the shared media dependencies')
        for name in ('generated.png', 'copy.png'):
            Image.new('RGB', (40, 30), 'blue').save(self.source / name)

    def test_incremental_scan_reuses_decoding_and_still_hashes_all_candidates(self):
        self.make_pngs()
        self.run_scan(True, '--no-video-covers')
        with mock.patch.object(scan, 'full_hash', wraps=scan.full_hash) as hashing, mock.patch.object(scan.ImageProbeWorker, 'request', side_effect=AssertionError('unchanged PNG must reuse decoding')):
            _, document = self.run_scan(True, '--no-video-covers')
        self.assertEqual(hashing.call_count, 2)
        self.assertEqual(document['scan_reuse']['image_metadata'], 2)
        self.assertEqual(document['scan_reuse']['image_previews'], 2)
        self.assertEqual(len(document['duplicates']), 1)

    def test_incremental_changed_file_force_refresh_and_damaged_preview_fallback(self):
        self.make_pngs()
        old, _ = self.run_scan(True, '--no-video-covers')
        for path in (old / 'previews').glob('*.png'):
            path.write_bytes(b'damaged generated cached preview')
        original = scan.ImageProbeWorker.request
        with mock.patch.object(scan.ImageProbeWorker, 'request', autospec=True, side_effect=original) as worker:
            _, document = self.run_scan(True, '--no-video-covers')
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(document['scan_reuse']['image_metadata'], 2)
        from PIL import Image
        Image.new('RGB', (44, 32), 'red').save(self.source / 'generated.png')
        _, changed = self.run_scan(True, '--no-video-covers')
        self.assertEqual(changed['scan_reuse']['image_metadata'], 1)
        _, forced = self.run_scan(True, '--no-video-covers', '--refresh-media')
        self.assertFalse(forced['scan_reuse']['enabled'])
        self.assertEqual(forced['scan_reuse']['image_metadata'], 0)

    def test_cache_rejects_restored_snapshots_backend_changes_and_source_links(self):
        self.make_pngs()
        report, document = self.run_scan(True, '--no-video-covers')
        from scan_cache import ScanCache
        fingerprint = document['options']['analysis_fingerprint']
        self.assertIsNone(ScanCache(self.output, [self.source], 'different-decoder').report)
        document['restored_snapshot'] = True
        (report / 'report.json').write_text(json.dumps(document), encoding='utf-8')
        self.assertIsNone(ScanCache(self.output, [self.source], fingerprint).report)
        document.pop('restored_snapshot')
        (report / 'report.json').write_text(json.dumps(document), encoding='utf-8')
        cache = ScanCache(self.output, [self.source], fingerprint)
        record = document['files'][0]
        record['_signature'] = tuple(record['source_signature'])
        path = Path(record['path']);path.unlink();make_symlink(path, self.source/'copy.png' if path.name != 'copy.png' else self.source/'generated.png')
        self.assertIsNone(cache.metadata(record))
        self.assertFalse(cache.thumbnail(record, self.destination/'unsafe.png'))
        self.assertFalse((self.destination/'unsafe.png').exists())

    def test_video_frame_cache_uses_current_source_identity(self):
        movie = self.generated('ABC-123.mp4')
        self.make_pngs()
        from PIL import Image
        def generated_frame(worker, path, thumbnail=None, **options):
            if thumbnail:
                Image.new('RGB', (32, 24), 'green').save(thumbnail)
            return {'path': path, 'width': 32, 'height': 24, 'dhash': '0123456789abcdef', 'low_detail': False}
        with mock.patch.object(scan.ImageProbeWorker, 'request', autospec=True, side_effect=generated_frame):
            self.run_scan(True)
        with mock.patch.object(scan.ImageProbeWorker, 'request', side_effect=AssertionError('unchanged frame must reuse decoding')):
            _, document = self.run_scan(True)
        self.assertEqual(document['scan_reuse']['video_covers'], 1)
        movie.write_bytes(b'changed generated video')
        with mock.patch.object(scan.ImageProbeWorker, 'request', autospec=True, side_effect=generated_frame) as worker:
            _, document = self.run_scan(True)
        self.assertEqual(document['scan_reuse']['video_covers'], 0)
        self.assertEqual(worker.call_count, 1)

    def test_shared_javascript_is_valid_and_searches_manual_edition(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Both-platform CI installs Node for actual shared interface tests')
        for name in ('management.js', 'library_details.js'):
            result = subprocess.run([node, '--check', str(Path(__file__).parent / name)], capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
        code = "const b=require(process.argv[1]);const g={title:'影片',edition:'导演剪辑',files:[]};if(b.browseMovies([g],{query:'导演'}).length!==1)process.exit(1);"
        result = subprocess.run([node, '-e', code, str(Path(__file__).parent / 'library_browse.js')], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_attachment_audit_matches_copy_preview_and_does_not_flag_other_work(self):
        movies, _, report, document, _, _ = self.bundle_sample()
        self.generated('OTHER-456.mp4', b'another movie')
        other = self.generated('OTHER-456.en.srt', b'other subtitle')
        report, document = self.run_scan()
        group = next(item for item in document['video_library']['groups'] if item['title'] == 'ABC-123')
        summary = group['attachment_summary']
        self.assertEqual(summary['counts'], {'subtitle': 1, 'nfo': 1, 'cover': 0, 'still': 0})
        self.assertEqual(summary['missing'], ['封面', '剧照'])
        self.assertNotIn(str(other), [item['path'] for item in summary['skipped']])
        ops, _ = self.operations(document, report)
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        self.assertEqual(preview['bundles'], [summary])
        self.assertEqual({item['path'] for item in preview['items'] if item['role'] != '影片'}, {item['path'] for item in summary['items']})
        self.assertFalse(list(self.destination.iterdir()))

    def test_attachment_audit_windows_unc_and_posix_paths_use_same_rules(self):
        from movie_attachments import attachment_manifests
        for folder in ('C:\\电影', '\\\\server\\share\\电影', '/电影'):
            path = grouping.source_path(folder)
            video, cover, subtitle = [str(path / name) for name in ('ABC-123.mp4', 'ABC-123-poster.png', 'ABC-123.en.srt')]
            document = {'files': [{'path': cover, 'kind': '照片', 'extension': 'png', 'bytes': 1}],
                'video_library': {'groups': [{'title': 'ABC-123', 'type': '编号', 'tag_key': 'a'*64, 'files': [{'path': video}]}]},
                'sidecars': [{'path': subtitle, 'extension': 'srt', 'status': '已关联', 'media_paths': [video], 'bytes': 1}]}
            summary, = attachment_manifests(document)
            self.assertEqual(summary['counts'], {'subtitle': 1, 'nfo': 0, 'cover': 1, 'still': 0})
            self.assertEqual(summary['identity_missing'], 2)
            self.assertEqual({item['relative'] for item in summary['items']}, {'ABC-123.en.srt', 'ABC-123-poster.png'})

    def test_grouping_stale_windows_and_missing_revision_never_overwrite_saved_data(self):
        movie = self.generated('ABC-123.mp4')
        report, document = self.run_scan()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            first, url = library_server.create_library_server(report, self.output)
            second, second_url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        second_prefix = urlsplit(second_url).path.removesuffix('library.html')
        edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': '第一窗口作品', 'edition': '1080p', 'part': 1}
        body = {'edits': [edit], 'revision': document['video_library']['grouping_revision']}
        headers, _ = request(first, prefix + 'api/grouping', 'POST', body)
        self.assertIn('200', headers)
        before = (self.output / grouping.NAME).read_bytes()
        body['edits'] = [dict(edit, title='旧窗口作品')]
        headers, error = request(second, second_prefix + 'api/grouping', 'POST', body)
        self.assertIn('400', headers)
        self.assertIn('另一窗口', json.loads(error)['error'])
        headers, _ = request(second, second_prefix + 'api/grouping', 'POST', {'edits': body['edits']})
        self.assertIn('400', headers)
        self.assertEqual((self.output / grouping.NAME).read_bytes(), before)
        headers, page = request(second, second_prefix + 'library.html')
        self.assertIn(grouping.assignments_revision(grouping.load_assignments(self.output)).encode(), page)
        self.assertIn('第一窗口作品'.encode(), page)

    def test_grouping_revision_is_checked_under_lock_and_reset_changes_revision(self):
        movie = self.generated('ABC-123.mp4')
        _, document = self.run_scan()
        revision = document['video_library']['grouping_revision']
        edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': '新作品', 'edition': '', 'part': 0}
        values = grouping.update_assignments(self.output, document, [edit], revision)
        with self.assertRaisesRegex(ValueError, '另一窗口'):
            grouping.update_assignments(self.output, document, [{'id': edit['id'], 'reset': True}], revision)
        self.assertEqual(grouping.load_assignments(self.output), values)
        reset = grouping.update_assignments(self.output, document, [{'id': edit['id'], 'reset': True}], grouping.assignments_revision(values))
        self.assertEqual(reset, {})
        self.assertEqual(grouping.assignments_revision(reset), revision)

    def test_bundle_stop_during_subtitle_keeps_finished_movies_and_records_pending_roles(self):
        import file_operations as operations
        movies, _, report, document, ops, plan = self.bundle_sample()
        originals = {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        original_copy = operations.copy_one
        def stop_subtitle(media, identifier, destination, relative, signature, identity, progress, cancelled):
            def on_progress(amount, phase):
                progress(amount, phase)
                if relative.endswith('.srt') and amount and phase == 'copying':
                    ops.request_stop(next(iter(ops.jobs)))
            return original_copy(media, identifier, destination, relative, signature, identity, on_progress, cancelled)
        with mock.patch.object(operations, 'copy_one', side_effect=stop_subtitle):
            result = self.wait_job(ops, ops.start(preview['token']))
        self.assertEqual(result['status'], 'cancelled', result)
        self.assertEqual([item['status'] for item in result['items']], ['success', 'success', 'cancelled'])
        self.assertEqual([item['role'] for item in result['planned_items']], ['影片', '影片', '字幕', 'NFO', '封面', '剧照'])
        self.assertEqual(result['items'][-1]['role'], '字幕')
        reopened = FileOperations(report, MediaActions(document), lambda: plan, document=document)
        self.assertEqual(reopened.snapshot(result['id']), result)
        with self.assertRaisesRegex(ValueError, '整组复制'):
            reopened.remaining(result['id'])
        self.assertEqual(len([path for path in self.destination.rglob('*') if path.is_file()]), 2)
        self.assertFalse(list(self.destination.rglob('.media-copy-*')))
        self.assertEqual(originals, {path: Path(path).read_bytes() for path in originals})
        ops.update_plan(lambda: plan.set_states([media_id(str(movies[0]))], 'hold'))

    def test_bundle_failure_keeps_verified_copies_and_never_overwrites_existing_target(self):
        import file_operations as operations
        movies, _, _, _, ops, _ = self.bundle_sample()
        originals = {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        original_copy = operations.copy_one
        def fail_nfo(*args):
            if args[3].endswith('.nfo'):
                raise OSError('generated disk failure')
            return original_copy(*args)
        with mock.patch.object(operations, 'copy_one', side_effect=fail_nfo):
            result = self.wait_job(ops, ops.start(preview['token']))
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual([item['status'] for item in result['items']], ['success']*3+['failed'])
        self.assertEqual(result['items'][-1]['role'], 'NFO')
        self.assertEqual(len([path for path in self.destination.rglob('*') if path.is_file()]), 3)
        with self.assertRaisesRegex(ValueError, '不会覆盖'):
            ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        self.assertEqual(originals, {path: Path(path).read_bytes() for path in originals})

    def test_png_signature_alone_never_reuses_corrupt_or_truncated_cache(self):
        self.make_pngs()
        for corrupt in (lambda body: body[:-12], lambda body: body[:40]+bytes([body[40]^1])+body[41:], lambda body: body+b'junk'):
            old, _ = self.run_scan(True, '--no-video-covers', '--refresh-media')
            for path in (old/'previews').glob('*.png'):
                path.write_bytes(corrupt(path.read_bytes()))
            original = scan.ImageProbeWorker.request
            with mock.patch.object(scan.ImageProbeWorker, 'request', autospec=True, side_effect=original) as worker:
                _, document = self.run_scan(True, '--no-video-covers')
            self.assertEqual(document['scan_reuse']['image_metadata'], 2)
            self.assertEqual(document['scan_reuse']['image_previews'], 0)
            self.assertEqual(worker.call_count, 2)

    def test_cache_source_changes_during_copy_cleanup_and_existing_file_is_preserved(self):
        self.make_pngs()
        _, document = self.run_scan(True, '--no-video-covers')
        from scan_cache import ScanCache
        cache = ScanCache(self.output, [self.source], document['options']['analysis_fingerprint'])
        record = document['files'][0]
        record['_signature'] = tuple(record['source_signature'])
        target = self.destination/'preview.png'
        with mock.patch.object(cache, 'unchanged', side_effect=[True, False]):
            self.assertFalse(cache.thumbnail(record, target))
        self.assertFalse(target.exists())
        target.write_bytes(b'keep existing generated file')
        self.assertFalse(cache.thumbnail(record, target))
        self.assertEqual(target.read_bytes(), b'keep existing generated file')
        self.assertEqual(cache.reused_previews, 0)

    def test_attachment_and_stopped_bundle_interface_display_actual_summary_and_roles(self):
        node = shutil.which('node')
        code = r"""
const fs=require('fs'),vm=require('vm');
class Element{constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];}append(...children){this.children.push(...children);}setAttribute(){} }
function make(tag,cls,text){return new Element(tag,text||'');}
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
const scope={make,operating:false,saving:false,fileSize:n=>String(n)};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const summary={videos:2,counts:{subtitle:1,nfo:0,cover:1,still:0},missing:['NFO','剧照'],identity_missing:0,items:[{label:'字幕',path:'C:\\sample\\film.srt'}],skipped:[{path:'orphan.srt',reason:'未唯一关联'}]};
const shown=text(scope.attachmentSummary(summary));
if(!shown.includes('字幕 1')||!shown.includes('缺少不表示影片损坏')||!shown.includes('人工核对'))throw Error(shown);
const management=fs.readFileSync(process.argv[2],'utf8');vm.runInContext(management.slice(management.indexOf('function activeOperation'),management.indexOf('async function reviewRemaining')),scope);
const card=scope.operationCard({mode:'copy',bundle:true,status:'cancelled',items:[{path:'film.mp4',target:'out/film.mp4',status:'success',role:'影片'}],total:2,planned_items:[{path:'film.mp4',target:'out/film.mp4',role:'影片'},{path:'film.srt',target:'out/film.srt',role:'字幕'}]});
const rendered=text(card);if(!rendered.includes('[字幕]')||!rendered.includes('整组未全部完成')||rendered.includes('核对此批次未处理项'))throw Error(rendered);
"""
        result = subprocess.run([node, '-e', code, str(Path(__file__).parent/'library_details.js'), str(Path(__file__).parent/'management.js')], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_simultaneous_grouping_saves_allow_one_writer_and_preserve_the_winner(self):
        import threading
        movie = self.generated('ABC-123.mp4')
        _, document = self.run_scan()
        barrier = threading.Barrier(2)
        saved, errors = [], []
        def save(title):
            edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': title, 'edition': '', 'part': 0}
            barrier.wait(timeout=5)
            try:
                saved.append(grouping.update_assignments(self.output, document, [edit], document['video_library']['grouping_revision']))
            except ValueError as error:
                errors.append(str(error))
        threads = [threading.Thread(target=save, args=(title,)) for title in ('第一窗口', '第二窗口')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('另一窗口', errors[0])
        self.assertEqual(grouping.load_assignments(self.output), saved[0])

    def test_bundle_journal_rejects_invalid_type_markers_and_keeps_legacy_readable(self):
        import file_operations as operations
        movies, _, report, document, ops, plan = self.bundle_sample()
        preview = ops.preview('copy', [media_id(str(movies[0]))], str(self.destination), bundle=True)
        queued = []
        with mock.patch.object(operations.threading.Thread, 'start', autospec=True, side_effect=queued.append):
            job = ops.start(preview['token'])
        try:
            ops.request_stop(job['id'])
        finally:
            queued[0].run()
        valid = ops.snapshot(job['id'])
        journal = report/'operations'/(job['id']+'.json')
        reopened = FileOperations(report, MediaActions(document), lambda: plan, document=document)
        for change in ('role', 'bundle'):
            malformed = json.loads(json.dumps(valid))
            if change == 'role':
                malformed['planned_items'][0]['role'] = ['invalid role']
            else:
                malformed['bundle'] = 'true'
            journal.write_text(json.dumps(malformed), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '操作记录'):
                reopened.snapshot(job['id'])
        legacy = json.loads(json.dumps(valid))
        legacy.pop('bundle')
        for item in legacy['planned_items']:
            item.pop('role')
        journal.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(reopened.snapshot(job['id']), legacy)
        self.assertFalse(list(self.destination.iterdir()))
