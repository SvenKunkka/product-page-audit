from __future__ import annotations

import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from product_page_audit.engine import audit_html, audit_url
from product_page_audit.network import FetchError, SafeFetcher, UnsafeURLError, redact_url, validate_url


FIXTURE = (Path(__file__).parent / "fixtures" / "core-product.html").read_text(encoding="utf-8")
URL = "https://example.com/products/demo"


def response(url, body, status=200):
    if isinstance(body, dict):
        body = json.dumps(body)
    return {"url": url, "status": status, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body.encode()}


class FakeFetcher:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def fetch(self, url, method="GET", **kwargs):
        if kwargs.get("url_guard") and not kwargs["url_guard"](url):
            raise UnsafeURLError("Outside test collection scope")
        self.calls.append((url, method))
        item = self.replies[url]
        if isinstance(item, Exception):
            raise item
        return item


def dns(host, port, **kwargs):
    address = "127.0.0.1" if host in {"private.test", "localhost"} else "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]


class NetworkTests(unittest.TestCase):
    def test_rejects_protocols_credentials_and_local_addresses(self):
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns):
            for url in ("file:///etc/passwd", "https://user:pass@example.com", "http://private.test/", "http://localhost/", "http://host\\@example.com", "http://example.com/\n"):
                with self.subTest(url=url), self.assertRaises(UnsafeURLError):
                    validate_url(url)

    def test_every_dns_address_is_checked(self):
        answers = dns("example.com", 443) + dns("private.test", 443)
        with patch("product_page_audit.network.socket.getaddrinfo", return_value=answers):
            with self.assertRaises(UnsafeURLError):
                validate_url(URL)

    def test_private_redirect_is_rejected_before_request(self):
        fetcher = SafeFetcher()
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns), patch.object(fetcher, "_request", return_value={"url": URL, "status": 302, "headers": {"location": "http://private.test/admin"}, "body": b""}) as request:
            with self.assertRaises(UnsafeURLError):
                fetcher.fetch(URL)
            self.assertEqual(request.call_count, 1)

    def test_raw_redirect_option_still_checks_destination(self):
        fetcher = SafeFetcher()
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns), patch.object(fetcher, "_request", return_value={"url": URL, "status": 302, "headers": {"location": "http://private.test/"}, "body": b""}):
            with self.assertRaises(UnsafeURLError):
                fetcher.fetch(URL, follow_redirects=False)

    def test_allow_local_is_explicit_and_does_not_allow_url_passwords(self):
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns):
            self.assertEqual(validate_url("http://localhost/", allow_local=True), "http://localhost/")
            with self.assertRaises(UnsafeURLError):
                validate_url("http://user:pass@localhost/", allow_local=True)

    def test_request_receives_validated_addresses_and_keeps_preview_query(self):
        fetcher = SafeFetcher()
        url = URL + "?preview_key=secret%2Bvalue&variant=123"
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns) as resolver, patch.object(fetcher, "_request", return_value=response(url, FIXTURE)) as request:
            self.assertEqual(fetcher.fetch(url)["url"], url)
            self.assertEqual(resolver.call_count, 1)
            self.assertEqual(request.call_args.args[0], url)
            self.assertEqual(request.call_args.args[2][0][3][0], "93.184.216.34")

    def test_redaction_and_unicode_path(self):
        output = redact_url("https://user:password@example.com/p?preview_key=secret&variant=123#private")
        self.assertNotIn("password", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("private", output)
        self.assertIn("variant=123", output)
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns):
            self.assertIn("%E4%BA%A7", validate_url("https://example.com/产品"))

    def test_allow_hosts_applies_to_redirects(self):
        fetcher = SafeFetcher(allow_hosts=["example.com"])
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns), patch.object(fetcher, "_request", return_value={"url": URL, "status": 302, "headers": {"location": "https://other.example/"}, "body": b""}):
            with self.assertRaises(UnsafeURLError):
                fetcher.fetch(URL)

    def test_request_specific_guard_blocks_external_redirect_before_connection(self):
        fetcher = SafeFetcher()
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns), patch.object(fetcher, "_request", return_value={"url": URL, "status": 302, "headers": {"location": "https://other.example/"}, "body": b""}) as request:
            with self.assertRaises(UnsafeURLError):
                fetcher.fetch(URL, url_guard=lambda url: url.startswith("https://example.com/"))
            self.assertEqual(request.call_count, 1)


