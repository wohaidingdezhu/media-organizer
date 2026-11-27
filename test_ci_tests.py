"""The CI runner must preserve coverage and fail visibly on every failure path."""
from collections import Counter
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ci_tests


class CIRunnerTests(unittest.TestCase):
    def run_generated(self, modules, workers=2):
        with tempfile.TemporaryDirectory() as temp:
            for name, source in modules.items():
                (Path(temp) / (name + '.py')).write_text(source, encoding='utf-8')
            return subprocess.run([sys.executable, '-X', 'utf8', str(Path(ci_tests.__file__)),
                                   '--workers', str(workers), '--root', temp],
                                  capture_output=True, text=True, encoding='utf-8', timeout=30)

    def test_partition_preserves_all_ids_including_duplicates_and_module_fixtures(self):
        names = ['test_a.Example.test_%d' % number for number in range(5)]
        names += ['test_b.Example.test_one', 'test_b.Example.test_one', 'test_c.Example.test_one']
        groups = ci_tests.partition(names, 2)
        self.assertEqual(len(groups), 2)
        self.assertEqual(Counter(name for group in groups for name in group), Counter(names))
        for module in ('test_a', 'test_b', 'test_c'):
            self.assertEqual(sum(any(name.startswith(module + '.') for name in group) for group in groups), 1)

    def test_both_processes_run_and_preserve_platform_skip_reporting(self):
        result = self.run_generated({
            'test_generated_a': "import unittest\nclass Generated(unittest.TestCase):\n def test_photo(self): self.assertTrue(True)\n",
            'test_generated_b': "import unittest\nclass Generated(unittest.TestCase):\n @unittest.skip('generated platform reason')\n def test_video(self): pass\n",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for text in ('test_photo', 'test_video', 'generated platform reason', 'Worker 1:', 'Worker 2:', '2 tests run, 1 skipped'):
            self.assertIn(text, result.stdout)

    def test_failed_test_does_not_skip_other_worker_and_cannot_pass(self):
        result = self.run_generated({
            'test_generated_a': "import unittest\nclass Generated(unittest.TestCase):\n def test_failure(self): self.fail('visible failure')\n",
            'test_generated_b': "import unittest\nclass Generated(unittest.TestCase):\n def test_other_worker(self): pass\n",
        })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for text in ('visible failure', 'test_other_worker', '2 tests run, 0 skipped', 'FAIL'):
            self.assertIn(text, result.stdout)

    def test_discovery_error_and_empty_suite_are_failures(self):
        for modules, message in (({'test_generated_error': "raise RuntimeError('broken discovery')"}, 'broken discovery'),
                                 ({}, 'No tests discovered')):
            with self.subTest(message=message):
                result = self.run_generated(modules)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)

    def test_worker_exception_is_not_reported_as_success(self):
        result = self.run_generated({'test_generated_exit':
            "import os, unittest\nclass Generated(unittest.TestCase):\n def test_worker_exit(self): os._exit(7)\n"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Worker 1 failed', result.stderr)
        self.assertIn('FAIL', result.stdout)

    def test_single_worker_uses_the_same_discovery(self):
        result = self.run_generated({'test_generated_one':
            "import unittest\nclass Generated(unittest.TestCase):\n def test_one(self): pass\n"}, workers=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('1 tests run, 0 skipped', result.stdout)


if __name__ == '__main__':
    unittest.main()
