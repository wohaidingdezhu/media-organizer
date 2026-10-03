"""Report navigation checks using generated reports and in-memory HTTP handlers."""
from email.message import Message
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

import media_gui
import library_server


class MemoryServer:
    ports = itertools.count(43210)

    def __init__(self, address, handler):
        self.server_port = next(self.ports)
        self.handler = handler

    def serve_forever(self):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


def request(server, path, method="GET", payload=None, *, host=None, origin=None):
    handler = object.__new__(server.handler)
    handler.server = server
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.headers = Message()
    handler.headers["Host"] = host or f"127.0.0.1:{server.server_port}"
    handler.headers["Origin"] = origin or f"http://127.0.0.1:{server.server_port}"
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    handler.headers["Content-Type"] = "application/json"
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    getattr(handler, f"do_{method}")()
    headers, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    return headers.decode("latin1"), body


def write_sample_report(output, name, *, library=True):
    directory = output / name
    directory.mkdir()
    document = {"version": 9, "created_at": "2026-10-02T12:00:00+08:00",
                "roots": ["/sample/never-opened"],
                "files": [{"kind": "照片", "suggested_path": "照片/2026/01/a.jpg"},
                          {"kind": "照片", "suggested_path": "照片/2026/01/b.jpg"},
                          {"kind": "视频"}],
                "duplicates": [{"paths": ["a", "b"]}], "similar": {"pairs": [["a", "b"]]},
                "issues": [{"path": "sample", "reason": "sample"}]}
    if library:
        document["video_library"] = {"groups": [{"title": "旧版影片没有标签标识"}], "poster_count": 1}
        (directory / "library.html").write_text("<!doctype html><html><body>Sample library</body></html>")
    (directory / "report.json").write_text(json.dumps(document), encoding="utf-8")
    (directory / "report.html").write_text("<!doctype html><html><body>Sample report</body></html>")
    return directory


