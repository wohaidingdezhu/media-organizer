import csv
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest

from organization_plan import MAX_UPDATE_IDS, OrganizationPlan, STATE_FILE


class OrganizationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.document = {"files": [
            {"path": "/synthetic/camera/photo.jpg", "kind": "照片", "bytes": 12,
             "suggested_path": "照片/2024/02/photo.jpg", "reason": "拍摄日期",
             "date_source": "EXIF", "hash_status": "完整 SHA-256 已校验"},
            {"path": "/synthetic/camera/movie.mov", "kind": "视频", "bytes": 24,
             "suggested_path": "视频/旅行/movie.mov", "reason": "来源文件夹"},
            {"path": "/synthetic/backup/photo.jpg", "kind": "照片", "bytes": 12,
             "suggested_path": "照片/2024/02/photo_backup.jpg", "reason": "修改日期",
             "hardlink_to": "/synthetic/camera/photo.jpg"},
        ]}

    def plan(self, document=None):
        return OrganizationPlan(self.directory, document or self.document)

    def test_initial_snapshot_is_read_only_and_groups_target_folders(self):
        snapshot = self.plan().snapshot()
        self.assertEqual(snapshot["counts"], {"total": 3, "pending": 3, "include": 0, "hold": 0})
        self.assertEqual(snapshot["folder_count"], 2)
        self.assertEqual(snapshot["folders"][0]["count"], 2)
        self.assertEqual(snapshot["items"][2]["hardlink_to"], "/synthetic/camera/photo.jpg")
        self.assertEqual(list(self.directory.iterdir()), [])
        reverse = self.plan({"files": list(reversed(self.document["files"]))}).snapshot()
        self.assertEqual({i["path"]: i["id"] for i in snapshot["items"]},
                         {i["path"]: i["id"] for i in reverse["items"]})

    def test_decisions_persist_and_updates_are_private_atomic_and_resettable(self):
        plan = self.plan()
        identifiers = [item["id"] for item in plan.snapshot()["items"]]
        plan.set_states(identifiers[:2], "include")
        result = self.plan().set_states(identifiers[2:], "hold")
        self.assertEqual(result["counts"], {"total": 3, "pending": 0, "include": 2, "hold": 1})
        self.assertEqual(stat.S_IMODE((self.directory / STATE_FILE).stat().st_mode), 0o600)
        self.assertEqual([path.name for path in self.directory.iterdir()], [STATE_FILE])
        result = plan.set_states(identifiers[:1], "pending")
        self.assertEqual(result["counts"]["pending"], 1)
        self.assertEqual(result["counts"]["include"], 1)

    def test_exact_duplicate_groups_are_visible_without_treating_similar_images_as_duplicates(self):
        document = json.loads(json.dumps(self.document))
        paths = [record["path"] for record in document["files"]]
        document["duplicates"] = [{"paths": [paths[0], paths[2]], "sha256": "a" * 64}]
        document["similar"] = {"pairs": [[paths[0], paths[1]]]}
        items = self.plan(document).snapshot()["items"]
        self.assertEqual([item["duplicate_group"] for item in items], [1, None, 1])

    def test_invalid_batch_cannot_partially_update_existing_decisions(self):
        plan = self.plan()
        identifier = plan.snapshot()["items"][0]["id"]
        plan.set_states([identifier], "hold")
        previous = (self.directory / STATE_FILE).read_bytes()
        for ids, state in [([identifier, "unknown"], "include"), ([identifier], "move"),
                           ([], "include"), ([identifier] * (MAX_UPDATE_IDS + 1), "hold"),
                           ([{}], "include"), ([identifier], {})]:
            with self.subTest(ids_type=type(ids), state=state), self.assertRaises(ValueError):
                plan.set_states(ids, state)
            self.assertEqual((self.directory / STATE_FILE).read_bytes(), previous)

    def test_unsafe_and_case_unicode_colliding_targets_cannot_be_included(self):
        targets = ["/absolute.jpg", "../outside.jpg", "safe/../outside.jpg", "a\x00.jpg", "",
                   "C:/outside.jpg", "a\\b.jpg", "a//b.jpg", "a/./b.jpg",
                   "Photo/CAFÉ.jpg", "photo/cafe\u0301.jpg", "folder/file", "folder/file/child.jpg"]
        document = {"files": [{"path": f"/synthetic/{index}.jpg", "suggested_path": target}
                               for index, target in enumerate(targets)]}
        plan = self.plan(document)
        snapshot = plan.snapshot()
        self.assertTrue(all(not item["selectable"] and item["blocked_reason"] for item in snapshot["items"]))
        for item in snapshot["items"]:
            with self.subTest(target=item["suggested_path"]), self.assertRaises(ValueError):
                plan.set_states([item["id"]], "include")
        self.assertEqual(snapshot["folder_count"], 0)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_corrupt_foreign_and_symlink_state_files_are_not_overwritten(self):
        plan = self.plan()
        identifier = plan.snapshot()["items"][0]["id"]
        state_path = self.directory / STATE_FILE
        for raw in [b"{broken", b"[]", json.dumps({"version": 1, "report_id": "other", "states": {}}).encode()]:
            state_path.write_bytes(raw)
            # A damaged state must not prevent opening unrelated report pages.
            other_plan = self.plan()
            with self.assertRaises(ValueError):
                other_plan.snapshot()
            with self.assertRaises(ValueError):
                other_plan.set_states([identifier], "include")
            self.assertEqual(state_path.read_bytes(), raw)
        state_path.unlink()
        target = self.directory / "synthetic-original.jpg"
        target.write_bytes(b"temporary synthetic media bytes")
        before = target.stat()
        state_path.symlink_to(target)
        for action in [plan.snapshot, lambda: plan.set_states([identifier], "include")]:
            with self.assertRaises(OSError):
                action()
        self.assertEqual(target.read_bytes(), b"temporary synthetic media bytes")
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertTrue(state_path.is_symlink())

    def test_saved_ids_must_match_current_report_and_targets(self):
        plan = self.plan()
        identifier = plan.snapshot()["items"][0]["id"]
        plan.set_states([identifier], "include")
        previous = (self.directory / STATE_FILE).read_bytes()
        changed = json.loads(json.dumps(self.document))
        changed["files"][0]["suggested_path"] = "new/photo.jpg"
        with self.assertRaises(ValueError):
            self.plan(changed).snapshot()
        saved = json.loads(previous)
        saved["states"]["f" * 64] = "include"
        (self.directory / STATE_FILE).write_text(json.dumps(saved))
        with self.assertRaises(ValueError):
            plan.export_csv()

    def test_exports_only_included_items_and_escapes_spreadsheet_formulas(self):
        document = json.loads(json.dumps(self.document))
        document["files"][0]["path"] = "=SUM(1,2).jpg"
        document["files"][0]["reason"] = " \t@malicious"
        document["files"][0]["suggested_path"] = "+formula/photo.jpg"
        plan = self.plan(document)
        items = plan.snapshot()["items"]
        plan.set_states([items[0]["id"]], "include")
        plan.set_states([items[1]["id"]], "hold")
        exported = plan.export_csv()
        self.assertTrue(exported.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(exported.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "'=SUM(1,2).jpg")
        self.assertEqual(rows[1][1], "'+formula/photo.jpg")
        self.assertEqual(rows[1][4], "' \t@malicious")
        exported_document = plan.export_document()
        self.assertEqual(exported_document["count"], 1)
        self.assertEqual(exported_document["mode"], "review_only")
        self.assertEqual(exported_document["items"][0]["path"], "=SUM(1,2).jpg")

    def test_concurrent_instances_preserve_each_others_changes(self):
        plans = [self.plan() for _ in range(3)]
        ids = [item["id"] for item in plans[0].snapshot()["items"]]
        errors = []
        def update(plan, identifier):
            try:
                plan.set_states([identifier], "include")
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=update, args=(plan, identifier)) for plan, identifier in zip(plans, ids)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(plans[0].snapshot()["counts"]["include"], 3)

    def test_target_adjustment_persists_exports_and_requires_review_again(self):
        original = self.directory / "original.jpg"
        original.write_bytes(b"generated media")
        before = original.stat().st_mtime_ns
        document = json.loads(json.dumps(self.document))
        document["files"][0]["path"] = str(original)
        plan = self.plan(document)
        identifier = plan.snapshot()["items"][0]["id"]
        plan.set_states([identifier], "include")
        result = plan.set_target(identifier, "照片/家庭/新名称.jpg")
        self.assertEqual(result["counts"]["include"], 0)
        self.assertEqual(result["review"]["edited"], 1)
        item = self.plan(document).snapshot()["items"][0]
        self.assertEqual(item["suggested_path"], "照片/家庭/新名称.jpg")
        self.assertEqual(item["original_suggested_path"], "照片/2024/02/photo.jpg")
        plan.set_states([identifier], "include")
        rows = list(csv.reader(io.StringIO(plan.export_csv().decode("utf-8-sig"))))
        self.assertEqual(rows[1][1], "照片/家庭/新名称.jpg")
        self.assertEqual(rows[1][-2:], ["照片/2024/02/photo.jpg", "True"])
        self.assertEqual(plan.export_document()["items"][0]["target_edited"], True)
        # Re-saving an unchanged target should preserve the review decision.
        self.assertEqual(plan.set_target(identifier, "照片/家庭/新名称.jpg")["counts"]["include"], 1)
        result = plan.set_target(identifier, None)
        self.assertEqual(result["items"][0]["suggested_path"], "照片/2024/02/photo.jpg")
        self.assertEqual(result["items"][0]["state"], "pending")
        self.assertFalse(result["items"][0]["target_edited"])
        self.assertEqual(original.read_bytes(), b"generated media")
        self.assertEqual(original.stat().st_mtime_ns, before)

    def test_invalid_target_edits_are_atomic_and_cannot_conflict_with_included_items(self):
        plan = self.plan()
        ids = [item["id"] for item in plan.snapshot()["items"]]
        plan.set_states([ids[1]], "include")
        before = (self.directory / STATE_FILE).read_bytes()
        for target in ("../outside.jpg", "/outside.jpg", "C:/file.jpg", "a\\b.jpg", "",
                       "a" * 4097, "视频/旅行/MOVIE.mov", "视频/旅行/movie.mov/child.jpg",
                       "视频/旅行", "a\nfile.jpg", [], {}):
            with self.subTest(target=type(target)), self.assertRaises(ValueError):
                plan.set_target(ids[0], target)
            self.assertEqual((self.directory / STATE_FILE).read_bytes(), before)
        with self.assertRaises(ValueError):
            plan.set_target("foreign", "照片/a.jpg")
        self.assertEqual(plan.snapshot()["items"][1]["state"], "include")

    def test_conflicts_can_be_repaired_and_restoring_cannot_break_included_plan(self):
        document = {"files": [
            {"path": "/sample/one.jpg", "suggested_path": "照片/same.jpg"},
            {"path": "/sample/two.jpg", "suggested_path": "照片/SAME.jpg"}]}
        plan = self.plan(document)
        items = plan.snapshot()["items"]
        self.assertEqual(plan.snapshot()["review"]["blocked"], 2)
        result = plan.set_target(items[1]["id"], "照片/two.jpg")
        self.assertEqual(result["review"]["blocked"], 0)
        plan.set_states([item["id"] for item in items], "include")
        before = (self.directory / STATE_FILE).read_bytes()
        with self.assertRaisesRegex(ValueError, "恢复待核对"):
            plan.set_target(items[1]["id"], None)
        self.assertEqual((self.directory / STATE_FILE).read_bytes(), before)
        plan.set_states([items[0]["id"]], "pending")
        result = plan.set_target(items[1]["id"], None)
        self.assertEqual(result["review"]["blocked"], 2)
        self.assertEqual(result["counts"]["include"], 0)

    def test_version_one_progress_upgrades_without_losing_decisions(self):
        plan = self.plan()
        ids = [item["id"] for item in plan.snapshot()["items"]]
        state_path = self.directory / STATE_FILE
        state_path.write_text(json.dumps({"version": 1, "report_id": plan._report_id,
                                          "states": {ids[0]: "include", ids[1]: "hold"}}))
        result = plan.set_target(ids[2], "照片/backup.jpg")
        self.assertEqual(result["counts"]["include"], 1)
        self.assertEqual(result["counts"]["hold"], 1)
        saved = json.loads(state_path.read_bytes())
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["targets"], {ids[2]: "照片/backup.jpg"})
        # State changes from a separate viewer must keep current target edits.
        self.plan().set_states([ids[0]], "hold")
        self.assertEqual(plan.snapshot()["items"][2]["suggested_path"], "照片/backup.jpg")
        plan.set_target(ids[1], "视频/自定义/movie.mov")
        self.assertEqual(plan.snapshot()["items"][2]["suggested_path"], "照片/backup.jpg")

    def test_tampered_saved_targets_are_rejected_without_overwrite(self):
        plan = self.plan()
        identifier = plan.snapshot()["items"][0]["id"]
        plan.set_target(identifier, "照片/new.jpg")
        saved = json.loads((self.directory / STATE_FILE).read_bytes())
        for targets in (["wrong"], {identifier: "../escape.jpg"}, {"foreign": "new.jpg"}):
            saved["targets"] = targets
            raw = json.dumps(saved).encode()
            (self.directory / STATE_FILE).write_bytes(raw)
            for action in (plan.snapshot, lambda: plan.set_target(identifier, None),
                           lambda: plan.set_states([identifier], "include")):
                with self.assertRaises(ValueError):
                    action()
            self.assertEqual((self.directory / STATE_FILE).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
