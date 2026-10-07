"""Cross-platform integration using only generated, temporary sample files."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import media_scan
import media_gui
import media_backend
import portable_fs as fs
from media_actions import checked_stat, file_signature, MediaActions, media_id
from organization_plan import _target_error
from test_media_scan import sample_png
from test_library_server import request, MemoryServer
import library_server


class CrossPlatformTests(unittest.TestCase):
    def test_both_platforms_choose_shared_workers_when_installed(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), mock.patch.object(sys, 'platform', platform), \
                 mock.patch.object(media_backend, 'portable_available', return_value=True):
                for name, filename in (('image_probe', 'portable_image_probe.py'),
                                       ('video_cover', 'portable_video_cover.py')):
                    helper = media_backend.helper(name)
                    self.assertEqual(helper.name, filename)
                    self.assertEqual(media_backend.command(helper)[:3], [sys.executable, '-X', 'utf8'])
                log = mock.Mock()
                with mock.patch.object(media_gui.subprocess, 'run') as launch:
                    media_gui.compile_helpers(log)
                launch.assert_not_called()
                self.assertEqual(log.call_count, 2)

    def test_unavailable_shared_dependencies_keep_readonly_scanning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'; source.mkdir()
            sample_png(source / 'one.png', 'readonly')
            (source / 'one.mp4').write_bytes(b'generated')
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}
            with mock.patch.object(media_scan, 'helper_available', return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(media_scan.main([str(source), '--output', str(root / 'reports')]), 0)
            report = next((root / 'reports').glob('scan-*'))
            document = json.loads((report / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(document['summary']['files'], 2)
            self.assertEqual(document['previews'], {})
            self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()})

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Shared image dependencies not installed; CI installs them')
    def test_shared_image_fingerprint_matches_reference_on_each_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'reference.png'
            sample_png(path, 'parity')
            worker = media_scan.ImageProbeWorker(media_backend.helper('image_probe'))
            try:
                result = worker.request(str(path))
            finally:
                worker.close()
            self.assertEqual((result['width'], result['height']), (32, 32))
            self.assertEqual(result['dhash'], '54b5ad6a54b5a94a')
            self.assertFalse(result['low_detail'])

    @unittest.skipUnless(sys.platform == 'darwin', 'Mac native fallback; Windows uses shared workers')
    def test_native_image_fallback_still_decodes_generated_photos(self):
        native = media_scan.BASE / 'native' / 'image_probe'
        if not media_scan.helper_available(native):
            self.skipTest('Native fallback not compiled; macOS CI compiles it')
        with mock.patch.object(media_backend, 'portable_available', return_value=False):
            self.assertEqual(media_backend.helper('image_probe'), native)
        with tempfile.TemporaryDirectory() as temporary:
            worker = media_scan.ImageProbeWorker(native)
            try:
                for number in range(20):
                    source = Path(temporary).resolve() / f'{number}.png'
                    sample_png(source, str(number))
                    self.assertEqual(worker.request(str(source))['width'], 32)
            finally:
                worker.close()

    def test_scan_launcher_forwards_cli_options_without_accessing_media(self):
        if sys.platform == 'win32':
            command = ['cmd.exe', '/d', '/c', str(media_scan.BASE / '开始扫描.bat'), '--help']
        elif sys.platform == 'darwin':
            command = ['/bin/zsh', str(media_scan.BASE / '开始扫描.command'), '--help']
        else:
            self.skipTest('Desktop launchers target macOS and Windows')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                input='\n', timeout=15, env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--video-rule', result.stdout, result.stderr)
        result = subprocess.run([*command[:-1], '--media-invalid-option'], capture_output=True,
                                text=True, encoding='utf-8', input='\n', timeout=15,
                                env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('--media-invalid-option', result.stderr)

    def test_lock_reopen_keeps_header_with_a_substituted_desktop_platform(self):
        from file_operations import open_directory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = open_directory(root)
            try:
                # Desktop actions are substituted in shared tests. Filesystem
                # handles must keep using the real host's OS adapter.
                with mock.patch.object(sys, 'platform', 'darwin' if os.name == 'nt' else 'win32'):
                    descriptor = fs.open_lock('.generated-lock', directory)
                    os.write(descriptor, b'generated job header')
                    fs.close(descriptor)
                    descriptor = fs.open_lock('.generated-lock', directory)
                    try:
                        self.assertEqual(os.read(descriptor, 64), b'generated job header')
                    finally:
                        fs.close(descriptor)
            finally:
                fs.close(directory)

    def test_single_character_targets_publish_without_overwriting(self):
        from file_operations import open_directory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = open_directory(root)
            try:
                for name in ('a', '图', '🙂'):
                    with self.subTest(name=name):
                        source = root / ('temporary-' + name)
                        source.write_bytes(b'first copy')
                        fs.publish(source.name, name, directory)
                        self.assertEqual((root / name).read_bytes(), b'first copy')
                        # POSIX publishes a hard link; Windows renames the file.
                        # Match copy_one's cleanup before creating another copy.
                        try:
                            fs.unlink(source.name, dir_fd=directory)
                        except FileNotFoundError:
                            pass
                        source.write_bytes(b'second copy')
                        with self.assertRaises(FileExistsError):
                            fs.publish(source.name, name, directory)
                        self.assertEqual((root / name).read_bytes(), b'first copy')
                        self.assertEqual(source.read_bytes(), b'second copy')
                        fs.replace(source.name, name, src_dir_fd=directory, dst_dir_fd=directory)
                        self.assertEqual((root / name).read_bytes(), b'second copy')
            finally:
                fs.close(directory)

    def test_native_handles_match_path_signatures(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / '中文 sample.png'
            for number in range(200):
                path.write_bytes(str(number).encode())
                expected = file_signature(path.stat())
                actual = file_signature(checked_stat(path))
                self.assertEqual(actual, expected, f'{number}: {actual} != {expected}')

    def test_complete_scan_and_report_service_keep_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, reports = root / '中文 来源', root / 'reports'
            source.mkdir()
            sample_png(source / 'IMG_20240101.png', 'A')
            sample_png(source / 'backup.png', 'B')
            (source / 'ABC-123.mp4').write_bytes(b'synthetic video')
            (source / 'copy.mp4').write_bytes(b'synthetic video')
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(media_scan.main([str(source), '--output', str(reports), '--no-video-covers']), 0)
            report = next(reports.glob('scan-*'))
            document = json.loads((report / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(document['summary']['files'], 4)
            self.assertEqual(document['summary']['duplicate_groups'], 1)
            self.assertEqual(len(document['previews']), 2)
            self.assertTrue(all(not _target_error(record['suggested_path']) for record in document['files']))
            with mock.patch.object(library_server, 'ThreadingHTTPServer', MemoryServer):
                server, url = library_server.create_library_server(report, reports)
            prefix = url.split(':43210', 1)[1].removesuffix('library.html')
            for endpoint in ('report.html', 'organize.html', 'api/organization', 'api/photos', 'api/storage'):
                headers, _ = request(server, prefix + endpoint)
                self.assertIn('200 OK', headers, endpoint)
            self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()})

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Optional Pillow backend not installed; CI installs media dependencies on both platforms')
    def test_pillow_orientation_exif_and_source_protection(self):
        from PIL import Image
        from portable_image_probe import probe
        with tempfile.TemporaryDirectory() as temporary:
            path, preview = Path(temporary).resolve() / 'sample.jpg', Path(temporary).resolve() / 'preview.png'
            exif = Image.Exif()
            exif[274] = 6
            exif[36867] = '2020:02:03 04:05:06'
            Image.new('RGB', (3000, 2000), 'red').save(path, exif=exif)
            before = path.read_bytes()
            result = probe(str(path), str(preview))
            self.assertEqual((result['width'], result['height']), (2000, 3000))
            self.assertEqual(result['date_source'], 'exif_original')
            self.assertTrue(result['low_detail'])
            with Image.open(preview) as image:
                self.assertLessEqual(max(image.size), 512)
            with self.assertRaises((ValueError, FileExistsError)):
                probe(str(path), str(path))
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec('imageio_ffmpeg'), 'Optional FFmpeg backend not installed; CI installs media dependencies on both platforms')
    def test_ffmpeg_extracts_only_report_preview(self):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as temporary:
            for extension in ('mp4', 'mkv', 'avi'):
                with self.subTest(extension=extension):
                    video = Path(temporary).resolve() / ('generated.' + extension)
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
                                    '-i', 'color=c=blue:s=80x60:d=1', '-c:v', 'libx264', str(video)], check=True, timeout=15)
                    before = (video.read_bytes(), video.stat().st_mtime_ns)
                    records, _ = media_scan.discover([video.parent], video.parent / 'report', False, [])
                    record = next(item for item in records if item['path'] == str(video))
                    report = video.parent / ('report-' + extension); report.mkdir()
                    groups = [{'poster': '', 'files': [{'path': str(video), 'extension': extension}]}]
                    helper = media_backend.helper('video_cover')
                    self.assertEqual(helper.name, 'portable_video_cover.py')
                    issues = []
                    count = media_scan.export_video_frame_covers(report, groups, [record], helper, issues)
                    self.assertEqual(count, 1, issues)
                    self.assertTrue((report / groups[0]['poster']).read_bytes().startswith(b'\x89PNG'))
                    self.assertEqual(before, (video.read_bytes(), video.stat().st_mtime_ns))

    def test_windows_reserved_names_cannot_enter_plans(self):
        for name in ('CON', 'NUL.jpg', 'COM1.png', 'aux.txt', 'a:b.png', 'trailing.', 'space ', 'LPT¹.mov'):
            self.assertTrue(_target_error('照片/' + name), name)
            self.assertFalse(_target_error('照片/' + media_scan.safe_segment(name)), name)

    @unittest.skipUnless(os.name == 'nt', 'Windows integration')
    def test_windows_junctions_are_rejected_for_scan_copy_and_reports(self):
        from file_operations import open_directory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target, junction = root / 'actual', root / 'junction'
            target.mkdir()
            (target / 'sample.png').write_bytes(b'generated')
            result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                with self.assertRaises((OSError, ValueError)):
                    open_directory(junction)
                with self.assertRaises((OSError, ValueError)):
                    checked_stat(junction / 'sample.png')
                with self.assertRaises((OSError, ValueError)):
                    media_scan.normalize_roots([str(junction)], root / 'reports')
                records, skipped = media_scan.discover([root], root / 'reports', False, [])
                self.assertEqual(len(records), 1)
                self.assertEqual(skipped['符号链接'], 1)
            finally:
                junction.rmdir()  # Removes only the generated junction, never its target.
            self.assertEqual((target / 'sample.png').read_bytes(), b'generated')

    @unittest.skipUnless(os.name == 'nt', 'Windows integration')
    def test_windows_media_actions_use_native_open_without_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / '中文 $(literal) & file.mp4'
            path.write_bytes(b'generated')
            record = {'path': str(path), 'kind': '视频', 'bytes': path.stat().st_size,
                      'mtime': path.stat().st_mtime, 'source_signature': file_signature(path.stat())}
            manager = MediaActions({'roots': [str(path.parent)], 'files': [record]})
            with mock.patch('system_integration.os.startfile') as start, mock.patch('system_integration.subprocess.Popen') as popen:
                manager.perform(media_id(str(path)), 'open')
                start.assert_called_once_with(str(path))
                manager.perform(media_id(str(path)), 'reveal')
                self.assertEqual(popen.call_args.args[0][-2:], ['/select,', str(path)])
                self.assertNotIn('shell', popen.call_args.kwargs)

    @unittest.skipUnless(os.name == 'nt', 'Windows integration')
    def test_windows_recycle_refuses_permanent_delete_and_changed_sources(self):
        import pythoncom
        from win32com.shell import shellcon
        import system_integration
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'generated.png'
            path.write_bytes(b'keep')
            signature = file_signature(path.stat())
            operation = mock.Mock()
            operation.PerformOperations.return_value = 0
            operation.GetAnyOperationsAborted.return_value = False
            def wrap(sink, interface):
                with self.assertRaises(pythoncom.com_error):
                    sink.PreDeleteItem(0, None)
                self.assertEqual(sink.PreDeleteItem(shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE, None), 0)
                path.write_bytes(b'changed')
                with self.assertRaises(pythoncom.com_error):
                    sink.PreDeleteItem(shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE, None)
                sink.newItem = 'synthetic recycle destination'
                return sink
            with mock.patch('pythoncom.CoCreateInstance', return_value=operation), mock.patch('pythoncom.WrapObject', side_effect=wrap), \
                 mock.patch('win32com.shell.shell.SHCreateItemFromParsingName'):
                self.assertEqual(system_integration.recycle_file(str(path), signature)['trashed_path'], 'synthetic recycle destination')
            self.assertTrue(operation.SetOperationFlags.call_args.args[0] & 0x00080000)
            self.assertEqual(path.read_bytes(), b'changed')

    @unittest.skipUnless(os.name == 'nt', 'Windows integration')
    def test_scan_cancellation_uses_a_cooperative_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = media_gui.DashboardState(root / 'reports')
            process = mock.Mock()
            state._signal_scan(process)
            self.assertTrue(state.cancel_file.exists())
            process.send_signal.assert_not_called()
            source = root / 'media'
            source.mkdir()
            (source / 'sample.mp4').write_bytes(b'generated')
            # A wrapper delays discovery so the monitor can observe the marker.
            code = ('import media_scan,time,sys; original=media_scan.discover; '
                    'media_scan.discover=lambda *a,**k:(time.sleep(1),original(*a,**k))[1]; '
                    'sys.exit(media_scan.main(sys.argv[1:]))')
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(source), '--output', str(state.output), '--no-image-metadata'],
                                    env=dict(os.environ, MEDIA_ORGANIZER_CANCEL_FILE=str(state.cancel_file)), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 130, result.stderr)
            self.assertFalse(list(state.output.glob('scan-*')))
            self.assertEqual((source / 'sample.mp4').read_bytes(), b'generated')


if __name__ == '__main__':
    unittest.main()
