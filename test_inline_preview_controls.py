"""Inline previews can be closed, retried, and respect current access state."""
import unittest
import test_media_center as center_tests


class InlinePreviewControlTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_open_close_and_retry_preview_without_opening_original(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const file=$('detail-content').children[0].children[1].children.find(n=>n.className==='file'),inline=file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='页内预览');
inline.onclick();const first=file.children.find(n=>n.tagName==='VIDEO');assert(first);assert.equal(inline.textContent,'关闭预览');first.onerror();assert($('detail-status').textContent.includes('页内预览无法读取'));inline.onclick();assert(!file.children.includes(first));assert(first.paused);assert(first.unloaded);assert.equal(inline.textContent,'页内预览');assert.equal($('detail-status').textContent,'');inline.onclick();const second=file.children.find(n=>n.tagName==='VIDEO');assert(second&&second!==first);first.onerror();assert.equal($('detail-status').textContent,'');assert.equal(calls.length,0);assert.equal(operations.length,0);
''', setup=r'''
E.prototype.pause=function(){this.paused=true;};E.prototype.removeAttribute=function(key){delete this[key];};E.prototype.load=function(){this.unloaded=true;};E.prototype.removeChild=function(child){this.children=this.children.filter(n=>n!==child);};
''')

    def test_access_loss_disables_open_but_always_allows_closing_existing_preview(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const file=$('detail-content').children[0].children[1].children.find(n=>n.className==='file'),inline=file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='页内预览');inline.onclick();const player=file.children.find(n=>n.tagName==='VIDEO');
delete access.ids_by_path[movie.files[0].path];await $('center-refresh').onclick();assert(!inline.disabled);inline.onclick();assert(!file.children.includes(player));assert(inline.disabled);inline.onclick();assert(!file.children.some(n=>n.tagName==='VIDEO'));
access.ids_by_path[movie.files[0].path]=movie.files[0].id;await $('center-refresh').onclick();assert(!inline.disabled);inline.onclick();assert(file.children.some(n=>n.tagName==='VIDEO'));assert.equal(operations.length,0);
''', setup=r'''
E.prototype.pause=function(){};E.prototype.removeAttribute=function(key){delete this[key];};E.prototype.load=function(){};E.prototype.removeChild=function(child){this.children=this.children.filter(n=>n!==child);};
''')

    def test_failed_catalog_refresh_disables_preview_until_recovery(self):
        self.run_ui(r'''
$('center-content').children[0].children[0].children[0].onclick();const file=$('detail-content').children[0].children[1].children.find(n=>n.className==='file'),inline=file.children.find(n=>n.className==='actions').children.find(n=>n.textContent==='页内预览');
failCenter=true;await $('center-refresh').onclick();assert(inline.disabled);inline.onclick();assert(!file.children.some(n=>n.tagName==='VIDEO'));failCenter=false;await $('center-refresh').onclick();assert(!inline.disabled);assert.equal(operations.length,0);
''')
