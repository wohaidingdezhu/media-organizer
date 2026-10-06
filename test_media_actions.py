from test_support import make_symlink
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import media_actions as actions


class MediaActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / '影片 `literal` $(literal).mp4'
        self.source.write_bytes(b"temporary synthetic video bytes")
        info = self.source.stat()
        self.record = {"path": str(self.source), "kind": "视频", "bytes": info.st_size,
                       "mtime": info.st_mtime, "source_signature": actions.file_signature(info)}
        self.document = {"roots": [str(self.root)], "files": [self.record]}
        self.identifier = actions.media_id(str(self.source))

    def test_native_open_and_reveal_use_only_allowed_scanned_file_without_shell(self):
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        manager = actions.MediaActions(self.document)
        with mock.patch.object(actions.sys, "platform", "darwin"), mock.patch.object(actions.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(manager.perform(self.identifier, "open")["ok"])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", str(self.source)])
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertTrue(manager.perform(self.identifier, "reveal")["ok"])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-R", str(self.source)])
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime_ns), before)

    def test_unknown_non_media_outside_scope_and_unsupported_actions_are_rejected(self):
        manager = actions.MediaActions(self.document)
        with mock.patch.object(actions.sys, "platform", "darwin"), mock.patch.object(actions.subprocess, "run") as run:
            for identifier, action in (("foreign", "open"), (self.identifier, "delete"),
                                       (self.identifier, {}), ({}, "open")):
                with self.assertRaises(ValueError):
                    manager.perform(identifier, action)
            run.assert_not_called()
        for changed in ({**self.record, "path": "/outside/movie.mp4"},
                        {**self.record, "path": str(self.root / "program.command")},
                        {**self.record, "bytes": True}, {**self.record, "mtime": float("nan")},
                        {**self.record, "kind": "其他"}):
            self.assertFalse(actions.MediaActions({"roots": [str(self.root)], "files": [changed]}).records)

    def test_moved_changed_or_replaced_files_require_a_new_scan(self):
        manager = actions.MediaActions(self.document)
        original_mtime = self.source.stat().st_mtime_ns
        self.source.unlink()
        with self.assertRaises(ValueError):
            manager.validate(self.identifier)
        self.source.write_bytes(b"temporary synthetic video bytes")
        os.utime(self.source, ns=(original_mtime, original_mtime))
        # Same bytes, size and mtime still do not restore the original identity.
        with self.assertRaises(ValueError):
            manager.validate(self.identifier)

    def test_symlinked_file_or_parent_is_never_opened(self):
        manager = actions.MediaActions(self.document)
        target = self.root / "other.mp4"
        target.write_bytes(b"temporary synthetic video bytes")
        self.source.unlink()
        make_symlink(self.source, target)
        with self.assertRaises(ValueError):
            manager.validate(self.identifier)
        parent = self.root / "folder"
        parent.mkdir()
        nested = parent / "nested.mp4"
        nested.write_bytes(b"generated")
        info = nested.stat()
        record = {"path": str(nested), "kind": "视频", "bytes": info.st_size, "mtime": info.st_mtime}
        manager = actions.MediaActions({"roots": [str(self.root)], "files": [record]})
        parent.rename(self.root / "moved")
        make_symlink(parent, self.root / "moved", target_is_directory=True)
        with self.assertRaises(ValueError):
            manager.validate(actions.media_id(str(nested)))

    def test_legacy_reports_check_size_and_mtime_and_native_failures_are_visible(self):
        record = {key: value for key, value in self.record.items() if key != "source_signature"}
        manager = actions.MediaActions({"roots": [str(self.root)], "files": [record]})
        manager.validate(self.identifier)
        with mock.patch.object(actions.sys, "platform", "darwin"), mock.patch.object(actions.subprocess, "run") as run:
            run.return_value.returncode = 1
            with self.assertRaises(ValueError):
                manager.perform(self.identifier, "open")
            run.side_effect = subprocess.TimeoutExpired("open", 15)
            with self.assertRaises(ValueError):
                manager.perform(self.identifier, "open")
        self.source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            manager.validate(self.identifier)

    def test_missing_legacy_fields_disable_actions_without_breaking_other_views(self):
        for document in ({}, {"roots": None, "files": []}, {"files": [{}]}, {"roots": [], "files": None}):
            manager = actions.MediaActions(document)
            self.assertEqual(manager.snapshot()["ids_by_path"], {})


if __name__ == "__main__":
    unittest.main()
