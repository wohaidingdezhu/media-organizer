"""A slow bundle test stops safely and still reports a real timeout."""
import unittest
from unittest import mock

import test_photo_bundle


class PhotoBundleWaitTests(unittest.TestCase):
    def test_timeout_requests_stop_waits_for_worker_and_fails(self):
        ops = mock.Mock()
        ops.snapshot.side_effect = [{'status': 'running'}, {'status': 'cancelled'}]
        with mock.patch.object(test_photo_bundle.time, 'monotonic', side_effect=[0, 0, 61, 61, 61]), \
             mock.patch.object(test_photo_bundle.time, 'sleep') as sleep:
            with self.assertRaisesRegex(AssertionError, 'timed out; stop requested'):
                test_photo_bundle.PhotoBundleTests.wait(self, ops, {'id': 'generated'})
        ops.request_stop.assert_called_once_with('generated')
        self.assertEqual(ops.snapshot.call_count, 2)
        sleep.assert_called_once_with(.05)
