"""Large Windows-style catalogs are synthetic; wall-clock numbers aren't CI gates."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import unified_library
from media_center import CenterState, center_catalog
from workspace_data import write_json


class LibraryScaleTests(unittest.TestCase):
    def test_50000_windows_records_cached_reopen_never_reads_original_media(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve()
            for batch, root in enumerate((r'C:\生成测试\照片', r'\\generated-server\媒体\视频')):
                report = output / ('scan-%03d' % batch)
                report.mkdir()
                files = [{'path': root + '\\' + (('很长的目录名' * 8) + '\\') * 4 + '%03d\\%05d.jpg' % (number // 100, number),
                          'root': root, 'kind': '照片', 'bytes': 8 * 1024**3, 'mtime': number,
                          'suggested_path': '照片/2026/11/%05d.jpg' % number}
                         for number in range(25000)]
                write_json(report, 'report.json', {'roots': [root], 'files': files})
                for page in ('report.html', 'library.html'):
                    (report / page).write_text('generated', encoding='utf-8')
            began = time.monotonic()
            directory, document = unified_library.build_library(output)
            built = time.monotonic() - began
            self.assertEqual(len(document['files']), 50000)
            # Cache reuse reads application data, not every source report again.
            reader = unified_library.fs.read_private_file
            reads = []
            def read(root, parts, *args, **kwargs):
                self.assertIn(output, (Path(root), *Path(root).parents))
                reads.append((str(root), parts))
                return reader(root, parts, *args, **kwargs)
            began = time.monotonic()
            with mock.patch.object(unified_library.fs, 'read_private_file', side_effect=read):
                cached, second = unified_library.build_library(output)
            reopened = time.monotonic() - began
            self.assertEqual(cached, directory)
            self.assertEqual(len(reads), 2, reads)  # persistent index + materialized view
            began = time.monotonic()
            result = center_catalog(second, CenterState(output).snapshot())
            projected = time.monotonic() - began
            self.assertEqual(len(result['items']), 50000)
            self.assertEqual(sum(item['bytes'] for item in result['items']), 50000 * 8 * 1024**3)
            self.assertEqual(result['review']['exact'], [])
            print('Generated 50,000 records: build=%.3fs cached=%.3fs catalog=%.3fs' % (built, reopened, projected))


if __name__ == '__main__':
    unittest.main()
