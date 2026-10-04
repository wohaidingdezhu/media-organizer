"""All sources and destinations here are freshly generated temporary data."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import sys
import time
import unittest
from unittest import mock

import file_operations as operations
from media_actions import MediaActions, file_signature, media_id
from organization_plan import OrganizationPlan


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.report, self.destination = [self.root / name for name in ('source', 'report', 'destination')]
        for path in (self.source, self.report, self.destination):
            path.mkdir()
        self.records = []
        for index in range(2):
            path = self.source / f'generated-{index}.png'
            path.write_bytes(b'generated photo ' + bytes([index]) * 64)
            info = path.stat()
            self.records.append({'path': str(path), 'kind': '照片', 'bytes': info.st_size,
                'mtime': info.st_mtime, 'source_signature': file_signature(info),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'suggested_path': f'照片/旅行/{path.name}'})
        self.document = {'roots': [str(self.source)], 'files': self.records, 'duplicates': []}
        self.media = MediaActions(self.document)
        self.plan = OrganizationPlan(self.report, self.document)
        self.ids = [media_id(item['path']) for item in self.records]
        self.plan.set_states(self.ids, 'include')
        self.ops = operations.FileOperations(self.report, self.media, lambda: self.plan)
        self.before = {item['path']: (Path(item['path']).read_bytes(), Path(item['path']).stat().st_mtime_ns) for item in self.records}

    def finished(self, identifier):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.ops.snapshot(identifier)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('generated batch did not finish')

    def test_copy_is_confirmed_verified_keeps_originals_and_records_survive_restart(self):
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        self.assertFalse(any(self.destination.iterdir()))
        job = self.finished(self.ops.start(preview['token'])['id'])
        self.assertEqual(job['status'], 'complete')
        for source, result in zip(self.records, job['items']):
            self.assertEqual(Path(result['target']).read_bytes(), Path(source['path']).read_bytes())
            self.assertEqual(result['sha256'], source['sha256'])
            self.assertEqual(Path(result['target']).stat().st_mtime_ns, Path(source['path']).stat().st_mtime_ns)
        self.assertEqual(self.before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.before})
        reopened = operations.FileOperations(self.report, self.media, lambda: self.plan)
        self.assertEqual(reopened.snapshot(job['id']), job)
        self.assertEqual(reopened.history()['jobs'][0], job)
        self.assertEqual(reopened.request_stop(job['id']), job)
        self.assertEqual((self.report/'operations'/f"{job['id']}.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])

    def test_existing_case_equivalent_and_symlink_targets_never_overwrite(self):
        folder = self.destination/'照片'/'旅行'
        folder.mkdir(parents=True)
        existing = folder/'GENERATED-0.PNG'
        existing.write_bytes(b'keep this target')
        with self.assertRaises(ValueError):
            self.ops.preview('copy', self.ids[:1], str(self.destination))
        self.assertEqual(existing.read_bytes(), b'keep this target')
        other = self.root/'other'
        other.mkdir()
        (self.destination/'link').symlink_to(other, target_is_directory=True)
        self.plan.set_target(self.ids[0], 'link/photo.png')
        self.plan.set_states(self.ids[:1], 'include')
        with self.assertRaises(OSError):
            self.ops.preview('copy', self.ids[:1], str(self.destination))
        self.assertEqual(list(other.iterdir()), [])

    def test_unsafe_destination_pending_items_and_insufficient_space_are_rejected(self):
        for destination in (str(self.source), str(self.root), 'relative', str(self.source/'new')):
            with self.subTest(destination=destination), self.assertRaises((OSError, ValueError)):
                self.ops.preview('copy', self.ids, destination)
        self.plan.set_states(self.ids[:1], 'pending')
        with self.assertRaises(ValueError):
            self.ops.preview('copy', self.ids, str(self.destination))
        self.plan.set_states(self.ids, 'include')
        with mock.patch.object(operations.os, 'fstatvfs', return_value=type('Space', (), {'f_bavail': 0, 'f_frsize': 4096})()), self.assertRaises(ValueError):
            self.ops.preview('copy', self.ids, str(self.destination))

    def test_changed_source_plan_or_destination_after_preview_cannot_execute(self):
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        self.plan.set_target(self.ids[0], '照片/other.png')
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])
        self.plan.set_states(self.ids, 'include')
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        self.destination.rename(self.root/'original-destination')
        self.destination.mkdir()
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        Path(self.records[0]['path']).write_bytes(b'changed generated source')
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])
        self.assertFalse((self.report/'operations').exists())

    def test_copy_detects_changes_during_read_and_leaves_no_partial_file(self):
        source = Path(self.records[0]['path'])
        original = operations.os.read
        mutated = False
        def change_after_read(fd, count):
            nonlocal mutated
            data = original(fd, count)
            if not mutated:
                mutated = True
                source.write_bytes(b'temporary mutation during copy')
            return data
        with mock.patch.object(operations.os, 'read', side_effect=change_after_read), self.assertRaises(ValueError):
            operations.copy_one(self.media, self.ids[0], str(self.destination), self.records[0]['suggested_path'], self.records[0]['source_signature'])
        self.assertEqual(list(self.destination.rglob('*.png')), [])
        self.assertFalse(any(path.name.startswith('.media-copy') for path in self.destination.rglob('*')))

    def test_copy_reports_transfer_and_verification_progress(self):
        progress = []
        record = self.records[0]
        operations.copy_one(self.media, self.ids[0], str(self.destination), record['suggested_path'],
                            record['source_signature'], progress=lambda amount, phase: progress.append((amount, phase)))
        phases = {phase for amount, phase in progress}
        self.assertIn('copying', phases)
        self.assertIn('verifying', phases)
        self.assertEqual(progress[-1], (record['bytes'], 'verified'))
        self.assertEqual(Path(record['path']).read_bytes(), self.before[record['path']][0])

    def test_batch_failure_keeps_successful_copy_and_stops(self):
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        original = operations.copy_one
        count = 0
        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('generated destination failure')
            return original(*args)
        with mock.patch.object(operations, 'copy_one', side_effect=fail_second):
            job = self.finished(self.ops.start(preview['token'])['id'])
        self.assertEqual(job['status'], 'stopped')
        self.assertEqual([item['status'] for item in job['items']], ['success', 'failed'])
        self.assertTrue(Path(job['items'][0]['target']).is_file())
        self.assertEqual(self.before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.before})

    def test_trash_requires_preview_keeps_duplicate_and_never_uses_permanent_delete(self):
        self.document['duplicates'] = [{'paths': [item['path'] for item in self.records]}]
        plan = OrganizationPlan(self.report, self.document)
        self.ops.plan = lambda: plan
        with mock.patch.object(operations, 'trash_helper', return_value=Path('/synthetic/trash-component')):
            with self.assertRaises(ValueError):
                self.ops.preview('trash', self.ids)
            preview = self.ops.preview('trash', self.ids[:1])
            with mock.patch.object(operations.subprocess, 'run', return_value=type('Answer', (), {'returncode': 0, 'stdout': '{"ok": true, "trashed_path": "/synthetic/Trash/sample.png"}'})()) as run:
                job = self.finished(self.ops.start(preview['token'])['id'])
            self.assertEqual(job['status'], 'complete')
            command = run.call_args.args[0]
            self.assertEqual(command, ['/synthetic/trash-component'])
            payload = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['path'], self.records[0]['path'])
            self.assertEqual(payload['signature'], self.records[0]['source_signature'])
            self.assertNotIn('shell', run.call_args.kwargs)
        # Native Trash is mocked; no personal files or real Trash were touched.
        self.assertEqual(self.before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.before})

    def test_expiration_unknown_outcome_and_interrupted_journal_are_visible(self):
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        self.ops.previews[preview['token']]['expires'] = 0
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])
        with mock.patch.object(operations, 'trash_helper', return_value=Path('/synthetic/trash')):
            preview = self.ops.preview('trash', self.ids[:1])
            with mock.patch.object(operations.subprocess, 'run', side_effect=subprocess.TimeoutExpired('trash', 60)):
                job = self.finished(self.ops.start(preview['token'])['id'])
            self.assertEqual(job['items'][0]['status'], 'unknown')
        job['status'] = 'running'
        self.ops._save_job(job)
        reopened = operations.FileOperations(self.report, self.media, lambda: self.plan)
        self.assertEqual(reopened.snapshot(job['id'])['status'], 'interrupted')
        with self.assertRaises(ValueError):
            reopened.snapshot('../escape')

    def test_unwritable_or_symlink_journal_prevents_any_action(self):
        (self.report/'operations').symlink_to(self.destination, target_is_directory=True)
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        with self.assertRaises(ValueError):
            self.ops.start(preview['token'])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cleanup_requires_fresh_report_and_accessible_retained_duplicate(self):
        with mock.patch.object(operations, 'trash_helper', return_value=Path('/synthetic/trash')):
            self.records[0].pop('source_signature')
            with self.assertRaisesRegex(ValueError, '旧报告'):
                self.ops.preview('trash', self.ids[:1])
            self.records[0]['source_signature'] = file_signature(Path(self.records[0]['path']).stat())
            document = {**self.document, 'duplicates': [{'paths': [record['path'] for record in self.records]}]}
            plan = OrganizationPlan(self.report, document)
            self.ops.plan = lambda: plan
            preview = self.ops.preview('trash', self.ids[:1])
            Path(self.records[1]['path']).unlink()  # Generated sample only.
            with self.assertRaisesRegex(ValueError, '保留'):
                self.ops.preview('trash', self.ids[:1])
            with mock.patch.object(operations, 'trash_one') as trash:
                job = self.finished(self.ops.start(preview['token'])['id'])
                trash.assert_not_called()
            self.assertEqual(job['status'], 'stopped')
            self.assertTrue(Path(self.records[0]['path']).is_file())

    def test_controller_cannot_quit_during_file_operations(self):
        operations._BATCH_LOCK.acquire()
        try:
            callback = mock.Mock()
            with self.assertRaisesRegex(ValueError, '等待完成'):
                operations.shutdown_when_idle(callback)
            callback.assert_not_called()
        finally:
            operations._BATCH_LOCK.release()

    def test_live_batch_progress_is_shared_with_another_viewer(self):
        entered, release = threading.Event(), threading.Event()
        original = operations.copy_one
        def paused_copy(*args):
            args[-2](23, 'copying')
            entered.set()
            if not release.wait(5):
                raise ValueError('generated test timed out')
            return original(*args)
        preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
        reopened = operations.FileOperations(self.report, self.media, lambda: self.plan)
        with mock.patch.object(operations, 'copy_one', side_effect=paused_copy):
            job = self.ops.start(preview['token'])
            try:
                self.assertTrue(entered.wait(2))
                snapshot = reopened.snapshot(job['id'])
                self.assertEqual(snapshot['status'], 'running')
                self.assertEqual(snapshot['items'][0]['processed_bytes'], 23)
                self.assertEqual(reopened.history()['jobs'][0], snapshot)
                callback = mock.Mock()
                with self.assertRaisesRegex(ValueError, '等待完成'):
                    reopened.update_plan(callback)
                callback.assert_not_called()
            finally:
                release.set()
                result = self.finished(job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(reopened.snapshot(job['id'])['status'], 'complete')

    def test_another_process_lock_reports_live_status_and_prevents_execution(self):
        identifier = 'f' * 24
        job = {'id': identifier, 'mode': 'copy', 'status': 'running',
               'created_at': '2026-10-04 12:00:00', 'total': 1, 'items': []}
        self.ops._save_job(job)
        lock = self.report/'operations'/'.batch-lock'
        lock.write_text(identifier)
        script = ('import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); '
                  'fcntl.flock(fd,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read(1); '
                  'assert os.path.isfile(sys.argv[2]); print("stop received",flush=True)')
        child = subprocess.Popen([sys.executable, '-c', script, str(lock), str(lock.parent/('.stop-'+identifier))],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            self.assertEqual(self.ops.snapshot(identifier)['status'], 'external_running')
            self.assertTrue(self.ops.request_stop(identifier)['stop_requested'])
            preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
            with self.assertRaisesRegex(ValueError, '另一服务'):
                self.ops.start(preview['token'])
            with self.assertRaisesRegex(ValueError, '另一服务'):
                self.ops.update_plan(lambda: self.plan.set_states(self.ids, 'hold'))
            self.assertEqual(list(self.destination.iterdir()), [])
        finally:
            output, _ = child.communicate('x', timeout=5)
            self.assertIn('stop received', output)
        self.assertEqual(self.ops.snapshot(identifier)['status'], 'interrupted')
        self.assertEqual(self.ops.update_plan(lambda: self.plan.set_states(self.ids, 'hold'))['counts']['hold'], 2)

    def test_corrupt_history_is_visible_without_hiding_valid_records(self):
        preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
        job = self.finished(self.ops.start(preview['token'])['id'])
        directory = self.report/'operations'
        (directory/('a'*24+'.json')).write_text('{broken generated json')
        (directory/('b'*24+'.json')).write_text(json.dumps({
            'id': 'b'*24, 'mode': 'copy', 'status': 'running', 'total': 1, 'items': [None]}))
        reopened = operations.FileOperations(self.report, self.media, lambda: self.plan)
        history = reopened.history()
        self.assertEqual(history['jobs'], [job])
        self.assertEqual(len(history['warnings']), 2)
        self.assertTrue((directory/('a'*24+'.json')).exists())

    def test_plan_is_revalidated_after_acquiring_process_lock(self):
        preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
        original = self.ops._job_lock
        def concurrent_edit():
            self.plan.set_states(self.ids[:1], 'hold')
            return original()
        with mock.patch.object(self.ops, '_job_lock', side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, '分类计划已变化'):
                self.ops.start(preview['token'])
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(operations._BATCH_LOCK.locked())

    def test_thread_start_failure_releases_locks_and_does_not_show_live_job(self):
        preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
        with mock.patch.object(operations.threading.Thread, 'start', side_effect=RuntimeError('generated failure')):
            with self.assertRaises(RuntimeError):
                self.ops.start(preview['token'])
        self.assertEqual(self.ops.history()['jobs'][0]['status'], 'interrupted')
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(operations._BATCH_LOCK.locked())
        self.ops.update_plan(lambda: self.plan.set_states(self.ids, 'hold'))

    def test_completion_during_lock_probe_is_not_reported_as_interrupted(self):
        identifier = 'e'*24
        job = {'id': identifier, 'mode': 'copy', 'status': 'running', 'total': 1, 'items': []}
        self.ops._save_job(job)
        def finished_before_probe(_):
            job.update(status='complete', items=[{'path': self.records[0]['path'],
                'target': str(self.destination/'sample.png'), 'status': 'success'}])
            self.ops._save_job(job)
            return False
        with mock.patch.object(self.ops, '_record_is_active', side_effect=finished_before_probe):
            self.assertEqual(self.ops.snapshot(identifier)['status'], 'complete')

    def test_cancel_copy_during_transfer_and_verification_cleans_only_temporary(self):
        record = self.records[0]
        for phase in ('copying', 'verifying'):
            stop = threading.Event()
            def progress(amount, current):
                if current == phase and amount > 0:
                    stop.set()
            with self.subTest(phase=phase), self.assertRaises(operations.OperationCancelled):
                operations.copy_one(self.media, self.ids[0], str(self.destination), record['suggested_path'],
                    record['source_signature'], progress=progress, cancelled=stop.is_set)
            self.assertEqual(list(self.destination.rglob('*.png')), [])
            self.assertFalse(any(path.name.startswith('.media-copy-') for path in self.destination.rglob('*')))
            self.assertEqual(Path(record['path']).read_bytes(), self.before[record['path']][0])

    def test_stop_before_first_file_keeps_complete_pending_list(self):
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        queued = []
        with mock.patch.object(operations.threading.Thread, 'start', autospec=True, side_effect=queued.append):
            job = self.ops.start(preview['token'])
        try:
            self.assertTrue(self.ops.request_stop(job['id'])['stop_requested'])
            self.assertTrue(self.ops.request_stop(job['id'])['stop_requested'])
        finally:
            queued[0].run()
        result = self.ops.snapshot(job['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['items'], [])
        self.assertEqual([item['path'] for item in result['planned_items']], [record['path'] for record in self.records])
        self.assertFalse(any(self.destination.iterdir()))
        self.assertEqual(self.ops.request_stop(job['id']), result)
        self.assertEqual(operations.FileOperations(self.report, self.media, lambda: self.plan).snapshot(job['id']), result)

    def test_stop_after_copy_is_published_keeps_verified_copy(self):
        record = self.records[0]
        stop = threading.Event()
        def progress(amount, phase):
            if phase == 'verified':
                stop.set()
        result = operations.copy_one(self.media, self.ids[0], str(self.destination), record['suggested_path'],
            record['source_signature'], progress=progress, cancelled=stop.is_set)
        self.assertTrue(stop.is_set())
        self.assertEqual(Path(result['target']).read_bytes(), self.before[record['path']][0])
        self.assertEqual(result['sha256'], record['sha256'])
        self.assertFalse(list(self.destination.rglob('.media-copy-*')))

    def test_stop_from_second_viewer_keeps_completed_copy_and_releases_locks(self):
        entered, release = threading.Event(), threading.Event()
        original = operations.copy_one
        count = 0
        def pause_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
                if not release.wait(5):
                    raise ValueError('generated test timeout')
            return original(*args)
        reopened = operations.FileOperations(self.report, self.media, lambda: self.plan)
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        with mock.patch.object(operations, 'copy_one', side_effect=pause_second):
            job = self.ops.start(preview['token'])
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(reopened.request_stop(job['id'])['stop_requested'])
            finally:
                release.set()
                result = self.finished(job['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual([item['status'] for item in result['items']], ['success', 'cancelled'])
        self.assertEqual(Path(result['items'][0]['target']).read_bytes(), self.before[result['items'][0]['path']][0])
        self.assertFalse(Path(result['items'][1]['target']).exists())
        self.ops.update_plan(lambda: self.plan.set_states(self.ids, 'hold'))

    def test_stop_trash_waits_for_current_system_result_and_preserves_unknown_outcome(self):
        for unknown in (False, True):
            entered, release = threading.Event(), threading.Event()
            def paused_trash(*args):
                entered.set()
                if not release.wait(5):
                    raise ValueError('generated test timeout')
                if unknown:
                    raise operations.OutcomeUnknown('generated uncertain system result')
                return {'trashed_path': '/synthetic/Trash/generated.png'}
            with self.subTest(unknown=unknown), mock.patch.object(operations, 'trash_helper', return_value=Path('/synthetic/trash')):
                preview = self.ops.preview('trash', self.ids)
                with mock.patch.object(operations, 'trash_one', side_effect=paused_trash) as trash:
                    job = self.ops.start(preview['token'])
                    try:
                        self.assertTrue(entered.wait(2))
                        self.ops.request_stop(job['id'])
                    finally:
                        release.set()
                        result = self.finished(job['id'])
                    self.assertEqual(trash.call_count, 1)
            self.assertEqual(result['status'], 'stopped' if unknown else 'cancelled')
            self.assertEqual(result['items'][0]['status'], 'unknown' if unknown else 'success')
            self.assertEqual(len(result['planned_items']), 2)
        self.assertEqual(self.before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.before})

    def test_stop_marker_symlink_is_rejected_and_does_not_touch_target(self):
        preview = self.ops.preview('copy', self.ids[:1], str(self.destination))
        queued = []
        with mock.patch.object(operations.threading.Thread, 'start', autospec=True, side_effect=queued.append):
            job = self.ops.start(preview['token'])
        marker = self.report/'operations'/('.stop-'+job['id'])
        victim = self.root/'generated-unrelated.txt';victim.write_bytes(b'keep this sample')
        marker.symlink_to(victim)
        try:
            with self.assertRaises(OSError):
                self.ops.request_stop(job['id'])
        finally:
            queued[0].run()
        self.assertEqual(self.ops.snapshot(job['id'])['status'], 'stopped')
        self.assertEqual(victim.read_bytes(), b'keep this sample')
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cleanup_failure_is_reported_as_problem_instead_of_safe_stop(self):
        original_copy, original_unlink = operations.copy_one, operations.os.unlink
        def stop_after_transfer(*args):
            progress = args[-2]
            def stop(amount, phase):
                progress(amount, phase)
                if phase == 'copying' and amount > 0:
                    self.ops.request_stop(next(iter(self.ops.jobs)))
            return original_copy(*args[:-2], stop, args[-1])
        def fail_only_generated_temporary(path, *args, **kwargs):
            if str(path).startswith('.media-copy-'):
                raise OSError('generated temporary cleanup failure')
            return original_unlink(path, *args, **kwargs)
        preview = self.ops.preview('copy', self.ids, str(self.destination))
        with mock.patch.object(operations, 'copy_one', side_effect=stop_after_transfer), \
             mock.patch.object(operations.os, 'unlink', side_effect=fail_only_generated_temporary):
            result = self.finished(self.ops.start(preview['token'])['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['items'][0]['status'], 'failed')
        self.assertIn('cleanup failure', result['items'][0]['error'])
        self.assertEqual(len(list(self.destination.rglob('.media-copy-*'))), 1)
        self.assertFalse(Path(result['items'][0]['target']).exists())
        self.assertEqual(self.before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.before})
