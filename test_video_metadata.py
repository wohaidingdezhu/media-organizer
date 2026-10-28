"""Generated media only: shared facts, fallbacks, caching and identity checks."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import media_scan as scan
import media_gui
from media_center import center_catalog
from video_metadata import clean_video_metadata, parse_video_metadata


class VideoMetadataTests(unittest.TestCase):
    def test_both_platforms_prepare_only_requested_media_helpers(self):
        import sys
        import media_backend
        for platform in ('darwin', 'win32'):
            for images in (False, True):
                for videos in (False, True):
                    with self.subTest(platform=platform, images=images, videos=videos), \
                            mock.patch.object(sys, 'platform', platform), \
                            mock.patch.object(media_backend, 'helper', side_effect=lambda name: Path(name + '.py')) as helper, \
                            mock.patch.object(scan, 'helper_available', return_value=True):
                        media_gui.compile_helpers(mock.Mock(), images, videos)
                        self.assertEqual([call.args[0] for call in helper.call_args_list],
                                         (['image_probe'] if images or videos else []) + (['video_cover'] if videos else []))

    @unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('imageio_ffmpeg'),
                         'Shared image/FFmpeg dependencies are required; both platform CI jobs install them')
    def test_scan_controls_photo_analysis_and_video_preview_independently(self):
        import imageio_ffmpeg
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output = root / 'generated', root / 'reports'
            source.mkdir()
            photo, video = source / 'photo.jpg', source / 'ABC-123.mp4'
            Image.new('RGB', (32, 24), 'red').save(photo)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'color=c=blue:s=80x60:d=1', '-c:v', 'mpeg4', str(video)],
                           capture_output=True, text=True, encoding='utf-8', check=True, timeout=15)
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
            for image_analysis in (False, True):
                for video_covers in (False, True):
                    with self.subTest(image_analysis=image_analysis, video_covers=video_covers):
                        args = media_gui.scan_arguments([source], output=output, image_analysis=image_analysis,
                                                        video_covers=video_covers, refresh_media=True)
                        self.assertEqual('--no-photo-analysis' in args, not image_analysis)
                        self.assertEqual('--no-video-covers' in args, not video_covers)
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(scan.main(args[1:]), 0)
                        report = sorted(output.glob('scan-*'))[-1]
                        document = json.loads((report / 'report.json').read_text(encoding='utf-8'))
                        self.assertEqual(document['image_inspection']['enabled'], image_analysis)
                        self.assertEqual(document['video_metadata_inspection']['enabled'], video_covers)
                        self.assertEqual(document['options']['video_covers_enabled'], video_covers)
                        catalog = center_catalog(document, {'favorites': {}, 'collections': {}, 'recent': []})
                        self.assertEqual(catalog['preview_policy'], {'images': image_analysis, 'videos': video_covers})
                        facts = next(item for item in document['files'] if item['kind'] == '视频').get('video', {})
                        self.assertEqual(bool(facts), video_covers)
                        self.assertEqual(document['video_library']['frame_count'], int(video_covers))
                        if video_covers:
                            self.assertEqual((facts['width'], facts['height']), (80, 60))
                            self.assertTrue(document['video_library']['groups'][0]['poster'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main([str(source), '--output', str(output), '--no-image-metadata']), 0)
            legacy = json.loads((sorted(output.glob('scan-*'))[-1] / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(legacy['image_inspection']['enabled'])
            self.assertFalse(legacy['video_metadata_inspection']['enabled'])
            self.assertEqual(legacy['video_library']['frame_count'], 0)
            legacy.pop('options')
            catalog = center_catalog(legacy, {'favorites': {}, 'collections': {}, 'recent': []})
            self.assertEqual(catalog['preview_policy'], {'images': False, 'videos': False})
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})

    def test_header_facts_exclude_audio_and_cover_art_and_keep_unknown_duration(self):
        header = '''  Duration: 01:02:03.45, start: 0.000000, bitrate: 100 kb/s
  Stream #0:0: Video: mjpeg, yuv420p, 500x500 (attached pic)
  Stream #0:1: Audio: aac, 48000 Hz, stereo
  Stream #0:2(und): Video: h264 (High), yuv420p, 1920x1080 [SAR 1:1 DAR 16:9], 25 fps
'''
        self.assertEqual(parse_video_metadata(header.encode()), {'duration_seconds': 3723.45, 'width': 1920, 'height': 1080})
        self.assertEqual(parse_video_metadata(header.replace('01:02:03.45', 'N/A')), {'width': 1920, 'height': 1080})
        self.assertEqual(parse_video_metadata('\n'.join(header.splitlines()[:3])), {})
        self.assertEqual(parse_video_metadata(b'Invalid data found when processing input'), {})
        for bad in (None, [], {'duration_seconds': True, 'width': True, 'height': 100},
                    {'duration_seconds': float('nan'), 'width': -1, 'height': 2},
                    {'duration_seconds': float('inf')}, {'duration_seconds': -1}, {'duration_seconds': 10**1000}):
            self.assertEqual(clean_video_metadata(bad), {})

    def test_missing_backend_and_disabled_analysis_do_not_start_worker(self):
        for enabled, available in ((False, True), (True, False)):
            with mock.patch.object(scan, 'helper_available', return_value=available), mock.patch.object(scan, 'ImageProbeWorker') as worker:
                result = scan.inspect_video_metadata([], [], enabled=enabled)
            worker.assert_not_called()
            self.assertEqual(result['inspected'], 0)
            self.assertIn('仍可浏览', result['note'])

    def test_timeout_continues_and_source_changes_are_not_saved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ('one.mp4', 'two.mp4'):
                (root/name).write_bytes(b'Generated video placeholder')
            records, _ = scan.discover([root], root/'report', False, [])
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with mock.patch.object(scan, 'helper_available', return_value=True), mock.patch.object(scan, 'ImageProbeWorker') as worker, contextlib.redirect_stdout(io.StringIO()):
                worker.return_value.request.side_effect = [subprocess.TimeoutExpired('mock', 12), {'duration_seconds': 2, 'width': 80, 'height': 60}]
                issues = []
                result = scan.inspect_video_metadata(records, issues)
                self.assertEqual(result['inspected'], 1)
                self.assertEqual(len(issues), 1)
                worker.return_value.close.assert_called_once()
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
                records[0]['_signature'] = (0,)*len(records[0]['_signature'])
                worker.return_value.request.reset_mock()
                self.assertEqual(scan.inspect_video_metadata([records[0]], [])['inspected'], 0)
                worker.return_value.request.assert_not_called()

    def test_catalog_keeps_each_version_separate_and_validates_old_report_data(self):
        paths = [r'C:\Movies\Movie.mp4', '/Movies/Movie 4K.mkv']
        doc = {'files': [dict(path=path, kind='视频', bytes=1, mtime=1, video=facts)
                         for path, facts in zip(paths, ({'duration_seconds': 120, 'width': 1280, 'height': 720},
                                                       {'duration_seconds': 180, 'width': 3840, 'height': 2160}))],
               'video_library': {'groups': [{'group_key': 'a'*64, 'metadata': {'title': 'Movie'}, 'files': [{'path': p} for p in paths]}]}}
        state = {'favorites': {}, 'labels': {}, 'collections': {}, 'recent': []}
        movie = center_catalog(doc, state)['items'][0]
        self.assertNotIn('duration_seconds', movie['metadata'])
        self.assertEqual([f['video']['duration_seconds'] for f in movie['files']], [120, 180])
        del doc['files'][0]['video']
        doc['files'][1]['video'] = {'duration_seconds': '180', 'width': True, 'height': 2160}
        self.assertEqual([f['video'] for f in center_catalog(doc, state)['items'][0]['files']], [{}, {}])

    @unittest.skipUnless(importlib.util.find_spec('imageio_ffmpeg'), 'Optional FFmpeg missing; both platform CI jobs install the shared dependency')
    def test_real_scan_reads_all_versions_even_with_existing_poster_and_no_frame_budget(self):
        import imageio_ffmpeg
        from media_backend import video_metadata_helper
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); source = root/'generated'; source.mkdir(); output = root/'reports'
            video = source/'旅行 sample.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'color=c=blue:s=80x60:d=1', '-c:v', 'libx264', str(video)],
                           capture_output=True, text=True, encoding='utf-8', check=True, timeout=15)
            from PIL import Image
            Image.new('RGB', (16, 24), 'red').save(source/'旅行 sample-poster.jpg')
            (source/'旅行 sample.nfo').write_text('<movie><title>Generated movie</title></movie>', encoding='utf-8')
            original = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
            def run(*extra):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(scan.main([str(source), '--output', str(output), '--max-video-covers', '0', *extra]), 0)
                latest = sorted(output.glob('scan-*'))[-1]
                return json.loads((latest/'report.json').read_text(encoding='utf-8'))
            first = run()
            facts = next(f['video'] for f in first['files'] if f['kind'] == '视频')
            self.assertEqual((facts['width'], facts['height']), (80, 60))
            self.assertAlmostEqual(facts['duration_seconds'], 1, delta=.1)
            self.assertEqual(first['video_library']['frame_count'], 0)
            self.assertTrue(first['video_library']['groups'][0]['poster'])
            original_request = scan.ImageProbeWorker.request
            def no_video_decode(worker, *args, **kwargs):
                if worker.helper == video_metadata_helper():
                    self.fail('Unchanged video should reuse metadata')
                return original_request(worker, *args, **kwargs)
            with mock.patch.object(scan.ImageProbeWorker, 'request', no_video_decode):
                second = run()
            self.assertEqual(second['scan_reuse']['video_metadata'], 1)
            third = run('--refresh-media')
            self.assertEqual(third['scan_reuse']['video_metadata'], 0)
            disabled = run('--no-video-covers')
            self.assertFalse(disabled['video_metadata_inspection']['enabled'])
            self.assertFalse(any(f.get('video') for f in disabled['files']))
            self.assertEqual(original, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})
