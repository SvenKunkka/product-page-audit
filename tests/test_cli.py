import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from product_page_audit.report import render_markdown
from product_page_audit.cli import validate_profile


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "product_page_audit", *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", timeout=20)

    def test_offline_report_and_distinct_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.html"
            source.write_text('<html><head><title>Sample product</title></head><body><h1>Sample product</h1></body></html>', encoding="utf-8")
            args = ("--html", source, "--out", root / "reports")
            first = self.run_cli(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            paths = json.loads(first.stdout)
            result = json.loads(Path(paths["report_json"]).read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "1.0")
            self.assertTrue(result["not_checked"])
            second = self.run_cli(*args)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(paths["report_json"], json.loads(second.stdout)["report_json"])

    def test_invalid_combinations_and_profile(self):
        self.assertEqual(self.run_cli("--html", "nothing", "--browser").returncode, 2)
        self.assertEqual(self.run_cli("https://example.com", "--max-links", "1000").returncode, 2)
        for profile in (["not an object"], {"max_image_kb": float("nan")},
                        {"min_gallery_images": 1.5}, {"model": 123},
                        {"exclusive_tag_dimensions": {"layout": "wrong shape"}}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_profile(profile)

    def test_report_escapes_page_markup(self):
        text = render_markdown({"findings": [{"title": "<script>alert(1)</script>", "evidence": [{"location": "h1", "detail": "![click](https://example.com)"}]}]})
        self.assertNotIn("<script>", text)
        self.assertNotIn("![click]", text)


if __name__ == "__main__":
    unittest.main()
