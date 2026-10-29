"""Page inversion preserves other pages; selecting does not rebuild targets."""
import unittest
import test_media_center


class PageSelectionEfficiencyTests(unittest.TestCase):
    run_ui = test_media_center.MediaCenterTests.run_ui

    def test_invert_only_current_page_preserves_other_pages_and_cards(self):
        self.run_ui(r'''
items=Array.from({length:110},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.files[0].id]));location.hash='#photos';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();
$('select-page').onclick();$('center-next').onclick();const wall=$('center-content').children.find(n=>n.className.includes('photo-wall'));$('invert-page').onclick();assert($('selected-count').textContent.includes('96 项'));assert.equal($('center-content').children.find(n=>n.className.includes('photo-wall')),wall);
$('invert-page').onclick();assert($('selected-count').textContent.includes('48 项'));$('delete-selected').onclick();assert.equal(operations[0].ids.length,48);const firstPage=new Set(Array.from(operations[0].ids));$('center-prev').onclick();$('invert-page').onclick();assert($('selected-count').textContent.includes('0 项'));assert.equal(firstPage.size,48);
''')

    def test_invert_respects_200_limit_and_repeated_actions_never_delete(self):
        self.run_ui(r'''
items=Array.from({length:250},(_,n)=>({...photo,id:(n+1).toString(16).padStart(64,'0'),title:'Photo '+n,files:[{...photo.files[0],id:(n+1000).toString(16).padStart(64,'0'),path:'/generated/'+n+'.jpg'}]}));access.ids_by_path=Object.fromEntries(items.map(item=>[item.files[0].path,item.files[0].id]));location.hash='#photos';events.hashchange();await $('center-refresh').onclick();$('manage-toggle').onclick();for(let n=0;n<4;n++){$('select-page').onclick();$('center-next').onclick();}
$('invert-page').onclick();assert($('selected-count').textContent.includes('200 项'));assert($('center-error').textContent.includes('上限'));$('invert-page').onclick();assert($('selected-count').textContent.includes('200 项'));$('clear-selection').onclick();assert($('selected-count').textContent.includes('0 项'));assert.equal(operations.length,0);
''')

    def test_target_collection_options_and_choice_survive_checkboxes(self):
        self.run_ui(r'''
for(let n=0;n<300;n++)state.collections['c'+n]={name:'Collection '+n,ids:[]};await $('center-refresh').onclick();$('manage-toggle').onclick();const target=$('target-collection'),options=target.children;target.value='c100';const first=options[0];
$('select-page').onclick();$('invert-page').onclick();$('clear-selection').onclick();assert.equal(target.children,options);assert.equal(target.children[0],first);assert.equal(target.value,'c100');
state.collections.c100.name='Renamed';await $('center-refresh').onclick();assert.equal(target.value,'c100');assert(target.children.some(option=>option.value==='c100'&&option.textContent==='Renamed'));assert.notEqual(target.children,options);
delete state.collections.c100;await $('center-refresh').onclick();assert(!target.children.some(option=>option.value==='c100'));assert.equal(operations.length,0);
''')

    def test_invert_disabled_without_page_media_and_while_saving(self):
        self.run_ui(r'''
location.hash='#collections';events.hashchange();$('manage-toggle').onclick();assert($('invert-page').disabled);location.hash='#photos';events.hashchange();$('manage-toggle').onclick();assert(!$('invert-page').disabled);$('select-page').onclick();let finish;const original=scope.fetch;scope.fetch=async(path,options)=>{if(options?.body&&JSON.parse(options.body).action==='favorite')return new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>state}));return original(path,options);};
const card=$('center-content').children.find(n=>n.className.includes('photo-wall')).children[0],saving=card.children.find(n=>n.className==='card-actions').children.find(n=>n.textContent==='☆ 收藏').onclick();assert($('invert-page').disabled);const count=$('selected-count').textContent;$('invert-page').onclick();assert.equal($('selected-count').textContent,count);finish();await saving;assert(!$('invert-page').disabled);$('query').value='no matching generated photo';$('center-search').onsubmit({preventDefault(){}});assert($('invert-page').disabled);assert.equal(operations.length,0);
''')


if __name__ == '__main__':
    unittest.main()