class ReportHistoryTests(unittest.TestCase):
    def test_history_summarizes_valid_reports_and_explains_skipped_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_sample_report(output, "scan-20261001", library=False)
            write_sample_report(output, "scan-20261002")
            corrupt = write_sample_report(output, "scan-20261003")
            (corrupt / "report.json").write_text("{unfinished")
            incomplete = write_sample_report(output, "scan-20261004")
            (incomplete / "library.html").unlink()
            state = media_gui.DashboardState(output)
            history = state.report_history()
            self.assertEqual([item["id"] for item in history["reports"]], ["scan-20261002", "scan-20261001"])
            self.assertEqual(history["skipped_reports"], 2)
            self.assertFalse(history["has_more"])
            self.assertEqual(history["reports"][0]["summary"],
                             {"files": 3, "photos": 2, "videos": 1, "duplicate_groups": 1,
                              "planned_files": 2, "similar_pairs": 1, "issues": 1, "poster_count": 1})
            self.assertFalse(history["reports"][1]["has_library"])
            self.assertEqual(history["reports"][0]["roots"], ["/sample/never-opened"])

    def test_selected_report_rejects_traversal_and_symlinked_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            safe = write_sample_report(output, "scan-safe")
            (output / "scan-link").symlink_to(safe, target_is_directory=True)
            state = media_gui.DashboardState(output)
            for invalid in (None, 0, "../scan-safe", "scan-safe/../scan-safe", "scan-%2e%2e", "scan-link"):
                with self.subTest(report_id=invalid), self.assertRaises((ValueError, OSError)):
                    state.view_report(invalid)
            for name in ("report.json", "report.html", "library.html"):
                with self.subTest(name=name):
                    path = safe / name
                    saved = output / ("saved-" + name)
                    path.replace(saved)
                    path.symlink_to(saved)
                    with self.assertRaises(ValueError):
                        state.view_report("scan-safe")
                    path.unlink()
                    saved.replace(path)

    def test_summaries_are_cached_and_status_polling_does_not_parse_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = write_sample_report(output, "scan-cache")
            state = media_gui.DashboardState(output)
            with mock.patch.object(media_gui, "summarize_report", wraps=media_gui.summarize_report) as summarize:
                self.assertEqual(state.report_history()["reports"][0]["summary"]["issues"], 1)
                state.snapshot()
                state.snapshot()
                state.report_history()
                self.assertEqual(summarize.call_count, 1)
                data = json.loads((report / "report.json").read_text())
                data["issues"].append({"reason": "new finding"})
                (report / "report.json").write_text(json.dumps(data))
                self.assertEqual(state.report_history()["reports"][0]["summary"]["issues"], 2)
                self.assertEqual(summarize.call_count, 2)

    def test_history_limit_is_indicated_and_excludes_broken_newer_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for name in ("scan-1", "scan-2", "scan-3"):
                write_sample_report(output, name)
            broken = write_sample_report(output, "scan-4")
            (broken / "report.html").write_bytes(b"")
            with mock.patch.object(media_gui, "MAX_REPORT_HISTORY", 2):
                result = media_gui.DashboardState(output).report_history()
            self.assertEqual([item["id"] for item in result["reports"]], ["scan-3", "scan-2"])
            self.assertTrue(result["has_more"])
            self.assertEqual(result["skipped_reports"], 1)

    def test_api_opens_selected_history_and_preserves_older_viewers(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(media_gui, "ThreadingHTTPServer", MemoryServer), \
                mock.patch.object(library_server, "ThreadingHTTPServer", MemoryServer):
            output = Path(temporary)
            write_sample_report(output, "scan-old", library=False)
            write_sample_report(output, "scan-new")
            server, state, base_url = media_gui.create_dashboard_server(output)
            prefix = urlsplit(base_url).path
            try:
                headers, raw = request(server, prefix + "api/reports")
                self.assertIn("200 OK", headers)
                self.assertIn("frame-src http://127.0.0.1:*", headers)
                self.assertEqual(len(json.loads(raw)["reports"]), 2)
                headers, raw = request(server, prefix + "api/reports/view", "POST",
                                       {"report_id": "scan-old", "view": "issues"})
                self.assertIn("200 OK", headers)
                first = json.loads(raw)
                self.assertEqual(first["report_id"], "scan-old")
                self.assertTrue(first["url"].endswith("report.html#issues"))
                headers, raw = request(server, prefix + "api/reports/view", "POST",
                                       {"report_id": "scan-new", "view": "library"})
                self.assertIn("200 OK", headers)
                second = json.loads(raw)
                self.assertTrue(second["url"].endswith("library.html"))
                for result in (first, second):
                    viewer, _ = state.report_servers[result["report_id"]]
                    headers, content = request(viewer, urlsplit(result["url"]).path)
                    self.assertIn("200 OK", headers)
                    self.assertIn("返回控制台", content.decode())
                    self.assertIn(base_url, content.decode())
                self.assertEqual(len(state.report_servers), 2)
                headers, raw = request(server, prefix + "api/reports/view", "POST",
                                       {"report_id": "scan-new", "view": "duplicates"})
                self.assertIn("200 OK", headers)
                self.assertTrue(json.loads(raw)["url"].endswith("report.html#duplicates"))
                self.assertEqual(len(state.report_servers), 2)
                headers, _ = request(server, prefix + "api/reports/view", "POST",
                                     {"report_id": "../scan-old", "view": "issues"})
                self.assertIn("400 Bad Request", headers)
                headers, _ = request(server, prefix + "api/reports/view", "POST",
                                     {"report_id": "scan-old", "view": "library"})
                self.assertIn("400 Bad Request", headers)
                headers, raw = request(server, prefix + "api/library/open", "POST", {})
                self.assertIn("200 OK", headers)
                self.assertEqual(json.loads(raw)["report_id"], "scan-new")
                # This view is generated by the viewer and needs no file in an older report.
                self.assertFalse((output / "scan-old" / "organize.html").exists())
                headers, raw = request(server, prefix + "api/reports/view", "POST",
                                       {"report_id": "scan-old", "view": "organize"})
                self.assertIn("200 OK", headers)
                self.assertTrue(json.loads(raw)["url"].endswith("organize.html"))
            finally:
                state.close()
                server.server_close()
            self.assertEqual(state.report_servers, {})

    def test_report_api_rejects_wrong_origin_host_and_non_object_payload(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(media_gui, "ThreadingHTTPServer", MemoryServer):
            server, state, base_url = media_gui.create_dashboard_server(Path(temporary))
            prefix = urlsplit(base_url).path
            try:
                headers, _ = request(server, prefix + "api/reports", host="evil.example")
                self.assertIn("404 Not Found", headers)
                headers, _ = request(server, prefix + "api/reports/view", "POST", {},
                                     origin="https://example.com")
                self.assertIn("403 Forbidden", headers)
                for payload in ([], "report", 7):
                    headers, raw = request(server, prefix + "api/reports/view", "POST", payload)
                    self.assertIn("400 Bad Request", headers)
                    self.assertIn("JSON 对象", json.loads(raw)["error"])
            finally:
                state.close()

    def test_successful_scan_points_to_organizing_plan_with_result_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_sample_report(output, "scan-completed")
            state = media_gui.DashboardState(output)
            process = mock.Mock(stdout=io.StringIO("sample scan finished\n"))
            process.wait.return_value = 0
            with mock.patch.object(media_gui, "compile_helpers"), \
                    mock.patch.object(media_gui.subprocess, "Popen", return_value=process):
                state._run_scan(["sample.py"], False, False)
            self.assertIn("3 个媒体文件", state.status)
            self.assertIn("2 项分类建议", state.status)
            self.assertIn("1 组精确重复", state.status)
            self.assertIn("整理计划", state.status)
            self.assertFalse(state.running)
            self.assertIsNone(state.process)


if __name__ == "__main__":
    unittest.main()