class EngineTests(unittest.TestCase):
    def test_nested_jsonld_semantic_scope_and_svg_title(self):
        report = audit_html(FIXTURE)
        page = report["evidence"]["pages"][0]
        self.assertEqual(page["title"], "Example Model X1")
        self.assertEqual(page["h1"], ["Model X1"])
        self.assertEqual(page["product_jsonld_count"], 1)
        self.assertIn("main content", page["content_scope"])
        evidence = str(page["text_segments"])
        self.assertIn("4000 mAh", evidence)
        self.assertNotIn("Navigation must", evidence)
        self.assertNotIn("Unrelated recommendation", evidence)
        self.assertFalse(any(f["category"] == "images" for f in report["findings"]))
        self.assertEqual(report["summary"]["verdict"], "review_required")

    def test_two_headings_on_one_line_are_distinct(self):
        report = audit_html(FIXTURE.replace("<h1>Model <span>X1</span></h1>", "<h1>Model X1</h1><h1>Another</h1>"))
        self.assertEqual(len(report["evidence"]["pages"][0]["h1"]), 2)
        self.assertTrue(any("heading" in f["title"] for f in report["findings"]))

    def test_missing_fields_are_unknown_not_zero_or_sold_out(self):
        product = {"product": {"id": 1, "title": "Model X1", "variants": [{"id": 2, "title": "Standard"}]}}
        report = audit_html(FIXTURE, product_json=product)
        self.assertFalse(any(f["category"] in {"shipping_data", "pricing", "variants"} for f in report["findings"]))
        self.assertEqual(report["evidence"]["product"]["availability_unknown"], 1)
        self.assertIsNone(report["evidence"]["product"]["variants"][0]["fields"]["price"])

    def test_explicit_bad_fields_and_js_currency_units(self):
        report = audit_html(FIXTURE, product_js={"id": 1, "title": "X1", "variants": [{"id": 2, "sku": "", "price": 4999, "compare_at_price": 4000, "grams": 0, "available": False}]})
        self.assertEqual(report["evidence"]["product"]["variants"][0]["fields"]["price"], 49.99)
        self.assertEqual(report["evidence"]["product"]["availability_known"], 1)
        self.assertEqual({f["category"] for f in report["findings"]}, {"shipping_data", "pricing", "variants"})

    def test_specs_compare_labelled_values_and_convert_units(self):
        report = audit_html(FIXTURE, profile={"expected_specs": {"Battery": "6000 mAh", "Polling rate": "8000 Hz"}})
        specifications = [f for f in report["findings"] if f["category"] == "specifications"]
        self.assertEqual(len(specifications), 1)
        self.assertEqual(specifications[0]["severity"], "error")
        self.assertIn("Battery", specifications[0]["title"])
        self.assertIn("line", specifications[0]["evidence"][0]["location"])

    def test_model_is_not_a_prefix_match(self):
        report = audit_html(FIXTURE.replace("Model <span>X1</span>", "Model X10"), profile={"model": "X1"})
        self.assertTrue(any(f["category"] == "identity" for f in report["findings"]))

    def test_independent_endpoints_keep_preview_key_but_reports_redact(self):
        url = URL + "?preview_key=very-secret&variant=2"
        json_url, js_url = url.replace("demo?", "demo.json?"), url.replace("demo?", "demo.js?")
        page = FIXTURE.replace("An example product description.", f"Go to {url}")
        fetcher = FakeFetcher({url: response(url, page), json_url: FetchError("Unavailable"), js_url: response(js_url, {"id": 1, "handle": "demo", "variants": []})})
        report = audit_url(url, fetcher=fetcher)
        self.assertEqual([call[0] for call in fetcher.calls], [url, json_url, js_url])
        self.assertEqual(report["platform"], "shopify")
        self.assertNotIn("very-secret", json.dumps(report))
        self.assertIn("variant=2", json.dumps(report))

    def test_products_path_alone_is_not_shopify_and_nonproduct_json_is_unknown(self):
        fetcher = FakeFetcher({URL: response(URL, FIXTURE), URL + ".json": response(URL + ".json", {"error": "not found"}), URL + ".js": response(URL + ".js", {}, 404)})
        report = audit_url(URL, fetcher=fetcher)
        self.assertEqual(report["platform"], "generic")
        self.assertNotIn("product", report["evidence"])

    def test_rendered_html_supplies_dynamic_product_content_and_visibility(self):
        html = FIXTURE.replace('<img src="/hidden.png">', '<img src="/hidden.png">').replace("<div hidden>", '<div class="css-hidden">')
        rendered = {"views": [{"name": "desktop", "url": URL, "html": html, "width": 1440, "images": [{"src": "https://example.com/hidden.png", "visible": False}], "typography": [{"font": "Arial"}], "console_errors": ["Sample failure"], "resource_failures": [], "blocked_requests": [], "extraction_complete": True}]}
        report = audit_html("<html><head><title>Loading</title></head><body></body></html>", rendered=rendered)
        self.assertEqual(report["evidence"]["pages"][0]["title"], "Example Model X1")
        self.assertEqual(report["evidence"]["pages"][0]["source"], "desktop rendered HTML")
        self.assertFalse(any(f["title"] == "Visible image lacks an alt attribute" for f in report["findings"]))
        self.assertEqual(report["evidence"]["browser"]["views"][0]["typography"][0]["font"], "Arial")

    def test_multiple_product_jsonld_uses_current_page_identity(self):
        old = '"@type":"WebPage","@id":"https://example.com/products/demo"'
        new = '"@type":"Product","url":"https://example.com/products/other","name":"Other"'
        report = audit_html(FIXTURE.replace(old, new))
        page = report["evidence"]["pages"][0]
        self.assertEqual(page["product_jsonld_count"], 2)
        self.assertEqual(page["selected_product_jsonld_count"], 1)
        self.assertFalse(any(f["category"] == "structured_data" for f in report["findings"]))

    def test_link_check_is_capped_skips_actions_and_network_failure_is_not_broken(self):
        page = FIXTURE.replace("</main>", '<a href="/timeout">Timeout</a><a href="/uncalled">Beyond cap</a><a href="https://external.test/x">External</a></main>')
        fetcher = FakeFetcher({URL: response(URL, page), URL + ".json": response(URL + ".json", {}, 404), URL + ".js": response(URL + ".js", {}, 404), "https://example.com/pages/manual": response("https://example.com/pages/manual", "", 404), "https://example.com/timeout": FetchError("Timeout")})
        report = audit_url(URL, fetcher=fetcher, check_links=True, max_links=2)
        self.assertEqual(report["evidence"]["link_checks"]["checked"], 2)
        calls = str(fetcher.calls)
        self.assertNotIn("cart", calls)
        self.assertNotIn("external.test", calls)
        links = [f for f in report["findings"] if f["category"] == "links"]
        self.assertEqual([f["severity"] for f in links], ["error", "info"])

    def test_failed_page_does_not_produce_spurious_missing_metadata(self):
        report = audit_url(URL, fetcher=FakeFetcher({URL: response(URL, "Forbidden", 403)}))
        self.assertEqual({f["category"] for f in report["findings"]}, {"network"})

    def test_link_redirect_to_cart_is_not_requested(self):
        fetcher = SafeFetcher()
        calls = []
        def reply(url, method, addresses):
            calls.append(url)
            if url == URL:
                return response(url, FIXTURE)
            if url.endswith((".json", ".js")):
                return response(url, {}, 404)
            if url.endswith("/pages/manual"):
                return {"url": url, "status": 302, "headers": {"location": "/cart/add?id=1"}, "body": b""}
            self.fail("Unsafe redirect destination was requested: " + url)
        with patch("product_page_audit.network.socket.getaddrinfo", side_effect=dns), patch.object(fetcher, "_request", side_effect=reply):
            report = audit_url(URL, fetcher=fetcher, check_links=True)
        self.assertFalse(any("cart" in url for url in calls))
        self.assertTrue(any(f["category"] == "links" and f["severity"] == "info" for f in report["findings"]))

    def test_captcha_title_is_not_a_product_page(self):
        report = audit_html("<html><head><title>Just a moment...</title></head><body>Checking</body></html>")
        self.assertEqual({f["category"] for f in report["findings"]}, {"collection"})

    def test_unverified_limits_and_offline_links_are_disclosed(self):
        report = audit_html(FIXTURE, profile={"min_gallery_images": 4, "max_image_kb": 500})
        omissions = " ".join(report["not_checked"])
        for expected in ("min_gallery_images", "max_image_kb", "Link HTTP", "Real-device", "authoritative specification"):
            self.assertIn(expected, omissions)

    def test_nan_prices_stay_unknown_and_report_is_strict_json(self):
        report = audit_html(FIXTURE, product_json={"variants": [{"price": "NaN", "compare_at_price": "Infinity", "weight": float("nan")}]})
        json.dumps(report, allow_nan=False)
        self.assertIsNone(report["evidence"]["product"]["variants"][0]["fields"]["price"])


if __name__ == "__main__":
    unittest.main()
