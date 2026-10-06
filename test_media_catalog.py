from test_support import sample_path
from test_support import make_symlink
import json
from pathlib import Path
import tempfile
import unittest

from media_catalog import compare_scans, photo_catalog, root_status, load_notes, save_notes, clean_note, exact_duplicate_catalog, storage_catalog


class CatalogTests(unittest.TestCase):
    def test_storage_uses_logical_report_sizes_and_keeps_same_named_folders_distinct(self):
        document = self.duplicate_document()
        document['roots'] = [sample_path('/synthetic'), sample_path('/synthetic/travel')]
        document['files'][0]['suggested_path'] = '照片/2026/10/a.png'
        document['files'][1]['suggested_path'] = '照片/日期未知/b.png'
        document['files'] += [{'path': sample_path('/synthetic/other/travel/film.mp4'), 'kind': '视频', 'bytes': 100},
                              {'path': sample_path('/synthetic/other/link.png'), 'kind': '照片', 'bytes': 5,
                               'hardlink_to': sample_path('/synthetic/travel/a.png'), 'suggested_path': '照片/2026/13/link.png'}]
        result = storage_catalog(document)
        self.assertEqual(result['logical_bytes'], 121)
        self.assertEqual(result['duplicate_logical_bytes'], 5)
        self.assertEqual(result['hardlink_references'], 1)
        folders = {row['name']: row for row in result['folders']}
        self.assertEqual(folders[sample_path('/synthetic/travel')]['bytes'], 5)
        self.assertEqual(folders[sample_path('/synthetic/other/travel')]['bytes'], 100)
        roots = {row['name']: row['bytes'] for row in result['roots']}
        self.assertEqual(roots, {sample_path('/synthetic'): 116, sample_path('/synthetic/travel'): 5})
        months = {row['name']: row['count'] for row in result['photo_months']}
        self.assertEqual(months, {'2026/10': 1, '日期未分类': 3})
        self.assertEqual(result['largest'][0]['name'], 'film.mp4')

    def test_storage_does_not_promote_similar_or_invalid_duplicate_groups(self):
        document = self.duplicate_document()
        document['duplicates'][0]['sha256'] = 'broken'
        result = storage_catalog(document)
        self.assertEqual(result['logical_bytes'], 16)
        self.assertEqual(result['duplicate_logical_bytes'], 0)
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(storage_catalog({'files': []})['logical_bytes'], 0)

    def test_storage_refuses_ambiguous_sizes_paths_and_limits_large_file_rows(self):
        for change in ({'bytes': True}, {'bytes': -1}, {'kind': []}, {'path': sample_path('/synthetic/../outside/a.png')}):
            document = self.duplicate_document(); document['files'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                storage_catalog(document)
        document = self.duplicate_document(); document['files'].append(document['files'][0])
        with self.assertRaises(ValueError):
            storage_catalog(document)
        result = storage_catalog({'files': [{'path': sample_path(f'/synthetic/generated-{number}.mp4'), 'kind': '视频', 'bytes': number}
                                           for number in range(125)]})
        self.assertEqual(result['count'], 125)
        self.assertEqual(len(result['largest']), 100)
        self.assertEqual(result['largest'][0]['bytes'], 124)
        self.assertEqual(result['largest'][-1]['bytes'], 25)

    def duplicate_document(self):
        digest = 'a'*64
        return {'files': [{'path': sample_path('/synthetic/travel/a.png'), 'kind': '照片', 'bytes': 5,
                          'sha256': digest, 'mtime': 123},
                         {'path': sample_path('/synthetic/backup/b.png'), 'kind': '照片', 'bytes': 5,
                          'sha256': digest, 'mtime': 456},
                         {'path': sample_path('/synthetic/similar.png'), 'kind': '照片', 'bytes': 6,
                          'sha256': 'b'*64}],
                'duplicates': [{'paths': [sample_path('/synthetic/travel/a.png'), sample_path('/synthetic/backup/b.png')],
                                'sha256': digest, 'bytes_each': 5, 'redundant_logical_bytes': 999999}],
                'similar': {'pairs': [{'left': sample_path('/synthetic/travel/a.png'), 'right': sample_path('/synthetic/similar.png'), 'distance': 0}]},
                'sidecars': [{'path': sample_path('/synthetic/travel/a.xmp'), 'status': '已关联',
                             'media_paths': [sample_path('/synthetic/travel/a.png')]}]}

    def test_exact_grouping_uses_saved_full_hashes_and_keeps_all_members(self):
        result = exact_duplicate_catalog(self.duplicate_document())
        self.assertEqual(result['counts'], {'groups': 1, 'files': 2, 'redundant_logical_bytes': 5})
        self.assertEqual(result['warnings'], [])
        group = result['groups'][0]
        self.assertEqual(group['number'], 1)
        self.assertEqual([item['folder'] for item in group['items']], [sample_path('/synthetic/travel'), sample_path('/synthetic/backup')])
        self.assertEqual(group['items'][0]['mtime'], 123)
        self.assertEqual(group['items'][0]['sidecars'][0]['path'], sample_path('/synthetic/travel/a.xmp'))
        self.assertEqual(group['items'][1]['sidecars'], [])
        self.assertNotIn(sample_path('/synthetic/similar.png'), [item['path'] for item in group['items']])

    def test_incomplete_mismatched_and_hardlink_groups_are_not_promoted_to_exact(self):
        mutations = [lambda d: d['duplicates'][0].pop('sha256'),
                     lambda d: d['duplicates'][0].update(sha256='not a full hash'),
                     lambda d: d['duplicates'][0].update(bytes_each=0),
                     lambda d: d['duplicates'][0]['paths'].append(sample_path('/synthetic/missing.png')),
                     lambda d: d['duplicates'][0]['paths'].append(sample_path('/synthetic/travel/a.png')),
                     lambda d: d['files'][1].update(sha256='c'*64),
                     lambda d: d['files'][1].update(bytes=True),
                     lambda d: d['files'][1].update(hardlink_to=sample_path('/synthetic/travel/a.png')),
                     lambda d: d['files'][1].update(kind=[])]
        for mutation in mutations:
            document = self.duplicate_document();mutation(document)
            with self.subTest(mutation=mutation):
                result = exact_duplicate_catalog(document)
                self.assertEqual(result['groups'], [])
                self.assertEqual(len(result['warnings']), 1)

    def test_overlapping_groups_and_repeated_file_paths_are_rejected(self):
        document = self.duplicate_document()
        document['duplicates'].append(document['duplicates'][0].copy())
        result = exact_duplicate_catalog(document)
        self.assertEqual(result['groups'], [])
        self.assertEqual(len(result['warnings']), 2)
        document = self.duplicate_document();document['files'].append(document['files'][0].copy())
        self.assertEqual(exact_duplicate_catalog(document)['groups'], [])

    def test_invalid_group_does_not_hide_valid_groups_and_large_ones_sort_first(self):
        document = self.duplicate_document()
        document['duplicates'].insert(0, None)
        document['files'] += [{'path': sample_path('/synthetic/c.mp4'), 'kind': '视频', 'bytes': 50, 'sha256': 'd'*64, 'mtime': float('inf')},
                              {'path': sample_path('/synthetic/d.mp4'), 'kind': '视频', 'bytes': 50, 'sha256': 'd'*64, 'mtime': 10**1000}]
        document['duplicates'].append({'paths': [sample_path('/synthetic/c.mp4'), sample_path('/synthetic/d.mp4')], 'sha256': 'd'*64, 'bytes_each': 50})
        result = exact_duplicate_catalog(document)
        self.assertEqual([group['number'] for group in result['groups']], [3, 2])
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(result['counts']['redundant_logical_bytes'], 55)
        self.assertEqual([item['mtime'] for item in result['groups'][0]['items']], [None, None])

    def test_scan_comparison_distinguishes_added_changed_and_absent_without_reading_media(self):
        old = {'roots': [sample_path('/synthetic')], 'created_at': 'earlier', 'files': [
            {'path': sample_path('/synthetic/a.png'), 'bytes': 1, 'mtime': 1}, {'path': sample_path('/synthetic/b.png'), 'bytes': 2, 'mtime': 2}]}
        current = {'roots': [sample_path('/synthetic')], 'files': [
            {'path': sample_path('/synthetic/a.png'), 'bytes': 1, 'mtime': 3}, {'path': sample_path('/synthetic/c.mp4'), 'bytes': 3, 'mtime': 1}]}
        result = compare_scans(current, old)
        self.assertEqual(result['counts'], {'added': 1, 'changed': 1, 'absent': 1})
        current['roots'] = ['/other']
        with self.assertRaises(ValueError):
            compare_scans(current, old)

    def test_photo_catalog_and_offline_root_use_saved_report_metadata(self):
        document = {'files': [{'path': sample_path('/synthetic/a.png'), 'kind': '照片', 'bytes': 5,
                              'suggested_path': '照片/2026/10/a.png'}, {'path': sample_path('/synthetic/b.mp4'), 'kind': '视频'}],
                    'previews': {sample_path('/synthetic/a.png'): 'previews/image-1.png'},
                    'duplicates': [{'paths': [sample_path('/synthetic/a.png'), sample_path('/synthetic/c.png')]}]}
        item = photo_catalog(document)['items'][0]
        self.assertEqual(item['month'], '2026/10')
        self.assertEqual(item['duplicate_group'], 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            result = root_status([root, root/'not-connected'])
            self.assertEqual([item['status'] for item in result['roots']], ['available', 'unavailable'])

    def test_movie_notes_persist_privately_and_bad_or_symlink_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, key = root/'library-notes.json', 'a'*64
            self.assertEqual(load_notes(path), {})
            value = clean_note({'rating': 4, 'note': ' 生成样例\n备注 '})
            save_notes(path, {key: value})
            self.assertEqual(load_notes(path), {key: {'rating': 4, 'note': '生成样例\n备注'}})
            if __import__('os').name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for invalid in ({'rating': True}, {'rating': 6}, {'rating': 2, 'note': 'x'*2001}):
                with self.assertRaises(ValueError):
                    clean_note(invalid)
            path.write_text('{broken')
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                load_notes(path)
            self.assertEqual(path.read_bytes(), before)
            outside = root/'outside';outside.write_bytes(b'keep')
            path.unlink();make_symlink(path, outside)
            with self.assertRaises(OSError):
                load_notes(path)
            self.assertEqual(outside.read_bytes(), b'keep')
