"""Report viewing checks use generated temporary reports and an in-memory HTTP handler."""
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

import library_server as viewer


class MemoryServer:
    server_port = 43210

    def __init__(self, address, handler):
        self.handler = handler


def request(server, path, method="GET", payload=None, host=None, origin=None):
    handler = object.__new__(server.handler)
    handler.server = server
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.headers = Message()
    handler.headers["Host"] = host or f"127.0.0.1:{server.server_port}"
    if origin is not None:
        handler.headers["Origin"] = origin
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    handler.headers["Content-Type"] = "application/json"
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    getattr(handler, f"do_{method}")()
    headers, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    return headers.decode("latin1"), body


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.report = self.output / "scan-example"
        self.report.mkdir()
        self.key = "a" * 64
        (self.report / "report.json").write_text(json.dumps({
            "video_library": {"groups": [{"tag_key": self.key}]},
            "files": [{"path": "/synthetic/photo.jpg", "kind": "照片", "bytes": 12,
                       "suggested_path": "照片/2026/10/photo.jpg"},
                      {"path": "/synthetic/video.mp4", "kind": "视频", "bytes": 24,
                       "suggested_path": "视频/旅行/video.mp4"}],
            "duplicates": []
        }))
        self.page = b"<!doctype html><html><body><main>sample</main></body></html>"
        for name in ("report.html", "library.html"):
            (self.report / name).write_bytes(self.page)

    def server(self, **options):
        with mock.patch.object(viewer, "ThreadingHTTPServer", MemoryServer):
            server, url = viewer.create_library_server(self.report, self.output, **options)
        self.prefix = urlsplit(url).path.removesuffix("library.html")
        return server

    def test_dashboard_navigation_is_visible_escaped_and_does_not_change_disk(self):
        dashboard = 'http://127.0.0.1:45678/token/?example="a"&mode=reports'
        server = self.server(dashboard_url=dashboard)
        for name in ("report.html", "library.html"):
            headers, body = request(server, self.prefix + name)
            self.assertIn("200 OK", headers)
            self.assertIn('返回控制台'.encode(), body)
            self.assertIn(b'target="_top"', body)
            self.assertIn(b'&quot;a&quot;&amp;mode=reports', body)
            self.assertIn(f"Content-Length: {len(body)}", headers)
            self.assertEqual((self.report / name).read_bytes(), self.page)
        plain = self.server()
        self.assertEqual(request(plain, self.prefix + "report.html")[1], self.page)

    def test_only_local_dashboard_addresses_are_accepted(self):
        for url in ("https://127.0.0.1:123/a", "http://localhost:123/a",
                    "http://127.0.0.1:123@evil.example/a", "http://127.0.0.1:0/",
                    "http://127.0.0.1:99999/", "http://127.0.0.1:123/\nfoo"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.server(dashboard_url=url)

    def test_organizing_workspace_uses_shipped_page_and_dashboard_navigation(self):
        assets = self.output / "assets"
        assets.mkdir()
        page = assets / "organization.html"
        page.write_bytes(self.page)
        (self.report / "organize.html").write_text("ignored report page")
        server = self.server(dashboard_url="http://127.0.0.1:45678/dashboard/")
        with mock.patch.object(viewer, "__file__", str(assets / "library_server.py")):
            headers, body = request(server, self.prefix + "organize.html")
        self.assertIn("200 OK", headers)
        self.assertIn(b"<main>sample</main>", body)
        self.assertNotIn(b"ignored report page", body)
        self.assertIn("返回控制台".encode(), body)
        self.assertIn(f"Content-Length: {len(body)}", headers)
        self.assertEqual(page.read_bytes(), self.page)

    def test_legacy_reports_open_without_tags_and_reject_tag_writes(self):
        for document in ({}, {"video_library": {"groups": [{"title": "旧影片"}]}}):
            (self.report / "report.json").write_text(json.dumps(document))
            server = self.server()
            headers, body = request(server, self.prefix + "report.html")
            self.assertIn("200 OK", headers)
            headers, body = request(server, self.prefix + "api/tags")
            self.assertEqual(json.loads(body), {"groups": {}, "editable": False})
            headers, _ = request(server, self.prefix + "api/tags", "POST", {"key": self.key, "tags": ["标签"]})
            self.assertIn("400 Bad Request", headers)
            self.assertFalse((self.output / "library-tags.json").exists())

    def test_csv_download_uses_safe_attachment_name(self):
        name = '影片"清单.csv'
        content = '影片,数量\n例子,1\n'.encode()
        (self.report / name).write_bytes(content)
        server = self.server()
        headers, body = request(server, self.prefix + name)
        self.assertIn('Content-Disposition: attachment; filename="_____.csv";', headers)
        self.assertIn("filename*=UTF-8''", headers)
        self.assertEqual(body, content)

    def test_host_token_traversal_and_symlinks_are_rejected(self):
        server = self.server()
        (self.output / "outside.csv").write_text("outside report")
        (self.report / "escape.csv").symlink_to(self.output / "outside.csv")
        (self.output / "outside").mkdir()
        (self.output / "outside" / "poster.png").write_bytes(b"sample")
        (self.report / "covers").symlink_to(self.output / "outside", target_is_directory=True)
        for path in ("/wrong/report.html", self.prefix + "../outside.csv",
                     self.prefix + "%2e%2e/outside.csv", self.prefix + "escape.csv",
                     self.prefix + "covers/poster.png", self.prefix + "foo%00.csv"):
            with self.subTest(path=path):
                self.assertIn("404 Not Found", request(server, path)[0])
        self.assertIn("404 Not Found", request(server, self.prefix + "report.html", host="evil.example")[0])
        (self.report / "report.json").unlink()
        (self.report / "report.json").symlink_to(self.output / "outside.csv")
        with self.assertRaises(OSError):
            self.server()

    def test_report_viewers_share_tag_lock_and_saved_tags(self):
        first = self.server()
        first_prefix = self.prefix
        second = self.server()
        self.assertIs(first.tag_lock, second.tag_lock)
        headers, body = request(first, first_prefix + "api/tags", "POST",
                                {"key": self.key, "tags": ["旅行", "收藏"]})
        self.assertIn("200 OK", headers)
        headers, body = request(second, self.prefix + "api/tags")
        self.assertEqual(json.loads(body)["groups"][self.key], ["旅行", "收藏"])

    def test_oversized_tag_save_keeps_existing_file(self):
        path = self.output / "library-tags.json"
        viewer.save_tags(path, {self.key: ["原标签"]})
        before = path.read_bytes()
        with mock.patch.object(viewer, "MAX_TAG_FILE", len(before)), self.assertRaises(ValueError):
            viewer.save_tags(path, {self.key: ["更长的新标签"]})
        self.assertEqual(path.read_bytes(), before)

    def test_organization_api_persists_decisions_and_exports_only_included_files(self):
        server = self.server()
        headers, body = request(server, self.prefix + "api/organization")
        self.assertIn("200 OK", headers)
        items = json.loads(body)["items"]
        self.assertFalse((self.report / "organization-plan.json").exists())
        headers, body = request(server, self.prefix + "api/organization", "POST",
                                {"ids": [items[0]["id"]], "state": "include"})
        self.assertIn("200 OK", headers)
        self.assertEqual(json.loads(body)["counts"]["include"], 1)
        request(server, self.prefix + "api/organization", "POST",
                {"ids": [items[1]["id"]], "state": "hold"})
        reopened = self.server()
        headers, body = request(reopened, self.prefix + "api/organization")
        self.assertEqual(json.loads(body)["counts"], {"total": 2, "pending": 0, "include": 1, "hold": 1})
        headers, body = request(reopened, self.prefix + "organization.json")
        self.assertIn('Content-Disposition: attachment; filename="organization.json"', headers)
        exported = json.loads(body)
        self.assertEqual(exported["mode"], "review_only")
        self.assertEqual([item["path"] for item in exported["items"]], ["/synthetic/photo.jpg"])
        headers, body = request(reopened, self.prefix + "organization.csv")
        self.assertIn("text/csv", headers)
        self.assertIn(b"/synthetic/photo.jpg", body)
        self.assertNotIn(b"/synthetic/video.mp4", body)

    def test_organization_rejects_invalid_requests_and_preserves_damaged_state(self):
        server = self.server()
        items = json.loads(request(server, self.prefix + "api/organization")[1])["items"]
        for payload in ([], {}, {"ids": ["unknown"], "state": "include"},
                        {"ids": [items[0]["id"]], "state": "move"}):
            headers, _ = request(server, self.prefix + "api/organization", "POST", payload)
            self.assertIn("400 Bad Request", headers)
        headers, _ = request(server, self.prefix + "api/organization", "POST",
                             {"ids": [items[0]["id"]], "state": "include"}, origin="http://evil.example")
        self.assertIn("403 Forbidden", headers)
        state = self.report / "organization-plan.json"
        state.write_bytes(b"{broken")
        for route in ("api/organization", "organization.csv", "organization.json"):
            self.assertIn("500 Internal Server Error", request(server, self.prefix + route)[0])
        headers, _ = request(server, self.prefix + "api/organization", "POST",
                             {"ids": [items[0]["id"]], "state": "include"})
        self.assertIn("400 Bad Request", headers)
        self.assertEqual(state.read_bytes(), b"{broken")
        self.assertIn("200 OK", request(server, self.prefix + "report.html")[0])


if __name__ == "__main__":
    unittest.main()
