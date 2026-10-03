import json
from pathlib import Path
import tempfile
import unittest

from media_catalog import compare_scans, photo_catalog, root_status, load_notes, save_notes, clean_note


class CatalogTests(unittest.TestCase):
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
