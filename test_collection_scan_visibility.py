"""Collection cards distinguish current results from members in other scans."""
import unittest
import test_media_center as center_tests


class CollectionScanVisibilityTests(unittest.TestCase):
    run_ui = center_tests.MediaCenterTests.run_ui

    def test_unavailable_members_are_counted_separately_without_losing_records(self):
        self.run_ui(r'''
state.collections.mixed={name:'Mixed',ids:[photo.id,'f'.repeat(64)]};state.collections.other={name:'Other scan',ids:['d'.repeat(64)]};state.collections.empty={name:'Empty',ids:[]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();
const card=name=>$('center-content').children[0].children.find(n=>n.children.some(c=>c.textContent===name));const text=n=>n.children.filter(c=>c.tagName==='SPAN').map(c=>c.textContent).join(' ');
assert(text(card('Mixed')).includes('1 项媒体'));assert(text(card('Mixed')).includes('另有 1 项不在本次扫描'));assert(text(card('Other scan')).includes('0 项媒体'));assert(text(card('Other scan')).includes('另有 1 项不在本次扫描'));assert(!text(card('Empty')).includes('不在本次扫描'));
card('Mixed').onclick();assert($('center-subtitle').textContent.includes('1 项媒体'));assert($('center-status').textContent.includes('1 项不在本次扫描'));assert.deepEqual(state.collections.mixed.ids,[photo.id,'f'.repeat(64)]);assert.equal(operations.length,0);
''')

    def test_filter_exclusion_is_not_misreported_as_another_scan(self):
        self.run_ui(r'''
state.collections.mixed={name:'Mixed',ids:[photo.id,movie.id,'f'.repeat(64)]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();$('media-kind').value='photo';$('media-kind').onchange();
const card=$('center-content').children[0].children.find(n=>n.children.some(c=>c.textContent==='Mixed'));const text=card.children.filter(c=>c.tagName==='SPAN').map(c=>c.textContent).join(' ');assert(text.includes('1 项媒体'));assert(text.includes('另有 1 项不在本次扫描'));assert(!text.includes('另有 2 项'));card.onclick();assert($('center-status').textContent.includes('1 项不在本次扫描'));assert.equal(operations.length,0);
''')

    def test_collection_without_missing_members_has_no_warning(self):
        self.run_ui(r'''
state.collections.local={name:'Local',ids:[photo.id]};await $('center-refresh').onclick();location.hash='#collections';events.hashchange();const card=$('center-content').children[0].children.find(n=>n.children.some(c=>c.textContent==='Local'));assert(!card.children.some(n=>n.textContent.includes('不在本次扫描')));card.onclick();assert($('center-status').hidden);assert.equal(operations.length,0);
''')
