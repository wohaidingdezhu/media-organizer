"""Cache reuse across selected folders with generated temporary media."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import imageio_ffmpeg
from PIL import Image
import media_scan
from scan_cache import ScanCache


class MultiRootScanCacheTests(unittest.TestCase):
    def test_broken_or_incompatible_reports_do_not_discard_other_folder_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            a, b, output = root / 'a', root / 'b', root / 'reports'
            a.mkdir(); b.mkdir(); output.mkdir()
            paths = [a / 'one.jpg', b / 'two.jpg']
            for path in paths:
                path.write_bytes(b'generated image')
            for name, folder, path in [('scan-01', a, paths[0]), ('scan-02', b, paths[1])]:
                directory = output / name
                directory.mkdir()
                (directory / 'report.html').write_text('generated', encoding='utf-8')
                document = {'roots': [str(folder)], 'files': [{'path': str(path), 'kind': '照片',
                            'source_signature': list(media_scan.signature(path.stat())),
                            'image': {'width': 10, 'height': 10, 'dhash': '0123456789abcdef', 'low_detail': False}}],
                            'previews': {}, 'options': {'analysis_fingerprint': 'same'}}
                (directory / 'report.json').write_text(json.dumps(document), encoding='utf-8')
            broken = output / 'scan-zzzz'
            broken.mkdir()
            (broken / 'report.json').write_text('{incomplete', encoding='utf-8')
            (broken / 'report.html').write_text('generated', encoding='utf-8')
            cache = ScanCache(output, [a, b], 'same')
            self.assertEqual(set(cache.files), set(map(str, paths)))
            self.assertEqual(set(cache.reports), {'scan-01', 'scan-02'})
            for path in paths:
                record = {'path': str(path), '_signature': media_scan.signature(path.stat())}
                self.assertIsNotNone(cache.metadata(record))
            paths[1].write_bytes(b'changed generated image')
            record = {'path': str(paths[1]), '_signature': media_scan.signature(paths[1].stat())}
            self.assertIsNone(cache.metadata(record))
            self.assertFalse(ScanCache(output, [a, b], 'different').files)

    def test_separate_scans_reuse_each_sources_own_metadata_and_previews(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            a, b, output = root / '照片', root / '视频', root / 'reports'
            a.mkdir(); b.mkdir()
            photo_a, copy_a, photo_b, movie = a / 'a.png', a / 'a-copy.png', b / 'b.png', b / 'sample.mp4'
            Image.new('RGB', (48, 32), 'red').save(photo_a)
            copy_a.write_bytes(photo_a.read_bytes())
            Image.new('RGB', (48, 32), 'blue').save(photo_b)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'color=c=blue:s=48x32:d=1', '-c:v', 'mpeg4', str(movie)],
                           check=True, capture_output=True)
            originals = {path: path.read_bytes() for path in (photo_a, copy_a, photo_b, movie)}
            def scan(*folders):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(media_scan.main([*map(str, folders), '--output', str(output)]), 0)
                return json.loads((max(output.glob('scan-*')) / 'report.json').read_text(encoding='utf-8'))
            scan(a)
            scan(b)
            with mock.patch.object(media_scan, 'full_hash', wraps=media_scan.full_hash) as full:
                combined = scan(a, b)
            self.assertGreaterEqual(full.call_count, 2)
            self.assertTrue(any(set(group['paths']) == {str(photo_a), str(copy_a)} for group in combined['duplicates']))
            reuse = combined['scan_reuse']
            self.assertEqual(reuse['image_metadata'], 3)
            self.assertEqual(reuse['image_previews'], 3)
            self.assertEqual(reuse['video_metadata'], 1)
            self.assertEqual(reuse['video_covers'], 1)
            self.assertEqual(len(reuse['previous_reports']), 2)
            self.assertEqual(originals, {path: path.read_bytes() for path in originals})
            Image.new('RGB', (49, 32), 'green').save(photo_a)
            updated = scan(a, b)
            self.assertEqual(updated['scan_reuse']['image_metadata'], 2)
            self.assertEqual(updated['scan_reuse']['video_metadata'], 1)


if __name__ == '__main__':
    unittest.main()
