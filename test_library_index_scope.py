"""Cross-scan scope uses generated reports, never original media."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import library_index


class LibraryIndexScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.output = self.base / 'reports'
        self.output.mkdir()
        self.parent = self.base / 'photos'
        self.child = self.parent / 'trip'
        self.other = self.parent / 'family'
        self.one = self.child / 'one.jpg'
        self.two = self.other / 'two.jpg'

    def report(self, name, roots, paths, issues=(), *, source_root=None):
        folder = self.output / name
        folder.mkdir()
        files = [{'path': str(path), 'kind': '照片', 'bytes': 10, 'mtime': 1, 'sha256': ''}
                 for path in paths]
        if source_root is not None:
            for record in files:
                record['root'] = str(source_root)
        (folder / 'report.json').write_text(json.dumps({'roots': list(map(str, roots)),
            'files': files, 'issues': list(issues), 'created_at': '2026-01-01'}, ensure_ascii=False), encoding='utf-8')

    def items(self):
        return {item['path']: item for item in library_index.catalog(self.output)['items']}

    def test_subdirectory_scan_only_marks_absent_files_under_selected_subdirectory(self):
        self.report('scan-01', [self.parent], [self.one, self.two])
        self.report('scan-02', [self.child], [])
        items = self.items()
        self.assertEqual(items[str(self.one)]['state'], 'not_seen')
        self.assertEqual(items[str(self.two)]['state'], 'new')

    def test_parent_scan_marks_absence_of_old_subdirectory_record(self):
        self.report('scan-01', [self.child], [self.one])
        self.report('scan-02', [self.parent], [])
        self.assertEqual(self.items()[str(self.one)]['state'], 'not_seen')

    def test_unreadable_directory_and_unreadable_file_do_not_imply_absence(self):
        self.report('scan-01', [self.parent], [self.one, self.two])
        self.report('scan-02', [self.parent], [], [
            {'path': str(self.child), 'reason': '无法读取目录：Permission denied'},
            {'path': str(self.two), 'reason': '文件状态读取失败：Permission denied'}])
        items = self.items()
        self.assertEqual(items[str(self.one)]['state'], 'new')
        self.assertEqual(items[str(self.two)]['state'], 'new')

    def test_windows_unc_and_drive_scope_are_compared_by_components(self):
        first = r'C:\Photos\A\one.jpg'
        other = r'C:\Photos\AB\two.jpg'
        nas = r'\\server\share\A\three.jpg'
        self.report('scan-01', [r'C:\Photos', r'\\server\share'], [first, other, nas])
        self.report('scan-02', [r'c:\PHOTOS\A'], [])
        items = self.items()
        self.assertEqual(items[first]['state'], 'not_seen')
        self.assertEqual(items[other]['state'], 'new')
        self.assertEqual(items[nas]['state'], 'new')

    def test_large_rescan_parses_each_retained_media_path_once_per_report(self):
        paths = [self.child / f'{number}.jpg' for number in range(500)]
        roots = [self.parent / f'other-{number}' for number in range(20)] + [self.parent, self.child]
        self.report('scan-01', roots, paths)
        self.report('scan-02', roots, paths, source_root=self.child)
        with mock.patch.object(library_index, 'source_path', wraps=library_index.source_path) as parse:
            index, warnings = library_index.sync_index(self.output)
        self.assertEqual(warnings, [])
        self.assertEqual(len(index['items']), len(paths))
        self.assertTrue(all(item['state'] == 'seen' and item['root'] == str(self.child)
                            for item in index['items'].values()))
        self.assertLessEqual(parse.call_count, 2 * (len(paths) + len(roots)))

    def test_legacy_record_uses_first_matching_root(self):
        self.report('scan-01', [self.parent, self.child], [self.one])
        self.assertEqual(self.items()[str(self.one)]['root'], str(self.parent))


if __name__ == '__main__':
    unittest.main()
