"""Real generated previews plus shared UI fallback behavior on both desktops."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image
import imageio_ffmpeg
import portable_image_probe
import portable_video_cover
import test_media_center as center_tests


class PreviewResilienceTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_transparent_photo_preview_keeps_visible_content_on_white_background(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp).resolve()/'transparent.png', Path(temp).resolve()/'preview.png'
            picture = Image.new('RGBA', (40, 30), (0, 0, 0, 0))
            picture.paste((220, 30, 20, 255), (10, 10, 30, 20))
            picture.save(source)
            before = source.read_bytes()
            portable_image_probe.probe(str(source), str(target))
            with Image.open(target) as preview:
                self.assertEqual(preview.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(preview.getpixel((20, 15)), (220, 30, 20))
            self.assertEqual(source.read_bytes(), before)

    def test_video_black_introduction_uses_visible_middle_frame_and_all_black_still_has_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            for black_only in (False, True):
                with self.subTest(black_only=black_only):
                    source, target = Path(temp).resolve()/('black.mp4' if black_only else 'intro.mp4'), Path(temp).resolve()/('black.png' if black_only else 'intro.png')
                    video_filter = 'color=c=black:s=64x48:d=4:r=5'
                    if not black_only:
                        video_filter += ",drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(t,2)'"
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error',
                                    '-f', 'lavfi', '-i', video_filter, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
                                   capture_output=True, text=True, encoding='utf-8', check=True, timeout=20)
                    before = hashlib.sha256(source.read_bytes()).digest()
                    portable_video_cover.probe(str(source), str(target))
                    with Image.open(target) as preview:
                        red, green, blue = preview.convert('RGB').getpixel((32, 24))
                        if black_only:
                            self.assertLess(max(red, green, blue), 10)
                        else:
                            self.assertGreater(red, 180)
                            self.assertLess(max(green, blue), 40)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_album_missing_and_broken_cover_use_shared_preview_queue(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};
const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>({preview:'covers/album-fixed.png'})};}return originalFetch(path,options);};
movie.preview='';photo.preview='previews/broken.png';await $('center-refresh').onclick();location.hash='#collections';events.hashchange();
const cards=$('center-content').children[0].children,missing=cards.map(card=>card.children.find(n=>n.className==='collection-art')).find(art=>art.children[0].tagName!=='IMG'),broken=cards.map(card=>card.children.find(n=>n.className==='collection-art')).find(art=>art.children[0].tagName==='IMG');
broken.children[0].onerror();observers[0].callback([{target:missing,isIntersecting:true},{target:broken,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert.equal(calls.filter(c=>c.path==='api/media/cover').length,2);assert.equal(missing.children[0].tagName,'IMG');assert.equal(broken.children[0].tagName,'IMG');assert.equal(operations.length,0);
''')

    def test_missing_observer_still_generates_visible_page_previews(self):
        self.run_ui(r'''
delete scope.IntersectionObserver;photo.preview='';items=[photo];const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>({preview:'covers/fallback.png'})};}return originalFetch(path,options);};
location.hash='#photos';events.hashchange();await $('center-refresh').onclick();await new Promise(r=>setImmediate(r));assert.equal($('center-content').children[0].children[0].children[0].children[0].tagName,'IMG');assert.equal(calls.filter(c=>c.path==='api/media/cover').length,1);assert.equal(operations.length,0);
''')

    def test_offline_source_explains_missing_preview_without_claiming_it_will_generate(self):
        self.run_ui(r'''
photo.preview='';items=[photo];access.available=false;await $('center-refresh').onclick();location.hash='#photos';events.hashchange();const cover=$('center-content').children[0].children[0].children[0];assert(cover.children[0].children[1].textContent.includes('磁盘连接'));assert.equal(calls.filter(c=>c.path==='api/media/cover').length,0);assert.equal(operations.length,0);
''')

    def test_temporary_preview_failure_retries_once_then_recovers_or_exposes_retry(self):
        self.run_ui(r'''
const observers=[];scope.IntersectionObserver=class{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(n){this.nodes.push(n);}unobserve(){}disconnect(){}};scope.setTimeout=fn=>{fn();return 1;};
let attempts=0,alwaysFail=false;const originalFetch=scope.fetch;scope.fetch=async(path,options)=>{if(path==='api/media/cover'){attempts++;const fail=alwaysFail||attempts===1;return {ok:!fail,status:fail?503:200,json:async()=>fail?{error:'Temporary generated outage'}:{preview:'covers/retry-success.png'}};}return originalFetch(path,options);};
photo.preview='';items=[photo];await $('center-refresh').onclick();location.hash='#photos';events.hashchange();let cover=$('center-content').children[0].children[0].children[0];observers[0].callback([{target:cover,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert.equal(attempts,2);assert.equal(cover.children[0].tagName,'IMG');
const second={...photo,id:'9'.repeat(64),files:[{...photo.files[0],id:'8'.repeat(64),path:'/generated/second.jpg'}]};items=[second];access.ids_by_path[second.files[0].path]=second.files[0].id;alwaysFail=true;await $('center-refresh').onclick();cover=$('center-content').children[0].children[0].children[0];observers[0].callback([{target:cover,isIntersecting:true}]);await new Promise(r=>setImmediate(r));assert.equal(attempts,4);assert(cover.children[0].children[1].textContent.includes('Temporary'));assert.equal($('preview-progress').value,100);assert.equal(operations.length,0);
''')
