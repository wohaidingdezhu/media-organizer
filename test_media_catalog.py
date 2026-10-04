import json
from pathlib import Path
import tempfile
import unittest

from media_catalog import compare_scans, photo_catalog, root_status, load_notes, save_notes, clean_note, exact_duplicate_catalog


class CatalogTests(unittest.TestCase):
    def duplicate_document(self):
        digest = 'a'*64
        return {'files': [{'path': '/synthetic/travel/a.png', 'kind': '照片', 'bytes': 5,
                          'sha256': digest, 'mtime': 123},
                         {'path': '/synthetic/backup/b.png', 'kind': '照片', 'bytes': 5,
                          'sha256': digest, 'mtime': 456},
                         {'path': '/synthetic/similar.png', 'kind': '照片', 'bytes': 6,
                          'sha256': 'b'*64}],
                'duplicates': [{'paths': ['/synthetic/travel/a.png', '/synthetic/backup/b.png'],
                                'sha256': digest, 'bytes_each': 5, 'redundant_logical_bytes': 999999}],
                'similar': {'pairs': [{'left': '/synthetic/travel/a.png', 'right': '/synthetic/similar.png', 'distance': 0}]},
                'sidecars': [{'path': '/synthetic/travel/a.xmp', 'status': '已关联',
                             'media_paths': ['/synthetic/travel/a.png']}]}

    def test_exact_grouping_uses_saved_full_hashes_and_keeps_all_members(self):
        result = exact_duplicate_catalog(self.duplicate_document())
        self.assertEqual(result['counts'], {'groups': 1, 'files': 2, 'redundant_logical_bytes': 5})
        self.assertEqual(result['warnings'], [])
        group = result['groups'][0]
        self.assertEqual(group['number'], 1)
        self.assertEqual([item['folder'] for item in group['items']], ['/synthetic/travel', '/synthetic/backup'])
        self.assertEqual(group['items'][0]['mtime'], 123)
        self.assertEqual(group['items'][0]['sidecars'][0]['path'], '/synthetic/travel/a.xmp')
        self.assertEqual(group['items'][1]['sidecars'], [])
        self.assertNotIn('/synthetic/similar.png', [item['path'] for item in group['items']])

    def test_incomplete_mismatched_and_hardlink_groups_are_not_promoted_to_exact(self):
        mutations = [lambda d: d['duplicates'][0].pop('sha256'),
                     lambda d: d['duplicates'][0].update(sha256='not a full hash'),
                     lambda d: d['duplicates'][0].update(bytes_each=0),
                     lambda d: d['duplicates'][0]['paths'].append('/synthetic/missing.png'),
                     lambda d: d['duplicates'][0]['paths'].append('/synthetic/travel/a.png'),
                     lambda d: d['files'][1].update(sha256='c'*64),
                     lambda d: d['files'][1].update(bytes=True),
                     lambda d: d['files'][1].update(hardlink_to='/synthetic/travel/a.png'),
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
        document['files'] += [{'path': '/synthetic/c.mp4', 'kind': '视频', 'bytes': 50, 'sha256': 'd'*64, 'mtime': float('inf')},
                              {'path': '/synthetic/d.mp4', 'kind': '视频', 'bytes': 50, 'sha256': 'd'*64, 'mtime': 10**1000}]
        document['duplicates'].append({'paths': ['/synthetic/c.mp4', '/synthetic/d.mp4'], 'sha256': 'd'*64, 'bytes_each': 50})
        result = exact_duplicate_catalog(document)
        self.assertEqual([group['number'] for group in result['groups']], [3, 2])
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(result['counts']['redundant_logical_bytes'], 55)
        self.assertEqual([item['mtime'] for item in result['groups'][0]['items']], [None, None])

    def test_scan_comparison_distinguishes_added_changed_and_absent_without_reading_media(self):
        old = {'roots': ['/synthetic'], 'created_at': 'earlier', 'files': [
            {'path': '/synthetic/a.png', 'bytes': 1, 'mtime': 1}, {'path': '/synthetic/b.png', 'bytes': 2, 'mtime': 2}]}
        current = {'roots': ['/synthetic'], 'files': [
            {'path': '/synthetic/a.png', 'bytes': 1, 'mtime': 3}, {'path': '/synthetic/c.mp4', 'bytes': 3, 'mtime': 1}]}
        result = compare_scans(current, old)
        self.assertEqual(result['counts'], {'added': 1, 'changed': 1, 'absent': 1})
        current['roots'] = ['/other']
        with self.assertRaises(ValueError):
            compare_scans(current, old)

    def test_photo_catalog_and_offline_root_use_saved_report_metadata(self):
        document = {'files': [{'path': '/synthetic/a.png', 'kind': '照片', 'bytes': 5,
                              'suggested_path': '照片/2026/10/a.png'}, {'path': '/synthetic/b.mp4', 'kind': '视频'}],
                    'previews': {'/synthetic/a.png': 'previews/image-1.png'},
                    'duplicates': [{'paths': ['/synthetic/a.png', '/synthetic/c.png']}]}
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
            path.unlink();path.symlink_to(outside)
            with self.assertRaises(OSError):
                load_notes(path)
            self.assertEqual(outside.read_bytes(), b'keep')
