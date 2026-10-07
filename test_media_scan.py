from test_support import make_symlink
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import zlib

import media_scan as scan
import media_gui
from library_server import clean_tags, load_tags, save_tags


def sample_png(path, marker):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + bytes((x * 31 + y * 17 + (x // 4 % 2) * 70) % 256 for x in range(32)) for y in range(32))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 0, 0, 0, 0))
                     + chunk(b"tEXt", b"Comment\0" + marker.encode()) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class MediaTests(unittest.TestCase):
    def test_graphical_launcher_uses_selected_folders_and_latest_sample_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first, second = base / "one", base / "two"
            first.mkdir()
            second.mkdir()
            args = media_gui.scan_arguments([first, second], image_analysis=False,
                                            video_headers=True, video_rule="folder")
            self.assertEqual(args[1:3], [str(first), str(second)])
            self.assertIn("--no-image-metadata", args)
            self.assertIn("--check-video-headers", args)
            self.assertEqual(args[-2:], ["--no-image-metadata", "--check-video-headers"])
            self.assertIsNone(media_gui.latest_library(base))
            for name in ("scan-20260101", "scan-20260102"):
                report = base / name
                report.mkdir()
                (report / "library.html").write_text("sample")
                (report / "report.json").write_text("{}")
            self.assertEqual(media_gui.latest_library(base).name, "scan-20260102")

    def test_end_to_end_read_only_duplicates_and_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "media", base / "reports"
            source.mkdir()
            (source / "ABC-123-CD1.mp4").write_bytes(b"movie-one")
            (source / "copy.mkv").write_bytes(b"movie-one")
            (source / "ABC-123-CD2.mp4").write_bytes(b"movie-two")
            (source / "IMG_20240229.jpg").write_bytes(b"not-a-real-photo")
            (source / "empty.mov").touch()
            (source / ".hidden.mp4").write_bytes(b"movie-one")
            os.link(source / "ABC-123-CD1.mp4", source / "hard.mp4")
            make_symlink(source / "link.mp4", source / "ABC-123-CD1.mp4")
            library = source / "Photos.photoslibrary"
            library.mkdir()
            (library / "private.mp4").write_bytes(b"movie-one")
            before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
            original_umask = os.umask(0o077)
            os.umask(original_umask)
            with contextlib.redirect_stdout(io.StringIO()):
                code = scan.main([str(source), str(source), "--output", str(output), "--no-image-metadata"])
            self.assertEqual(os.umask(original_umask), original_umask)
            self.assertEqual(code, 0)
            report = next(output.glob("*/report.json"))
            data = json.loads(report.read_text())
            self.assertEqual(len(data["roots"]), 1)
            self.assertEqual(data["summary"]["duplicate_groups"], 1)
            self.assertEqual(len(data["duplicates"][0]["paths"]), 2)
            self.assertEqual(data["summary"]["hardlinks"], 1)
            self.assertEqual(data["summary"]["redundant_logical_bytes"], 9)
            self.assertEqual(data["summary"]["files"], 6)
            self.assertTrue(any("照片/2024/02" in r["suggested_path"] for r in data["files"]))
            self.assertFalse(data["similar"]["enabled"])
            self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()})
            self.assertEqual(len(list(report.parent.glob("*"))), 12)

    def test_same_folder_names_are_reported_and_suggested_paths_stay_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first, second = base / "one" / "Movies", base / "two" / "Movies"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            first_name, second_name = ("Trip:2024", "Trip?2024") if os.name != "nt" else ("Trip" + "a" * 97 + "A", "Trip" + "a" * 97 + "B")
            for name, filename in [(first_name, "clip-a.mp4"), (second_name, "clip-b.mp4")]:
                folder = first / name
                folder.mkdir()
                (folder / filename).write_bytes(filename.encode())
            (first / first_name / "clip-a.srt").write_bytes(b"sample subtitle")
            (second / "clip-c.mp4").write_bytes(b"third video")
            originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for root in (first, second) for p in root.rglob("*.mp4")}
            with contextlib.redirect_stdout(io.StringIO()):
                code = scan.main([str(first), str(second), "--output", str(base / "reports"),
                                  "--video-rule", "folder", "--no-image-metadata"])
            self.assertEqual(code, 0)
            report = next((base / "reports").glob("scan-*/report.json"))
            data = json.loads(report.read_text())
            self.assertEqual({g["type"] for g in data["folder_groups"]}, {"同名", "整理后名称冲突"})
            targets = {Path(r["path"]).name: Path(r["suggested_path"]).parts for r in data["files"]}
            self.assertNotEqual(targets["clip-a.mp4"][2], targets["clip-c.mp4"][2])
            self.assertNotEqual(targets["clip-a.mp4"][3], targets["clip-b.mp4"][3])
            self.assertIn("文件夹名称冲突", next(r["reason"] for r in data["files"] if r["name"] == "clip-a.mp4"))
            self.assertEqual(len((report.parent / "folder_names.csv").read_text().splitlines()), 5)
            collision = next(g for g in data["folder_groups"] if g["type"] == "整理后名称冲突")
            self.assertEqual(next(f for f in collision["folders"] if f["path"].endswith(first_name))["sidecar_files"], 1)
            self.assertIn("包含附属文件项", (report.parent / "folder_names.csv").read_text().splitlines()[0])
            self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})

    def test_same_folder_media_content_requires_complete_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            roots = [base / name / "Album" for name in ("one", "two", "three", "four")]
            for root in roots:
                root.mkdir(parents=True)
            roots = [root.resolve() for root in roots]
            sample_png(roots[0] / "photo.png", "same")
            sample_png(roots[1] / "renamed.png", "same")
            sample_png(roots[2] / "photo.png", "same")
            (roots[0] / "clip.mp4").write_bytes(b"video-A")
            (roots[1] / "renamed.mov").write_bytes(b"video-A")
            (roots[2] / "clip.mp4").write_bytes(b"video-B")
            (roots[3] / "empty.mp4").touch()
            originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for root in roots for p in root.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                code = scan.main([*(str(root) for root in roots), "--output", str(base / "reports"),
                                  "--no-image-metadata"])
            self.assertEqual(code, 0)
            report_dir = next((base / "reports").glob("scan-*"))
            data = json.loads((report_dir / "report.json").read_text())
            self.assertEqual(data["version"], 10)
            group = next(group for group in data["folder_groups"] if group["name"] == "Album")
            folders = {folder["path"]: folder for folder in group["folders"]}
            self.assertEqual(folders[str(roots[0])]["content_match_example"], str(roots[1]))
            self.assertEqual(folders[str(roots[1])]["content_match_example"], str(roots[0]))
            self.assertIn("已确认", folders[str(roots[0])]["content_check"])
            self.assertFalse(folders[str(roots[2])]["content_match_example"])
            self.assertIn("未发现", folders[str(roots[2])]["content_check"])
            self.assertFalse(folders[str(roots[3])]["content_match_example"])
            self.assertIn("未确认", folders[str(roots[3])]["content_check"])
            self.assertIn("匹配示例", (report_dir / "report.html").read_text())
            self.assertIn("匹配文件夹示例", (report_dir / "folder_names.csv").read_text())
            self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})

    def test_video_library_browse_and_review_use_only_sample_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "media", base / "reports"
            source.mkdir()
            for name in ("First", "Second"):
                folder = source / name
                folder.mkdir()
                (folder / "Holiday.mp4").write_bytes(name.encode())
            good_header = b"\x00\x00\x00\x18ftypisom" + b"\0" * 12
            (source / "ABC-123-CD1.mp4").write_bytes(good_header)
            (source / "ABC-123-CD2.mp4").write_bytes(good_header)
            sample_png(source / "ABC-123.png", "poster-A")
            sample_png(source / "First" / "folder.png", "poster-B")
            sample_png(source / "folder.png", "ambiguous-folder-poster")
            (source / "bad.mp4").write_bytes(b"invalid video header")
            (source / "bad.en.srt").write_bytes(b"subtitle")
            (source / "orphan.srt").write_bytes(b"orphan subtitle")
            originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main([str(source), "--output", str(output), "--check-video-headers"]), 0)
            report_dir = next(output.glob("scan-*"))
            data = json.loads((report_dir / "report.json").read_text())
            library = data["video_library"]
            self.assertEqual(library["video_files"], 5)
            self.assertEqual(library["duplicate_files"], 2)
            self.assertEqual(len(library["groups"]), 4)
            self.assertEqual(library["poster_count"], 2)
            self.assertTrue(all((report_dir / group["poster"]).is_file() for group in library["groups"] if group["poster"]))
            self.assertFalse(next(group for group in library["groups"] if group["title"] == "bad")["poster"])
            self.assertEqual(len([group for group in library["groups"] if group["title"] == "Holiday"]), 2)
            self.assertTrue(any("orphan.srt" in item["path"] for item in library["issues"]))
            self.assertTrue(any("bad.mp4" in item["path"] and "文件头" in item["reason"] for item in library["issues"]))
            self.assertTrue(any(file["sidecars"] for group in library["groups"] for file in group["files"]))
            page = (report_dir / "library.html").read_text()
            self.assertIn("搜索影片、路径、标签和附属文件", page)
            self.assertIn("tag-editor", page)
            self.assertNotIn("prompt(", page)
            self.assertIn("library_issues.csv", page)
            self.assertIn("打开影片资料库", (report_dir / "report.html").read_text())
            self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})

    def test_video_library_embedded_data_escapes_script_markup(self):
        title = "</script><script>alert(1)</script>"
        data = {"created_at": "2026-10-02", "video_library": {"groups": [{"type": "名称", "title": title,
                "files": [], "needs_review": False, "has_sidecars": False}], "issues": [], "video_files": 0,
                "duplicate_files": 0}}
        page = scan.render_video_library(data)
        self.assertNotIn(title, page)
        self.assertIn("\\u003c/script\\u003e", page)

    def test_tags_survive_new_scan_without_touching_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "media", root / "reports"
            source.mkdir()
            video = source / "ABC-123.mp4"
            video.write_bytes(b"sample film")
            original = (video.read_bytes(), video.stat().st_mtime_ns)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main([str(source), "--output", str(output), "--no-image-metadata"]), 0)
            first = next(output.glob("scan-*/report.json"))
            key = json.loads(first.read_text())["video_library"]["groups"][0]["tag_key"]
            save_tags(output / "library-tags.json", {key: ["收藏", "周末"]})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main([str(source), "--output", str(output), "--no-image-metadata"]), 0)
            latest = sorted(output.glob("scan-*/report.json"))[-1]
            self.assertEqual(json.loads(latest.read_text())["video_library"]["groups"][0]["tags"], ["收藏", "周末"])
            self.assertEqual(load_tags(output / "library-tags.json")[key], ["收藏", "周末"])
            self.assertEqual((video.read_bytes(), video.stat().st_mtime_ns), original)
            self.assertEqual(clean_tags([" 收藏 ", "收藏", "周末"]), ["收藏", "周末"])
            with self.assertRaises(ValueError):
                clean_tags(["bad\nname"])

    def test_sidecars_are_read_only_and_ambiguous_matches_stay_unresolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "media", base / "reports"
            source.mkdir()
            sample_png(source / "IMG_1.png", "A")
            (source / "IMG_1.jpg").write_bytes(b"sample photo")
            (source / "Film.mp4").write_bytes(b"sample video")
            (source / "Film.mkv").write_bytes(b"alternate video")
            (source / "Film.en.mp4").write_bytes(b"language title")
            (source / "Film-C.mp4").write_bytes(b"subtitle variant")
            (source / "IMG_1.xmp").write_bytes(b"<xmp>sample</xmp>")
            (source / "IMG_1.png.aae").write_bytes(b"sample edit")
            (source / "Film.zh-CN.srt").write_bytes(b"sample subtitle")
            (source / "Film.mp4.nfo").write_bytes(b"sample info")
            (source / "Film.mp4.zh-CN.srt").write_bytes(b"sample subtitle 2")
            (source / "Film.mp4.ja.forced.srt").write_bytes(b"sample subtitle 3")
            (source / "Film.en.srt").write_bytes(b"sample subtitle 4")
            (source / "Orphan.srt").write_bytes(b"orphan")
            make_symlink(source / "linked.srt", source / "Orphan.srt")
            originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir() if p.is_file() and not p.is_symlink()}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main([str(source), "--output", str(output), "--no-image-metadata"]), 0)
            report_dir = next(output.glob("scan-*"))
            data = json.loads((report_dir / "report.json").read_text())
            sidecars = {Path(item["path"]).name: item for item in data["sidecars"]}
            self.assertEqual(set(sidecars), {"IMG_1.xmp", "IMG_1.png.aae", "Film.zh-CN.srt", "Film.mp4.nfo",
                                             "Film.mp4.zh-CN.srt", "Film.mp4.ja.forced.srt", "Film.en.srt", "Orphan.srt"})
            self.assertEqual(sidecars["IMG_1.xmp"]["status"], "多项候选：需人工确认")
            self.assertEqual(sidecars["IMG_1.xmp"]["media_suggested_path"], "")
            self.assertEqual(Path(sidecars["IMG_1.png.aae"]["media_paths"][0]).name, "IMG_1.png")
            self.assertEqual(sidecars["Film.zh-CN.srt"]["status"], "多项候选：需人工确认")
            self.assertEqual(Path(sidecars["Film.mp4.zh-CN.srt"]["media_paths"][0]).name, "Film.mp4")
            self.assertEqual(Path(sidecars["Film.mp4.ja.forced.srt"]["media_paths"][0]).name, "Film.mp4")
            self.assertEqual(Path(sidecars["Film.en.srt"]["media_paths"][0]).name, "Film.en.mp4")
            self.assertEqual(sidecars["Orphan.srt"]["status"], "未关联")
            self.assertEqual(data["summary"]["files"], 6)
            self.assertIn("附属文件关联", (report_dir / "report.html").read_text())
            self.assertEqual(len((report_dir / "sidecars.csv").read_text().splitlines()), 9)
            self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})

    def test_video_filename_variants_are_only_labels(self):
        self.assertIn("字幕变体", scan.video_variant("ABC-123-C"))
        self.assertIn("第 2 段", scan.video_variant("ABC-123-DISC2"))
        self.assertIn("第 2 段", scan.video_variant("ABC-123-DISC2-1080p"))
        self.assertEqual(scan.video_title("Holiday-DISC2-1080p"), "Holiday")
        self.assertEqual(scan.video_variant("holiday-C"), "")

    def test_nested_same_name_folders_do_not_match_themselves(self):
        root = Path("/temporary/Album")
        child = root / "Album"
        record = {"root": str(root), "path": str(child / "movie.mp4"),
                  "bytes": 5, "sha256": "a" * 64}
        group = scan.related_folders([str(root), str(child)], [record])[0]
        self.assertTrue(all(not folder["content_match_example"] for folder in group["folders"]))
        peer = Path("/another/Album")
        peer_record = {**record, "root": str(peer), "path": str(peer / "renamed.mov")}
        group = scan.related_folders([str(root), str(child), str(peer)], [record, peer_record])[0]
        folders = {folder["path"]: folder for folder in group["folders"]}
        self.assertEqual(folders[str(root)]["content_match_example"], str(peer))
        self.assertEqual(folders[str(child)]["content_match_example"], str(peer))
        self.assertIn(folders[str(peer)]["content_match_example"], (str(root), str(child)))

    def test_image_previews_and_video_groups_use_sample_files_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "media", base / "reports"
            source.mkdir()
            for marker in "ABCDEF":
                sample_png(source / f"sample-{marker}.png", marker)
            (source / "ABC-123-CD1-1080p.mp4").write_bytes(b"part-one")
            (source / "ABC-123-CD2-720p.mkv").write_bytes(b"part-two")
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                code = scan.main([str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            report_dir = next(output.glob("scan-*"))
            data = json.loads((report_dir / "report.json").read_text())
            self.assertEqual(data["summary"]["duplicate_groups"], 0)
            self.assertEqual(len(data["similar"]["pairs"]), 15)
            self.assertEqual(len(data["previews"]), 6)
            self.assertTrue(all((report_dir / relative).is_file() for relative in data["previews"].values()))
            self.assertEqual(len(data["video_groups"]), 1)
            self.assertEqual(data["video_groups"][0]["label"], "ABC-123")
            self.assertTrue(all("段标记" in file["variant"] for file in data["video_groups"][0]["files"]))
            self.assertTrue(all(file["modified_at"] and file["hash_status"] for file in data["video_groups"][0]["files"]))
            self.assertEqual(len((report_dir / "video_groups.csv").read_text().splitlines()), 3)
            page = (report_dir / "report.html").read_text()
            self.assertIn("<img loading='lazy' src='previews/", page)
            self.assertIn("筛选全部分类建议", page)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})

    def test_image_worker_reuses_process_after_unreadable_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first, second, broken = base / "first.png", base / ("中文 照片.png" if os.name == "nt" else "中文\n照片.png"), base / "broken.png"
            sample_png(first, "A")
            sample_png(second, "B")
            broken.write_bytes(b"not an image")
            worker = scan.ImageProbeWorker(scan.media_backend.helper("image_probe"))
            try:
                self.assertIsNotNone(worker.request(str(first))["dhash"])
                process_id = worker.process.pid
                with self.assertRaises(ValueError):
                    worker.request(str(broken))
                self.assertIsNotNone(worker.request(str(second))["dhash"])
                self.assertEqual(worker.process.pid, process_id)
            finally:
                worker.close()

    def test_image_worker_repeated_descriptor_reads_keep_all_previews(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            worker = scan.ImageProbeWorker(scan.media_backend.helper("image_probe"))
            try:
                process_id = None
                for index in range(60):
                    source = base / f"generated-{index}.png"
                    destination = base / f"preview-{index}.png"
                    sample_png(source, str(index))
                    before = source.read_bytes(), source.stat().st_mtime_ns
                    self.assertIsNotNone(worker.request(str(source))["dhash"])
                    worker.request(str(source), thumbnail=str(destination))
                    self.assertTrue(destination.is_file())
                    self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
                    if process_id is None:
                        process_id = worker.process.pid
                    self.assertEqual(worker.process.pid, process_id)
            finally:
                worker.close()

    def test_image_worker_restarts_after_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper = Path(temporary) / "slow_helper.py"
            helper.write_text("#!/usr/bin/env python3\nimport json, sys, time\nfor line in sys.stdin:\n request = json.loads(line)\n if request['path'] == 'slow': time.sleep(5)\n print(json.dumps({'path': request['path']}), flush=True)\n")
            helper.chmod(0o700)
            worker = scan.ImageProbeWorker(helper)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    worker.request("slow", timeout=0.05)
                self.assertEqual(worker.request("fast", timeout=5)["path"], "fast")
            finally:
                worker.close()

    def test_image_worker_times_out_on_partial_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper = Path(temporary) / "partial_helper.py"
            helper.write_text("#!/usr/bin/env python3\nimport json, sys, time\nfor line in sys.stdin:\n request = json.loads(line)\n if request['path'] == 'partial':\n  sys.stdout.write('{\"path\":')\n  sys.stdout.flush()\n  time.sleep(2)\n  print('\"partial\"}', flush=True)\n else:\n  print(json.dumps({'path': request['path']}), flush=True)\n")
            helper.chmod(0o700)
            worker = scan.ImageProbeWorker(helper)
            try:
                started = time.monotonic()
                with self.assertRaises(subprocess.TimeoutExpired):
                    worker.request("partial", timeout=0.1)
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertEqual(worker.request("fast", timeout=5)["path"], "fast")
            finally:
                worker.close()

    def test_failed_image_preview_leaves_no_partial_report_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "sample.png"
            sample_png(source, "preview")
            report = base / "report"
            report.mkdir()
            record = {"path": str(source), "_signature": scan.signature(source.stat())}
            class PartialPreviewWorker:
                def __init__(self, helper):
                    pass
                def request(self, path, thumbnail=None):
                    Path(thumbnail).write_bytes(b"partial")
                    raise ValueError("sample decode failure")
                def close(self):
                    pass
            issues = []
            with mock.patch.object(scan, "ImageProbeWorker", PartialPreviewWorker):
                previews = scan.export_previews(report, [record],
                                                {"pairs": [{"left": str(source), "right": str(source)}]},
                                                base / "unused", issues)
            self.assertEqual(previews, {})
            self.assertEqual(list((report / "previews").iterdir()), [])
            self.assertEqual(len(issues), 1)
            self.assertTrue(source.is_file())

    def test_photo_wall_previews_work_without_similar_candidates_and_respect_limit(self):
        helper = scan.media_backend.helper('image_probe')
        if not scan.helper_available(helper):
            self.skipTest('Image backend unavailable; CI prepares the backend on both platforms')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = root/'report';report.mkdir()
            records, before = [], {}
            for index in range(2):
                source = root/f'photo-{index}.png'
                sample_png(source, str(index))
                before[str(source)] = (source.read_bytes(), source.stat().st_mtime_ns)
                records.append({'path': str(source), 'kind': '照片', '_signature': scan.signature(source.stat())})
            issues = []
            previews = scan.export_previews(report, records, {'pairs': []}, helper, issues, photo_limit=1)
            self.assertEqual(len(previews), 1)
            self.assertTrue((report/next(iter(previews.values()))).is_file())
            self.assertEqual(issues, [])
            self.assertEqual(before, {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in before})

    def test_image_backend_rejects_symlink_source_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source.png"
            sample_png(source, "A")
            original = source.read_bytes()
            linked_source, linked_output = base / "linked.png", base / "preview.png"
            make_symlink(linked_source, source)
            make_symlink(linked_output, source)
            helper = scan.media_backend.helper("image_probe")
            self.assertFalse(scan.helper_available(linked_source))
            worker = scan.ImageProbeWorker(helper)
            try:
                with self.assertRaises((OSError, ValueError)):
                    worker.request(str(linked_source))
                with self.assertRaises((OSError, ValueError)):
                    worker.request(str(source), str(linked_output))
            finally:
                worker.close()
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(linked_output.is_symlink())

    def test_changed_file_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.mp4"
            path.write_bytes(b"first")
            record = {"path": str(path), "name": path.name, "_signature": scan.signature(path.stat())}
            path.write_bytes(b"second")
            with self.assertRaises(ValueError):
                scan.full_hash(record)
            link = Path(temporary) / "link.mp4"
            make_symlink(link, path)
            record = {"path": str(link), "name": link.name, "_signature": scan.signature(path.stat())}
            with self.assertRaises((ValueError, OSError)):
                scan.full_hash(record)

    def test_scan_root_rejects_symlink_and_package_subdirectory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "photos"
            target.mkdir()
            link = base / "linked-photos"
            make_symlink(link, target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "符号链接"):
                scan.normalize_roots([str(link)], base / "reports")
            nested = base / "Library.photoslibrary" / "originals"
            nested.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "资料库包"):
                scan.normalize_roots([str(nested)], base / "reports")

    def test_exif_filename_and_tiff_date_priority(self):
        record = {"name": "IMG_20240229.jpg", "mtime": 0, "image": {"date_original": "2020:01:02 12:00:00", "date_source": "exif_original"}}
        self.assertEqual(scan.month_for(record)[0], "2020/01")
        record["image"]["date_source"] = "tiff_datetime"
        self.assertEqual(scan.month_for(record)[0], "2024/02")
        record["name"] = "IMG_20230230.jpg"
        self.assertIn("修改时间", scan.month_for(record)[1])

    def test_ids_and_csv_formula_escaping(self):
        self.assertEqual(scan.video_id("ABC-123-CD2-1080p"), "ABC-123")
        self.assertEqual(scan.video_id("IMG_20240101"), "")
        self.assertEqual(scan.video_id("FC2-PPV-1234567"), "FC2-PPV-1234567")
        self.assertEqual(scan.csv_cell(" =HYPERLINK(x)"), "' =HYPERLINK(x)")

    def test_related_video_titles_keep_distinct_works_apart(self):
        def video(name):
            return {"kind": "视频", "name": name, "path": "/tmp/" + name,
                    "hardlink_to": "", "bytes": 7, "extension": "mp4"}
        records = [video("Family.Trip.CD1.1080p.mp4"), video("Family.Trip.CD2.720p.mp4"),
                   video("Family.Dinner.1080p.mp4"), video("ABC-123-CD1.mp4"), video("ABC-123-CD2.mp4")]
        groups = scan.related_videos(records)
        self.assertEqual({g["label"] for g in groups}, {"Family Trip", "ABC-123"})

    def test_ambiguous_live_photo_name_is_not_paired_automatically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "IMG_0001.heic").write_bytes(b"photo one")
            (root / "IMG_0001.jpg").write_bytes(b"photo two")
            (root / "IMG_0001.mov").write_bytes(b"video one")
            (root / "IMG_0002.jpg").write_bytes(b"photo three")
            (root / "IMG_0002.mov").write_bytes(b"video two")
            records, _ = scan.discover([root], root / "reports", False, [])
            scan.classify(records, "auto")
            by_name = {record["name"]: record for record in records}
            self.assertTrue(by_name["IMG_0001.mov"]["suggested_path"].startswith("视频/"))
            self.assertIn("不唯一", by_name["IMG_0001.mov"]["reason"])
            self.assertTrue(by_name["IMG_0002.mov"]["suggested_path"].startswith("照片/"))

    def test_optional_video_header_check_reports_uncertainty(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "media"
            source.mkdir()
            (source / "good.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
            os.link(source / "good.mp4", source / "hard.mp4")
            (source / "suspicious.mp4").write_bytes(b"just sample bytes")
            (source / "renamed-photo.mp4").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\0" * 12)
            (source / "unsupported.ts").write_bytes(b"sample transport stream")
            originals = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                code = scan.main([str(source), "--output", str(base / "reports"),
                                  "--no-image-metadata", "--check-video-headers"])
            self.assertEqual(code, 0)
            report_dir = next((base / "reports").glob("scan-*"))
            data = json.loads((report_dir / "report.json").read_text())
            self.assertEqual(data["video_inspection"], {"enabled": True, "checked": 3,
                                                         "recognized": 1, "unrecognized": 2})
            by_name = {record["name"]: record for record in data["files"]}
            self.assertIn("识别到", by_name["good.mp4"]["video_header_status"])
            self.assertIn("硬链接：识别到", by_name["hard.mp4"]["video_header_status"])
            self.assertIn("未识别", by_name["suspicious.mp4"]["video_header_status"])
            self.assertIn("未识别", by_name["renamed-photo.mp4"]["video_header_status"])
            self.assertIn("未检查", by_name["unsupported.ts"]["video_header_status"])
            self.assertEqual(len(data["issues"]), 2)
            self.assertIn("视频文件头状态", (report_dir / "inventory.csv").read_text())
            self.assertIn("本次轻量检查 3 个视频文件头", (report_dir / "report.html").read_text())
            self.assertEqual(originals, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})

    def test_report_search_data_includes_every_row_and_escapes_filenames(self):
        files = [{"kind": "照片", "path": f"/sample/photo-{i}.jpg",
                  "suggested_path": f"照片/photo-{i}.jpg", "reason": "样例"} for i in range(1000)]
        files.append({"kind": "视频", "path": "/sample/</script><script>alert(1)</script>.mp4",
                      "suggested_path": "视频/last.mp4", "reason": "最后一条"})
        data = {"summary": {"files": len(files), "duplicate_groups": 0, "hardlinks": 0, "redundant_logical_bytes": 0},
                "duplicates": [], "similar": {"pairs": [], "eligible": 0, "truncated": False},
                "previews": {}, "video_groups": [], "files": files, "issues": [], "skipped": {},
                "roots": ["/sample"], "created_at": "2026-10-01", "options": {"distance": 6},
                "image_inspection": {"note": "样例"}}
        page = scan.render_report(data)
        self.assertIn("视频/last.mp4", page)
        self.assertIn(r"\u003c/script\u003e", page)
        self.assertNotIn("</script><script>alert(1)", page)

    def test_similarity_threshold_and_exact_exclusion(self):
        def record(path, value, sha=""):
            return {"path": path, "hardlink_to": "", "sha256": sha, "image": {"dhash": f"{value:016x}", "low_detail": False, "width": 100, "height": 80}}
        data = [record("a", 0x1234, "same"), record("b", 0x1235), record("c", 0x1234, "same")]
        result = scan.similar_images(data, 1, 100, True)
        self.assertEqual(len(result["pairs"]), 2)
        self.assertFalse(any(p["left"] == "a" and p["right"] == "c" for p in result["pairs"]))
        self.assertTrue(scan.similar_images(data, 1, 1, True)["truncated"])
        self.assertFalse(scan.similar_images(data, 1, 2, True)["truncated"])

    def test_output_exclusion_and_target_collision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "reports"
            output.mkdir()
            (output / "ignored.mp4").write_bytes(b"report")
            for directory in ["a", "b"]:
                (root / directory).mkdir()
                (root / directory / "IMG_20240101.jpg").write_bytes(b"image")
            records, skipped = scan.discover([root], output, False, [])
            self.assertEqual(len(records), 2)
            self.assertEqual(skipped["报告目录"], 1)
            scan.classify(records, "auto")
            self.assertEqual(len({r["suggested_path"].casefold() for r in records}), 2)


if __name__ == "__main__":
    unittest.main()
