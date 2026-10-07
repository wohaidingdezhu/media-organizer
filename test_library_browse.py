"""Exercise the actual shared browser logic with generated report-only data."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import media_scan


class LibraryBrowseTests(unittest.TestCase):
    def browse(self, groups, **options):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Browser behavior tests need Node.js 22+; both CI platforms install it')
        script = '''const fs = require('fs');
const browse = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const before = JSON.stringify(input.groups);
const visible = browse.browseMovies(input.groups, input.options);
process.stdout.write(JSON.stringify({titles: visible.map(group => group.title),
  paths: visible.map(group => group.files.map(file => file.path)),
  folders: browse.movieFolderChoices(input.groups),
  facts: input.groups.map(browse.movieBrowseFacts),
  sizes: input.groups.map(group => browse.movieSizeLabel(browse.movieBrowseFacts(group).bytes)),
  unchanged: before === JSON.stringify(input.groups)}));'''
        result = subprocess.run([node, '-e', script, str(Path(__file__).parent / 'library_browse.js')],
                                input=json.dumps({'groups': groups, 'options': options}),
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['unchanged'], 'Browsing must not mutate report groups or file order')
        return value

    def group(self, title, paths, *, sizes=None, times=None, rating=0, tags=None):
        return {'title': title, 'tags': tags or [], 'personal': {'rating': rating, 'note': ''},
                'files': [{'path': path, 'bytes': (sizes or [10] * len(paths))[number],
                           'mtime': (times or [100] * len(paths))[number], 'sidecars': []}
                          for number, path in enumerate(paths)]}

    def test_same_named_folders_are_distinct_and_cross_folder_group_stays_complete(self):
        for left, right in (('/first/旅行', '/backup/旅行'),
                            ('C:\\first\\旅行', 'D:\\backup\\旅行')):
            suffix = '\\movie.mp4' if ':' in left else '/movie.mp4'
            groups = [self.group('ABC-123', [left + suffix, left + suffix + '.part2', right + suffix]),
                      self.group('Holiday', [right + suffix])]
            with self.subTest(left=left):
                result = self.browse(groups, folder=left)
                self.assertEqual(result['titles'], ['ABC-123'])
                self.assertEqual(len(result['paths'][0]), 3)
                self.assertEqual(dict(result['folders']), {left: 1, right: 2})
                self.assertEqual(result['facts'][0]['bytes'], 30)

    def test_rating_tags_search_and_folder_filters_combine(self):
        groups = [self.group('Movie 1', ['/one/a.mp4'], rating=5, tags=['收藏', '旅行']),
                  self.group('Movie 2', ['/one/b.mp4'], rating=4, tags=['已观看', '旅行']),
                  self.group('Movie 3', ['/two/c.mp4'], rating=0)]
        groups[0]['personal']['note'] = '周末重看'
        self.assertEqual(self.browse(groups, folder='/one', rating='4', tag='旅行',
                                     filter='unwatched', query='周末')['titles'], ['Movie 1'])
        self.assertEqual(self.browse(groups, rating='unrated')['titles'], ['Movie 3'])
        self.assertEqual(self.browse(groups, rating='5')['titles'], ['Movie 1'])
        self.assertEqual(self.browse(groups, filter='watched')['titles'], ['Movie 2'])
        self.assertEqual(self.browse(groups, filter='favorite')['titles'], ['Movie 1'])
        self.assertEqual(self.browse(groups, folder='/missing')['titles'], [])

    def test_sort_uses_whole_group_size_latest_timestamp_and_current_notes(self):
        groups = [self.group('Movie 10', ['/one/a.mp4'], sizes=[20], times=[200], rating=3),
                  self.group('Movie 2', ['/one/b.mp4', '/two/b.mp4'], sizes=[15, 15], times=[50, 300], rating=5),
                  self.group('Movie 1', ['/one/c.mp4'], sizes=[20], times=[200], rating=0)]
        self.assertEqual(self.browse(groups)['titles'], ['Movie 1', 'Movie 2', 'Movie 10'])
        self.assertEqual(self.browse(groups, sort='size')['titles'], ['Movie 2', 'Movie 1', 'Movie 10'])
        self.assertEqual(self.browse(groups, sort='newest')['titles'], ['Movie 2', 'Movie 1', 'Movie 10'])
        self.assertEqual(self.browse(groups, sort='oldest')['titles'], ['Movie 1', 'Movie 10', 'Movie 2'])
        self.assertEqual(self.browse(groups, sort='rating')['titles'], ['Movie 2', 'Movie 10', 'Movie 1'])
        groups[0]['personal']['rating'] = 5
        self.assertEqual(self.browse(groups, rating='5')['titles'], ['Movie 2', 'Movie 10'])

    def test_small_group_sizes_are_visible_instead_of_rounding_to_zero_gigabytes(self):
        groups = [self.group('Small', ['/generated/small.mp4'], sizes=[2 * 1048576]),
                  self.group('Large', ['/generated/large.mp4'], sizes=[3 * 1073741824]),
                  self.group('Empty', ['/generated/empty.mp4'], sizes=[0])]
        self.assertEqual(self.browse(groups)['sizes'], ['2.0 MB', '3.0 GB', '0 B'])

    def test_old_reports_support_both_root_styles_and_put_unknown_dates_last(self):
        groups = [self.group('Unknown', ['C:\\unknown.mp4']),
                  self.group('Older', ['/old.mp4']), self.group('Newer', ['C:\\new.mp4']),
                  self.group('Invalid', ['\\\\server\\share\\旅行\\x.mp4'])]
        for group, date in zip(groups, ('', '2025-12-01 12:00', '2026-01-01 01:00', '2026-02-30 12:00')):
            group.pop('personal')
            group['files'][0].pop('mtime')
            group['files'][0]['modified_at'] = date
        self.assertEqual(self.browse(groups, sort='newest')['titles'], ['Newer', 'Older', 'Invalid', 'Unknown'])
        self.assertEqual(self.browse(groups, sort='oldest')['titles'], ['Older', 'Newer', 'Invalid', 'Unknown'])
        self.assertEqual(self.browse(groups, folder='C:\\')['titles'], ['Newer', 'Unknown'])
        self.assertEqual(self.browse(groups, folder='/')['titles'], ['Older'])
        self.assertEqual(self.browse(groups, folder='\\\\server\\share\\旅行')['titles'], ['Invalid'])
        self.assertEqual(self.browse(groups, rating='unrated')['titles'], ['Invalid', 'Newer', 'Older', 'Unknown'])

    def test_existing_review_filters_and_sidecar_search_remain_separate_from_similarity(self):
        groups = [self.group('Exact', ['/one/a.mp4']), self.group('Related', ['/one/b.mp4'])]
        groups[0]['files'][0]['duplicate_group'] = 1
        groups[0]['files'][0]['sidecars'] = ['/one/中文.srt']
        groups[0]['needs_review'] = groups[0]['has_sidecars'] = True
        groups[1]['poster'] = 'covers/generated.png'
        for filter_name in ('duplicates', 'review', 'sidecars', 'missing-posters'):
            self.assertEqual(self.browse(groups, filter=filter_name)['titles'], ['Exact'])
        self.assertEqual(self.browse(groups, filter='posters')['titles'], ['Related'])
        self.assertEqual(self.browse(groups, query='中文.srt')['titles'], ['Exact'])

    def test_new_snapshot_exports_native_folder_and_time_without_reading_media(self):
        from test_support import sample_path
        path = sample_path('/generated/旅行/ABC-123.mp4')
        records = [{'kind': '视频', 'path': path, 'root': str(Path(path).parent), 'extension': '.mp4',
                    'bytes': 123, 'mtime': 1234567890.125, 'suggested_path': '视频/ABC-123/ABC-123.mp4',
                    'hash_status': '大小唯一，未计算'}]
        library = media_scan.build_video_library(records, [], [], [], [])
        file = library['groups'][0]['files'][0]
        self.assertEqual(file['source_folder'], str(Path(path).parent))
        self.assertEqual(file['mtime'], records[0]['mtime'])
        self.assertEqual(file['name'], 'ABC-123.mp4')
        page = media_scan.render_video_library({'created_at': 'generated', 'video_library': library})
        for marker in ('id="movie-folder"', 'id="movie-rating"', 'id="movie-sort"', 'id="movie-reset"',
                       'browseMovies(library.groups', 'updateFolderChoices();'):
            self.assertIn(marker, page)
        self.assertNotIn('@@BROWSE@@', page)
