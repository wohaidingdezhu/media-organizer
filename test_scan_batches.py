"""Large scan tasks stay bounded and cancellable; all media is generated."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image
import media_scan


class ScanBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        for index in range(10):
            (self.source / f'video-{index}.mp4').write_bytes(b'generated video ' + str(index).encode())
        Image.new('RGB', (20, 16), 'red').save(self.source / 'poster.png')
        self.records, _ = media_scan.discover([self.source], self.root / 'report', False, [])
        self.videos = [record for record in self.records if record['kind'] == '视频']
        self.photo = next(record for record in self.records if record['kind'] == '照片')
        self.batches = []
        owner = self
        class TrackingPool(ThreadPoolExecutor):
            def map(self, fn, *iterables, **options):
                batch = list(iterables[0])
                owner.batches.append(len(batch))
                return super().map(fn, batch, **options)
        self.pool = TrackingPool

    def groups(self):
        return [{'title': record['name'], 'poster': '', 'files': [{'path': record['path'], 'extension': 'mp4'}]}
                for record in self.videos]

    def test_video_and_local_poster_generation_uses_bounded_batches(self):
        outputs = []
        class Worker:
            def __init__(self, helper):
                pass
            def request(self, path, thumbnail=None, **options):
                Image.new('RGB', (20, 16), 'blue').save(thumbnail)
                outputs.append(path)
                return {'path': path, 'thumbnail': thumbnail}
            def close(self):
                pass
        video_output = self.root / 'video-report'
        local_output = self.root / 'local-report'
        video_output.mkdir(); local_output.mkdir()
        groups = self.groups()
        candidates = [[self.photo] if index % 2 == 0 else [] for index in range(len(groups))]
        with mock.patch.object(media_scan, 'ThreadPoolExecutor', self.pool), \
             mock.patch.object(media_scan, 'helper_available', return_value=True), \
             mock.patch.object(media_scan, 'ImageProbeWorker', Worker):
            self.assertEqual(media_scan.export_video_frame_covers(video_output, groups, self.records, Path('helper'), []), 10)
            with mock.patch.object(media_scan, 'local_cover_candidates', return_value=candidates):
                self.assertEqual(media_scan.export_local_covers(local_output, self.groups(), self.records, Path('helper'), []), 5)
        self.assertTrue(self.batches)
        self.assertTrue(all(0 < size <= 2 for size in self.batches))
        self.assertEqual(len(outputs), 15)
        self.assertEqual([record['path'] for record in self.videos], [str(self.source / f'video-{i}.mp4') for i in range(10)])

    def test_video_cancel_does_not_queue_the_rest_of_the_library(self):
        calls = []
        class Worker:
            def __init__(self, helper):
                pass
            def request(self, path, thumbnail=None, **options):
                calls.append(path)
                raise KeyboardInterrupt()
            def close(self):
                pass
        output = self.root / 'cancel-report'
        output.mkdir()
        with mock.patch.object(media_scan, 'ThreadPoolExecutor', self.pool), \
             mock.patch.object(media_scan, 'helper_available', return_value=True), \
             mock.patch.object(media_scan, 'ImageProbeWorker', Worker):
            with self.assertRaises(KeyboardInterrupt):
                media_scan.export_video_frame_covers(output, self.groups(), self.records, Path('helper'), [])
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(self.batches, [2])
        self.assertEqual(len(list(self.source.glob('*.mp4'))), 10)


if __name__ == '__main__':
    unittest.main()
