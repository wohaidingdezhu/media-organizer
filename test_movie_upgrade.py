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
        self.assertEqual(sum(item['role'] == '影片附件' for item in preview['items']), 4)
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
        report, _ = self.run_scan()
        with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
            server, url = library_server.create_library_server(report, self.output)
        prefix = urlsplit(url).path.removesuffix('library.html')
        edit = {'id': media_id(str(movie)), 'work_key': 'new', 'title': '人工修正作品', 'edition': '导演剪辑', 'part': 1}
        headers, _ = request(server, prefix + 'api/grouping', 'POST', {'edits': [edit]}, origin='http://outside.invalid')
        self.assertIn('403', headers)
        headers, _ = request(server, prefix + 'api/grouping', 'POST', {'edits': [edit]})
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
