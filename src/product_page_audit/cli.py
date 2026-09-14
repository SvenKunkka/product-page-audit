"""CLI front end. Generated reports stay in a new directory for every run."""

import argparse
import json
import math
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .engine import audit_html, audit_url
from .network import SafeFetcher
from .report import json_text, render_markdown


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("profile must be a JSON object")
    for key in ("brand", "model"):
        if key in profile and not isinstance(profile[key], str):
            raise ValueError(f"profile.{key} must be a string")
    for key in ("expected_specs", "exclusive_tag_dimensions"):
        if key in profile and not isinstance(profile[key], dict):
            raise ValueError(f"profile.{key} must be an object")
    for key in ("min_gallery_images", "max_image_kb"):
        if key in profile:
            value = profile[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"profile.{key} must be a finite nonnegative number")
    if "min_gallery_images" in profile and profile["min_gallery_images"] != int(profile["min_gallery_images"]):
        raise ValueError("profile.min_gallery_images must be an integer")
    for choices in profile.get("exclusive_tag_dimensions", {}).values():
        if not isinstance(choices, list) or any(not isinstance(v, str) for v in choices):
            raise ValueError("profile.exclusive_tag_dimensions values must be lists of tag strings")


def parser():
    p = argparse.ArgumentParser(description="Audit a product URL or saved HTML and write JSON + Markdown evidence.")
    p.add_argument("target", nargs="?", help="Public product page URL")
    p.add_argument("--html", type=Path, help="Saved UTF-8 HTML for offline auditing (no network)")
    p.add_argument("--url", default="https://example.com/products/demo", help="Source identity for --html")
    p.add_argument("--profile", type=Path, help="JSON brand/model/specification expectations")
    p.add_argument("--out", type=Path, default=Path("reports"), help="Report parent directory")
    p.add_argument("--browser", action="store_true", help="Add isolated desktop and mobile browser capture")
    p.add_argument("--check-links", action="store_true", help="Check a capped set of same-origin read-only links")
    p.add_argument("--max-links", type=int, default=30, help="Maximum link checks (0–100)")
    p.add_argument("--timeout", type=float, default=15, help="Per-request timeout in seconds (1–60)")
    p.add_argument("--allow-local", action="store_true", help="Allow private/local addresses for explicit development testing")
    p.add_argument("--fail-on-error", action="store_true", help="Exit 1 if the report contains error findings")
    p.add_argument("--version", action="version", version=f"product-page-audit {__version__}")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if bool(args.target) == bool(args.html):
        p.error("provide one product URL or --html FILE")
    if args.html and (args.browser or args.check_links):
        p.error("--html is offline; --browser and --check-links require a product URL")
    if not 0 <= args.max_links <= 100 or not 1 <= args.timeout <= 60:
        p.error("--max-links must be 0–100 and --timeout must be 1–60")
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else {}
        validate_profile(profile)
        source = args.target or args.url
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", urlsplit(source).path.strip("/")[-65:]).strip("-") or "page"
        args.out.mkdir(parents=True, exist_ok=True)
        prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + slug + "-"
        destination = Path(tempfile.mkdtemp(prefix=prefix, dir=args.out.resolve()))
        rendered = None
        if args.browser:
            from .browser import capture
            rendered = capture(source, output_dir=destination, timeout=args.timeout, allow_local=args.allow_local)
        if args.html:
            if args.html.stat().st_size > 4_000_000:
                raise ValueError("offline HTML exceeds 4 MB")
            report = audit_html(args.html.read_text(encoding="utf-8"), source, profile=profile)
        else:
            report = audit_url(source, fetcher=SafeFetcher(timeout=args.timeout, allow_local=args.allow_local),
                               profile=profile, check_links=args.check_links, max_links=args.max_links, rendered=rendered)
        report["tool_version"] = __version__
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        if rendered:
            report["screenshots"] = rendered.get("screenshots", [])
        (destination / "report.json").write_text(json_text(report), encoding="utf-8")
        (destination / "report.md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"report_json": str(destination / "report.json"),
                          "report_markdown": str(destination / "report.md"),
                          "summary": report.get("summary", {})}, ensure_ascii=False))
        return int(args.fail_on_error and any(f.get("severity") == "error" for f in report.get("findings", [])))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"pdp-audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
