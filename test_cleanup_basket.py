"""Generated report candidates only; operations never touch source media."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import cleanup_basket as basket
from media_actions import media_id


class BasketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.report = self.root/'report'; self.report.mkdir()
        self.source = self.root/'source'; self.source.mkdir()
        records = []
        for number in range(3):
            path = self.source/f'generated-{number}.png'; path.write_bytes(bytes([number])*32)
            records.append({'path': str(path), 'kind': '照片', 'bytes': 32, 'sha256': str(number)*64})
        self.document = {'files': records}
        self.ids = [media_id(item['path']) for item in records]
        self.store = basket.CleanupBasket(self.report, self.document)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.source.iterdir()}
        self.addCleanup(lambda: self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.source.iterdir()}))

    def test_reopen_and_second_viewer_merge_updates_without_changing_media(self):
        self.assertEqual(self.store.snapshot()['ids'], [])
        self.assertFalse(any(self.report.iterdir()))
        self.store.update('add', self.ids[:1])
        second = basket.CleanupBasket(self.report, self.document)
        second.update('add', self.ids[1:])
        self.store.update('remove', self.ids[:1])
        self.assertEqual(second.snapshot()['ids'], sorted(self.ids[1:]))
        state = self.report/basket.STATE_FILE
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        second.update('clear', [])
        self.assertEqual(self.store.snapshot()['ids'], [])

    def test_unknown_duplicate_and_oversized_updates_are_atomic(self):
        self.store.update('add', self.ids[:1]); state = self.report/basket.STATE_FILE
        before = state.read_bytes()
        for action, ids in [('add', self.ids+[media_id('/synthetic/not-in-report.png')]), ('add', self.ids*2),
                            ('clear', self.ids), ('wrong', []), ('remove', [None])]:
            with self.subTest(action=action, ids=ids), self.assertRaises(ValueError):
                self.store.update(action, ids)
            self.assertEqual(state.read_bytes(), before)
        document = {'files': self.document['files']+[{'path': str(self.source/f'only-reported-{number}.mp4'), 'kind': '视频', 'bytes': number}
                                                    for number in range(201)]}
        other_report = self.root/'other-report'; other_report.mkdir()
        large = basket.CleanupBasket(other_report, document)
        large.update('add', [media_id(item['path']) for item in document['files'][:200]])
        old = (other_report/basket.STATE_FILE).read_bytes()
        with self.assertRaises(ValueError):
            large.update('add', [media_id(document['files'][200]['path'])])
        self.assertEqual((other_report/basket.STATE_FILE).read_bytes(), old)

    def test_damaged_mismatched_and_symlink_records_are_preserved(self):
        self.store.update('add', self.ids[:1]); state = self.report/basket.STATE_FILE
        original = state.read_bytes()
        state.write_bytes(b'{generated broken json')
        with self.assertRaises(ValueError):
            self.store.update('clear', [])
        self.assertEqual(state.read_bytes(), b'{generated broken json')
        state.write_bytes(original)
        changed = json.loads(json.dumps(self.document)); changed['files'][0]['bytes'] += 1
        with self.assertRaises(ValueError):
            basket.CleanupBasket(self.report, changed).update('add', self.ids[1:])
        self.assertEqual(state.read_bytes(), original)
        victim = self.root/'generated-unrelated.json'; victim.write_bytes(original)
        state.unlink(); state.symlink_to(victim)
        with self.assertRaises(OSError):
            self.store.update('clear', [])
        self.assertEqual(victim.read_bytes(), original)

    def test_failed_publication_keeps_prior_candidates_and_removes_own_temporary(self):
        self.store.update('add', self.ids[:1]); state = self.report/basket.STATE_FILE
        before = state.read_bytes()
        with mock.patch.object(basket.os, 'replace', side_effect=OSError('generated publication failure')), self.assertRaises(OSError):
            self.store.update('add', self.ids[1:])
        self.assertEqual(state.read_bytes(), before)
        self.assertFalse(list(self.report.glob('.cleanup-basket-temp-*')))

    def test_separate_processes_preserve_both_manual_additions(self):
        code = "from cleanup_basket import CleanupBasket; import json,sys; CleanupBasket(sys.argv[1],json.loads(sys.argv[2])).update('add',[sys.argv[3]])"
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.report), json.dumps(self.document), identifier],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for identifier in self.ids[:2]]
        for process in processes:
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors.decode())
        self.assertEqual(self.store.snapshot()['ids'], sorted(self.ids[:2]))

    def test_symlink_lock_is_rejected_without_modifying_unrelated_file(self):
        victim = self.root/'generated-unrelated.txt'; victim.write_bytes(b'keep generated data')
        (self.report/'.cleanup-basket-lock').symlink_to(victim)
        with self.assertRaises(OSError):
            self.store.update('add', self.ids)
        self.assertEqual(victim.read_bytes(), b'keep generated data')
        self.assertFalse((self.report/basket.STATE_FILE).exists())

    def test_ambiguous_report_records_do_not_replace_existing_candidates(self):
        self.store.update('add', self.ids[:1]); state = self.report/basket.STATE_FILE
        before = state.read_bytes()
        for change in ({'kind': []}, {'bytes': True}, {'path': '/synthetic/../outside.png'}):
            document = json.loads(json.dumps(self.document)); document['files'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                basket.CleanupBasket(self.report, document)
        document = json.loads(json.dumps(self.document)); document['files'].append(document['files'][0])
        with self.assertRaises(ValueError):
            basket.CleanupBasket(self.report, document)
        self.assertEqual(state.read_bytes(), before)
