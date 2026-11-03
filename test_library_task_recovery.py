"""Generated 200-file batches survive view changes; no real desktop operation."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import file_operations
from media_actions import MediaActions, file_signature, media_id
from organization_plan import OrganizationPlan
from workspace_data import write_json
from library_backup import allowed, export_backup
import test_direct_cleanup


class LibraryTaskRecoveryTests(unittest.TestCase):
    def test_200_file_safe_cancel_survives_restart_and_new_library_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, first, second, journal = [root / name for name in ('source', 'library-first', 'library-second', 'library-actions')]
            for directory in (source, first, second, journal):
                directory.mkdir()
            records = []
            for number in range(200):
                path = source / ('样例-%03d.jpg' % number)
                content = ('generated %d' % number).encode()
                path.write_bytes(content)
                records.append({'path': str(path), 'kind': '照片', 'bytes': len(content),
                                'mtime': path.stat().st_mtime, 'source_signature': file_signature(path.stat()),
                                'sha256': hashlib.sha256(content).hexdigest(), 'suggested_path': '照片/' + path.name})
            doc = {'roots': [str(source)], 'files': records, 'duplicates': []}
            media = MediaActions(doc)
            plan = OrganizationPlan(first, doc)
            ops = file_operations.FileOperations(first, media, lambda: plan, operation_root=journal)
            ids = [media_id(record['path']) for record in records]
            calls = []
            def trash(*args, **kwargs):
                calls.append(args)
                job = next(iter(ops.jobs.values()))
                ops.request_stop(job['id'])
                return {'trashed_path': 'generated/mock-trash'}
            with mock.patch.object(file_operations, 'trash_helper', return_value=Path('generated-helper')), mock.patch.object(file_operations, 'trash_one', side_effect=trash):
                preview = ops.preview('trash', ids)
                job = ops.start(preview['token'])
                deadline = time.monotonic() + 10
                while ops.snapshot(job['id'])['status'] == 'running' and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertEqual(len(calls), 1)
            self.assertEqual(ops.snapshot(job['id'])['status'], 'cancelled')
            next_plan = OrganizationPlan(second, doc)
            reopened = file_operations.FileOperations(second, MediaActions(doc), lambda: next_plan, operation_root=journal)
            remaining = reopened.remaining(job['id'])
            self.assertEqual(remaining['ids'], ids[1:])
            self.assertEqual(len(remaining['skipped']), 1)
            self.assertEqual(reopened.history()['record_count'], 1)
            # A new scan can see a restored file at the same path. Old journals
            # cannot falsely hide it, but resume still conservatively skips it.
            self.assertEqual(reopened.history()['handled']['trash'], [])
            self.assertEqual(ops.history()['handled']['trash'], [records[0]['path']])
            self.assertEqual(len(list(source.iterdir())), 200)
            self.assertTrue(all(path.read_bytes().startswith(b'generated ') for path in source.iterdir()))
            self.assertTrue(allowed('library-actions/operations/' + job['id'] + '.json'))
            self.assertFalse(allowed('library-actions/operations/../../outside.json'))
            report = root / 'scan-generated'
            report.mkdir()
            write_json(report, 'report.json', doc)
            (report / 'report.html').write_text('generated', encoding='utf-8')
            import io, zipfile
            with zipfile.ZipFile(io.BytesIO(export_backup(root))) as archive:
                self.assertIn('library-actions/operations/' + job['id'] + '.json', archive.namelist())

    def test_resume_opens_new_confirmation_without_starting_or_repeating_completed_files(self):
        test_direct_cleanup.DirectCleanupTests.node(self, r'''
(async()=>{
const ids=Array.from({length:199},(_,n)=>(n+2).toString(16).padStart(64,'0')),body=new E();let closed=0;
Object.assign(scope,{document:{body,createElement:t=>new E(t),querySelector:()=>null},clearTimeout(){},setTimeout(){return 1;},fetch:async(path,options)=>{requests.push({path,options});return {ok:true,json:async()=>path.endsWith('/remaining')?{mode:'trash',ids,skipped:[{path:a.path}]}:{token:'fresh',items:ids.map(id=>({path:id+'.jpg'}))}};}});
vm.runInContext(fs.readFileSync(root+'/report_actions.js','utf8'),scope);
await scope.reportResumeTrash('f'.repeat(24),()=>{},()=>{},()=>closed++);await new Promise(setImmediate);
assert.equal(closed,1);assert.equal(body.children.length,1);assert(body.children[0].open);
assert.equal(requests.length,2);assert(requests[1].path.endsWith('/preview'));assert.deepEqual(JSON.parse(requests[1].options.body).ids,ids);assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
