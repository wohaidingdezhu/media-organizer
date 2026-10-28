"""Large-scan behaviour using only temporary generated files and subprocess doubles."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import media_scan
import movie_attachments
import library_index
import portable_video_cover


class LargeScanPerformanceTests(unittest.TestCase):
    def test_large_folder_index_counts_children_without_rescanning_all_items_for_each_folder(self):
        items = {}
        for folder in range(400):
            for number in range(2):
                path = f'/generated/folder-{folder:04d}/file-{number}.jpg'
                items[path] = {'path': path, 'folder': f'/generated/folder-{folder:04d}',
                               'root': '/generated', 'kind': '照片', 'state': 'seen'}
        with mock.patch.object(library_index, 'sync_index', return_value=({'items': items, 'scans': {}}, [])), \
             mock.patch.object(library_index, 'source_path', wraps=library_index.source_path) as parse:
            result = library_index.catalog('/unused', folder='/generated')
        self.assertEqual(result['total'], 800)
        self.assertEqual(len(result['folders']), 400)
        self.assertTrue(all(item['count'] == 2 for item in result['folders']))
        self.assertLess(parse.call_count, 8000)

    def test_folder_counts_include_nested_windows_paths_without_merging_neighbour_names(self):
        paths = [r'C:\Media\Trip\a.jpg', r'c:\media\TRIP\sub\b.jpg', r'C:\Media\Trip2\c.jpg']
        items = {path: {'path': path, 'folder': str(library_index.source_path(path).parent),
                        'root': r'C:\Media', 'kind': '照片', 'state': 'seen'} for path in paths}
        with mock.patch.object(library_index, 'sync_index', return_value=({'items': items, 'scans': {}}, [])):
            result = library_index.catalog('/unused', folder=r'C:\Media')
        counts = {item['name'].casefold(): item['count'] for item in result['folders']}
        self.assertEqual(counts, {'trip': 2, 'trip2': 1})

    def test_many_exact_duplicate_photos_do_not_quadratically_compare_each_pair(self):
        accesses = [0]
        class Record(dict):
            def __getitem__(self, key):
                if key == 'sha256':
                    accesses[0] += 1
                return super().__getitem__(key)
        records = [Record(path=f'/generated/{number}.jpg', hardlink_to='', sha256='a' * 64,
                          image={'dhash': '123456789abcdef0', 'width': 640, 'height': 480, 'low_detail': False})
                   for number in range(1500)]
        result = media_scan.similar_images(records, 6, 5000, True)
        self.assertEqual(result['pairs'], [])
        self.assertFalse(result['truncated'])
        self.assertLess(accesses[0], 5000)
        records.append(Record(path='/generated/different.jpg', hardlink_to='', sha256='b' * 64,
                              image={'dhash': '123456789abcdef0', 'width': 640, 'height': 480, 'low_detail': False}))
        mixed = media_scan.similar_images(records, 6, 5000, True)
        self.assertEqual(len(mixed['pairs']), 1500)
        self.assertEqual(mixed['pairs'][0]['left'], '/generated/0.jpg')
        self.assertEqual(mixed['pairs'][-1]['left'], '/generated/1499.jpg')

    def test_many_equal_size_but_different_files_do_not_read_every_full_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for index in range(12):
                with (root / f'{index}.mp4').open('wb') as stream:
                    stream.write(bytes([index]) * 256 * 1024)
                    stream.seek(2 * 1024 * 1024 - 1)
                    stream.write(b'z')
            issues = []
            records, _ = media_scan.discover([root], root / 'output', False, issues)
            with mock.patch.object(media_scan, 'full_hash', wraps=media_scan.full_hash) as full:
                self.assertEqual(media_scan.exact_duplicates(records, issues), [])
            self.assertEqual(full.call_count, 0)
            self.assertEqual(len(records), 12)
            self.assertFalse(issues)

    def test_many_unrelated_photos_are_not_rechecked_for_each_video_group(self):
        records = [{'kind': '照片', 'path': f'/generated/ordinary-{index}.jpg', 'bytes': 1} for index in range(500)]
        records += [{'kind': '照片', 'path': '/generated/poster.jpg', 'bytes': 1},
                    {'kind': '照片', 'path': '/generated/extrafanart/scene.jpg', 'bytes': 1}]
        groups = [{'title': f'video-{index}', 'files': [{'path': f'/generated/video-{index}.mp4'}]} for index in range(200)]
        with mock.patch.object(movie_attachments, 'source_path', wraps=movie_attachments.source_path) as parse:
            result = movie_attachments.attachment_manifests({'files': records, 'video_library': {'groups': groups}, 'sidecars': []})
        self.assertEqual(len(result), 200)
        self.assertLess(parse.call_count, 10000)
        self.assertTrue(all(any(item['path'] == '/generated/poster.jpg' for item in group['skipped']) for group in result))
        self.assertTrue(all(any(item['path'] == '/generated/extrafanart/scene.jpg' for item in group['skipped']) for group in result))

    def test_sampling_skips_unequal_large_files_but_confirms_duplicates_in_full(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            block = b'a' * (1100 * 1024)
            data = [block, block, b'b' + block[1:], block[:550000] + b'c' + block[550001:]]
            paths = []
            for index, content in enumerate(data):
                path = root / (str(index) + '.jpg')
                path.write_bytes(content)
                paths.append(path)
            issues = []
            records, _ = media_scan.discover([root], root / 'output', False, issues)
            with mock.patch.object(media_scan, 'full_hash', wraps=media_scan.full_hash) as full:
                groups = media_scan.exact_duplicates(records, issues)
            self.assertEqual(set(groups[0]['paths']), {str(paths[0]), str(paths[1])})
            self.assertEqual(groups[0]['sha256'], hashlib.sha256(block).hexdigest())
            self.assertEqual({call.args[0]['path'] for call in full.call_args_list}, {str(paths[0]), str(paths[1]), str(paths[3])})
            self.assertIn('抽样无匹配', next(item['hash_status'] for item in records if item['path'] == str(paths[2])))
            self.assertFalse(issues)
            self.assertEqual([path.read_bytes() for path in paths], data)

    def test_unreadable_sample_is_reported_and_never_claimed_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in ('a.jpg', 'b.jpg'):
                (root / name).write_bytes(b'x' * (1100 * 1024))
            issues = []
            records, _ = media_scan.discover([root], root / 'output', False, issues)
            original = media_scan.sample_hash
            def sample(record):
                if record['name'] == 'a.jpg':
                    raise OSError('generated read error')
                return original(record)
            with mock.patch.object(media_scan, 'sample_hash', side_effect=sample):
                self.assertEqual(media_scan.exact_duplicates(records, issues), [])
            self.assertTrue(any('generated read error' in item['reason'] for item in issues))
            self.assertIn('未确认', next(item['hash_status'] for item in records if item['name'] == 'a.jpg'))

    def test_video_metadata_uses_two_bounded_workers_and_keeps_failures_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in ('a.mp4', 'b.mp4', 'c.mp4'):
                (root / name).write_bytes(b'generated video')
            issues = []
            records, _ = media_scan.discover([root], root / 'output', False, issues)
            barrier = threading.Barrier(2)
            created = []
            class Worker:
                def __init__(self, helper):
                    self.closed = False
                    created.append(self)
                def request(self, path, timeout=12):
                    if Path(path).name in {'a.mp4', 'b.mp4'}:
                        barrier.wait(timeout=5)
                    if Path(path).name == 'c.mp4':
                        raise ValueError('generated decode failure')
                    return {'duration_seconds': 3, 'width': 16, 'height': 12}
                def close(self):
                    self.closed = True
            with mock.patch.object(media_scan, 'helper_available', return_value=True), mock.patch.object(media_scan, 'ImageProbeWorker', Worker):
                summary = media_scan.inspect_video_metadata(records, issues)
            self.assertEqual(summary['inspected'], 2)
            self.assertEqual(len(created), 2)
            self.assertTrue(all(worker.closed for worker in created))
            self.assertTrue(any('generated decode failure' in item['reason'] for item in issues))
            self.assertEqual(sum('video' in item for item in records), 2)

    def test_video_helper_passes_hidden_process_options_for_metadata_and_cover(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, output = root / 'video.mp4', root / 'preview.png'
            source.write_bytes(b'generated video')
            metadata = mock.Mock(stderr=b'Duration: 00:00:03.00,\n  Stream #0:0: Video: h264, yuv420p, 16x12\n', returncode=1)
            frame = mock.Mock(stdout=b'\x89PNG\r\n\x1a\n' + b'generated', returncode=0)
            with mock.patch.object(portable_video_cover, 'hidden_process_options', return_value={'creationflags': 0x08000000}), \
                 mock.patch.object(portable_video_cover.subprocess, 'run', side_effect=[metadata, frame]) as run, \
                 mock.patch.object(portable_video_cover, 'dark_frame', return_value=False):
                result = portable_video_cover.probe(str(source), str(output))
            self.assertEqual(result['thumbnail'], str(output))
            self.assertEqual(run.call_count, 2)
            self.assertTrue(all(call.kwargs['creationflags'] == 0x08000000 for call in run.call_args_list))
            self.assertEqual(source.read_bytes(), b'generated video')


if __name__ == '__main__':
    unittest.main()
