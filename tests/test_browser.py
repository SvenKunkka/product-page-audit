from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from product_page_audit.browser import BrowserUnavailable, _action_endpoint, _display_url, _response_headers, capture


class BrowserUnitTests(unittest.TestCase):
    def test_missing_dependency_gives_install_instructions_before_network(self):
        with patch.dict(sys.modules, {"playwright.sync_api": None}):
            with self.assertRaisesRegex(BrowserUnavailable, "playwright install chromium"):
                capture("https://example.com/product")

    def test_headers_preserve_raw_encoding_and_remove_cookie_and_hop_headers(self):
        self.assertEqual(
            _response_headers({"content-encoding": "gzip", "content-length": "9", "set-cookie": "secret=x", "content-type": "text/html"}),
            {"content-encoding": "gzip", "content-type": "text/html"},
        )

    def test_keyboard_interrupt_closes_context_and_browser(self):
        context = MagicMock()
        context.new_page.return_value.goto.side_effect = KeyboardInterrupt()
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: manager
        with patch.dict(sys.modules, {"playwright.sync_api": module}), \
             patch("product_page_audit.browser.validate_url"), \
             patch("product_page_audit.browser.SafeFetcher") as fetcher:
            fetcher.return_value.fetch.return_value = {"url": "https://example.com/product", "status": 200, "headers": {}, "body": b"<html></html>"}
            with self.assertRaises(KeyboardInterrupt):
                capture("https://example.com/product")
        context.close.assert_called_once()
        browser.close.assert_called_once()
        manager.__exit__.assert_called_once()

    def test_invalid_timeout_rejected(self):
        for timeout in (0, -1, float("nan"), float("inf"), 121):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                capture("https://example.com", timeout=timeout)

    def test_known_action_endpoints_blocked_without_blocking_products(self):
        for path in ("/cart/add.js?id=123", "/checkout", "/account/logout", "/unsubscribe", "/%63art/add.js", "/products/../cart/add", "/en/cart/add.js", "//cart/add.js"):
            self.assertTrue(_action_endpoint("https://example.com" + path))
        self.assertFalse(_action_endpoint("https://example.com/products/cartoon-keyboard"))
        self.assertEqual(_display_url("https://example.com/x?secret=abc#token"), "https://example.com/x")


def browser_tests_enabled():
    return os.environ.get("RUN_BROWSER_TESTS") == "1" and importlib.util.find_spec("playwright") is not None


@unittest.skipUnless(browser_tests_enabled(), "Set RUN_BROWSER_TESTS=1 and install browser extra/Chromium for browser fixtures.")
class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []
        document = Path(__file__).with_name("fixtures").joinpath("browser-fixture.html").read_bytes()
        seen = cls.seen

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.command, self.path))
                if self.path in ("/action-redirect", "/asset-redirect", "/account-resource-redirect"):
                    self.send_response(302)
                    self.send_header("Location", "/logout" if self.path == "/account-resource-redirect" else "/cart/add.js")
                    self.end_headers()
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/page")
                    self.end_headers()
                    return
                if self.path == "/page-with-action-resource":
                    body = document.replace(b'src="/normal.svg"', b'src="/asset-redirect"').replace(
                        b'document.title =', b'fetch("/account-resource-redirect").catch(() => {}); document.title =',
                    )
                    content_type, status = "text/html; charset=utf-8", 200
                elif self.path == "/page":
                    body, content_type, status = document, "text/html; charset=utf-8", 200
                elif self.path.endswith(".svg"):
                    body, content_type, status = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="blue"/></svg>', "image/svg+xml", 200
                else:
                    body, content_type, status = b"Missing", "text/plain", 404
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                seen.append((self.command, self.path))
                self.send_response(500)
                self.end_headers()

            def log_message(self, *_):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_local_capture_needs_explicit_allow_local(self):
        with self.assertRaises(ValueError):
            capture(self.base_url + "/page")

    def test_initial_and_redirected_action_documents_are_never_requested(self):
        self.seen.clear()
        for path in ("/cart/add.js", "/checkout", "/logout", "/action-redirect"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                capture(self.base_url + path, allow_local=True)
        self.assertIn(("GET", "/action-redirect"), self.seen)
        self.assertFalse(any(_action_endpoint(self.base_url + path) for _method, path in self.seen))

    def test_subresource_redirects_do_not_reach_action_endpoints(self):
        self.seen.clear()
        result = capture(self.base_url + "/page-with-action-resource", allow_local=True, timeout=15)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["views"]), 2)
        self.assertIn(("GET", "/asset-redirect"), self.seen)
        self.assertIn(("GET", "/account-resource-redirect"), self.seen)
        self.assertFalse(any(_action_endpoint(self.base_url + path) for _method, path in self.seen))
        for view in result["views"]:
            failed_paths = [failure["url"] for failure in view["resource_failures"]]
            self.assertTrue(any(path.endswith("/asset-redirect") for path in failed_paths))
            self.assertTrue(any(path.endswith("/account-resource-redirect") for path in failed_paths))

    def test_responsive_javascript_images_read_only_routes_and_screenshots(self):
        with tempfile.TemporaryDirectory() as folder:
            result = capture(self.base_url + "/redirect", output_dir=folder, timeout=15, allow_local=True)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["views"]), 2)
            desktop, mobile = result["views"]
            for view in result["views"]:
                self.assertEqual(view["title"], "Rendered browser fixture")
                self.assertTrue(view["url"].endswith("/page"))
                self.assertIn('data-javascript-ran="yes"', view["html"])
                self.assertTrue(view["specs_present"])
                self.assertTrue(view["typography"])
                images = {image["src"].rsplit("/", 1)[-1]: image for image in view["images"]}
                self.assertTrue(images["normal.svg"]["visible"])
                self.assertTrue(images["normal.svg"]["loaded"])
                self.assertGreater(images["normal.svg"]["bytes"], 0)
                self.assertEqual(images["normal.svg"]["byte_measurement"], "fetched_response_body")
                self.assertFalse(images["hidden.svg"]["visible"])
                self.assertFalse(images["clipped.svg"]["visible"])
                self.assertFalse(images["missing.png"]["loaded"])
                self.assertNotIn("bytes", images["missing.png"])
                self.assertTrue(images["lazy.svg"]["loaded"])
                self.assertTrue(any("POST" in item["reason"] for item in view["blocked_requests"]))
                png = Path(view["screenshot"]).read_bytes()
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                image_width, image_height = struct.unpack(">II", png[16:24])
                self.assertEqual(image_width, view["width"])
                self.assertLessEqual(image_height, 6000)
            self.assertTrue(desktop["specs_visible"])
            self.assertFalse(desktop["horizontal_overflow"])
            self.assertFalse(mobile["specs_visible"])
            self.assertTrue(mobile["horizontal_overflow"])
            self.assertFalse(any(method == "POST" for method, _ in self.seen))


if __name__ == "__main__":
    unittest.main()
