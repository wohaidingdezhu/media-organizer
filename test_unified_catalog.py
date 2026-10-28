"""Cross-scan preview and navigation using generated temporary reports."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlencode, urlsplit

from PIL import Image
import library_index
import media_gui
from media_actions import media_id
from test_report_history import MemoryServer, request
from test_support import make_symlink
import test_compact_browsing
import test_media_center


class UnifiedCatalogTests(unittest.TestCase):
    node = test_compact_browsing.CompactBrowsingTests.node

    def test_cross_scan_previews_are_scoped_to_listed_generated_reports(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(media_gui, 'ThreadingHTTPServer', MemoryServer):
            root = Path(temp).resolve()
            output = root / 'reports'
            output.mkdir()
            png = io.BytesIO()
            Image.new('RGB', (16, 12), 'blue').save(png, format='PNG')
            originals = []
            for number, kind in enumerate(('照片', '视频'), 1):
                source = root / ('source' + str(number))
                source.mkdir()
                path = source / ('one.jpg' if kind == '照片' else 'two.mp4')
                path.write_bytes(b'generated-only')
                originals.append(path)
                report = output / ('scan-0' + str(number))
                report.mkdir()
                category = 'previews' if kind == '照片' else 'covers'
                (report / category).mkdir()
                (report / category / 'sample.png').write_bytes(png.getvalue())
                document = {'roots': [str(source)], 'files': [{'path': str(path), 'kind': kind, 'bytes': path.stat().st_size, 'mtime': 1}],
                            'previews': {str(path): 'previews/sample.png'} if kind == '照片' else {},
                            'video_library': {'groups': [] if kind == '照片' else [{'poster': 'covers/sample.png', 'files': [{'path': str(path)}]}]}}
                (report / 'report.json').write_text(json.dumps(document), encoding='utf-8')
            server, state, url = media_gui.create_dashboard_server(output)
            self.addCleanup(state.close)
            prefix = urlsplit(url).path
            headers, raw = request(server, prefix + 'api/catalog', 'POST', {})
            self.assertIn('200 OK', headers)
            entries = json.loads(raw)['items']
            self.assertEqual(len(entries), 2)
            self.assertEqual({item['preview'] for item in entries}, {'previews/sample.png', 'covers/sample.png'})
            # Existing v1 indexes must discover historical video posters without rescanning media.
            index_path = output / library_index.INDEX
            index = json.loads(index_path.read_text(encoding='utf-8'))
            video = next(item for item in entries if item['kind'] == '视频')
            index['items'][video['id']]['preview'] = ''
            index['scans'][video['report_id']].pop('preview_schema')
            index_path.write_text(json.dumps(index), encoding='utf-8')
            migrated = library_index.catalog(output)['items']
            self.assertEqual(next(item for item in migrated if item['id'] == video['id'])['preview'], 'covers/sample.png')
            for item in entries:
                query = urlencode({'id': item['id'], 'report_id': item['report_id']})
                headers, body = request(server, prefix + 'api/catalog/preview?' + query)
                self.assertIn('200 OK', headers)
                self.assertIn('image/png', headers)
                self.assertEqual(body, png.getvalue())
            wrong = urlencode({'id': entries[0]['id'], 'report_id': entries[1]['report_id']})
            self.assertIn('404', request(server, prefix + 'api/catalog/preview?' + wrong)[0])
            invalid = urlencode({'id': '../outside', 'report_id': entries[0]['report_id']})
            self.assertIn('404', request(server, prefix + 'api/catalog/preview?' + invalid)[0])
            self.assertEqual([p.read_bytes() for p in originals], [b'generated-only'] * 2)
            self.assertIn("img-src 'self'", request(server, prefix)[0])

    def test_thumbnail_rejects_symlinks_and_invalid_png(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / 'reports'
            output.mkdir()
            report = output / 'scan-one'
            (report / 'previews').mkdir(parents=True)
            source = root / 'one.jpg'
            source.write_bytes(b'generated')
            path = report / 'previews' / 'one.png'
            path.write_bytes(b'not a PNG')
            (report / 'report.json').write_text(json.dumps({'roots': [str(root)], 'files': [{'path': str(source), 'kind': '照片', 'bytes': 9}], 'previews': {str(source): 'previews/one.png'}}), encoding='utf-8')
            state = media_gui.DashboardState(output)
            self.addCleanup(state.close)
            item = state.workspace_catalog({})['items'][0]
            with self.assertRaises(FileNotFoundError):
                state.catalog_preview(item['id'], item['report_id'])
            path.unlink()
            make_symlink(path, source)
            with self.assertRaises((OSError, ValueError)):
                state.catalog_preview(item['id'], item['report_id'])

    def test_video_toggle_is_not_disabled_by_photo_toggle(self):
        html = Path(media_gui.BASE / 'dashboard.html').read_text(encoding='utf-8')
        self.assertNotIn("$('covers').disabled=!$('images').checked", html)

    def test_catalog_navigation_race_and_gallery(self):
        self.node(r'''
const html=fs.readFileSync(root+'/dashboard.html','utf8'),pending=[];let opened=[];
Object.assign(scope,{catalogFolder:'',catalogPage:0,catalogPages:1,catalogRequest:0,stateLabels:{new:'新增'},formatTime:x=>x,notice(){},document:{createElement:t=>new E(t)},openCatalogSnapshot:item=>opened.push(item),request:()=>new Promise(resolve=>pending.push(resolve))});
vm.runInContext(html.slice(html.indexOf('async function loadCatalog()'),html.indexOf("$('nav-catalog').onclick")),scope);
const first=scope.loadCatalog(),second=scope.loadCatalog(),item={id:'a'.repeat(64),report_id:'scan-a',name:'sample.jpg',path:'/sample/a.jpg',kind:'照片',state:'new',created_at:'2026'};
const value=items=>({items,total:items.length,page:0,pages:1,all_count:items.length,scans:2,warnings:[],folders:[]});
(async()=>{pending[1](value([item]));await second;pending[0](value([]));await first;assert.equal($('catalog-items').children.length,1);const card=$('catalog-items').children[0];assert.equal(card.className,'catalog-card');assert(card.children[0].children[1].src.includes('api/catalog/preview?'));card.children[0].onclick();assert.equal(opened[0].id,item.id);})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_gallery_link_opens_the_correct_record_in_its_source_snapshot(self):
        test_media_center.MediaCenterTests.run_ui(self, r'''
assert($('media-detail').open);assert.equal($('detail-title').textContent,'Photo');assert.equal(operations.length,0);assert.equal(calls.length,0);
''', setup="location.hash='#photos';location.search='?open_file='+('b'.repeat(64));scope.URLSearchParams=URLSearchParams;")
        test_media_center.MediaCenterTests.run_ui(self, r'''
assert($('media-detail').open);assert.equal($('detail-title').textContent,'Film');assert.equal(operations.length,0);assert.equal(calls.length,0);
''', setup="location.hash='#resources';location.search='?open_file='+('a'.repeat(64));scope.URLSearchParams=URLSearchParams;")


if __name__ == '__main__':
    unittest.main()
