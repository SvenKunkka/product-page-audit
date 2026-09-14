"""Optional, isolated read-only page rendering with Playwright.

Every HTTP(S) request is fulfilled through SafeFetcher, which pins the
connection to the address it validated. Chromium never receives credentials
from an existing user profile. This is a bounded audit capture, not a logged-in
shopping session.
"""

from __future__ import annotations

import math
import posixpath
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from .network import SafeFetcher, validate_url


class BrowserUnavailable(RuntimeError):
    """The optional browser dependency or its Chromium binary is unavailable."""


VIEWPORTS = (("desktop", 1440, 1000), ("mobile", 375, 812))
MAX_REQUESTS = 120
MAX_SCREENSHOT_HEIGHT = 6000
MAX_SCROLL_STEPS = 8


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def _action_endpoint(url: str) -> bool:
    path = "/" + posixpath.normpath(unquote(urlsplit(url).path)).lower().lstrip("/")
    return bool(
        re.search(r"(?:^|/)(?:logout|log-out|signout|sign-out|unsubscribe)(?:/|$)", path)
        or re.search(r"(?:^|/)(?:cart/(?:add|change|update|clear)(?:\.js)?|checkout)(?:/|$)", path)
    )


def _allow_capture_url(url: str) -> bool:
    """Applied by SafeFetcher before each request, including redirects."""
    return not _action_endpoint(url)


def _response_headers(headers: dict[str, str]) -> dict[str, str]:
    # Let Playwright calculate the actual body length. SafeFetcher removes
    # Content-Encoding itself when decoding; preserve any remaining encoding.
    excluded = {"content-length", "transfer-encoding", "connection", "set-cookie"}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def capture(
    url: str,
    *,
    output_dir: str | Path | None = None,
    timeout: float = 20,
    allow_local: bool = False,
) -> dict[str, Any]:
    """Capture desktop/mobile evidence without clicking or submitting forms.

    ``timeout`` budgets navigation and fetching for each viewport; a preliminary
    safe document request has the same timeout. ``allow_local`` must be explicit
    for local fixtures.
    Missing Playwright/Chromium raises BrowserUnavailable. Capture failures are
    returned as errors, never represented as successful empty extractions.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError("Browser timeout must be between 1 and 120 seconds.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable(
            "Browser capture requires the browser extra: from a source checkout run pip install '.[browser]'; "
            "then run python -m playwright install chromium."
        ) from exc

    validate_url(url, allow_local=allow_local)
    initial = SafeFetcher(timeout=timeout, max_bytes=8_000_000, allow_local=allow_local).fetch(
        url, url_guard=_allow_capture_url,
    )
    resolved_url = initial["url"]
    validate_url(resolved_url, allow_local=allow_local)
    result: dict[str, Any] = {"views": [], "errors": [], "screenshots": [], "limitations": [
        "Fresh unauthenticated browser contexts; cookies, logins and existing browser profiles are not used.",
        "Rendering is bounded: at most eight scroll steps per view; consent dialogs and controls are not clicked.",
        "Browser traffic is served through the public-address-validated, DNS-pinned HTTP fetcher; "
        "POST/other mutations, service workers, WebSockets and downloads are disabled.",
    ]}
    destination = Path(output_dir).expanduser().resolve() if output_dir is not None else None
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
    extraction = Path(__file__).with_name("dom_extract.js").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                # A fail-closed proxy prevents incidental Chromium HTTP traffic
                # from bypassing route fulfillment. Python's pinned fetcher does
                # not use this proxy. Keep Chromium's normal sandbox enabled.
                proxy={"server": "http://127.0.0.1:9", "bypass": "<-loopback>"},
                args=["--disable-background-networking", "--disable-quic",
                      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            )
        except Exception as exc:
            raise BrowserUnavailable(
                "Chromium could not start. Install its binary with python -m playwright install chromium. "
                f"Details: {exc}"
            ) from exc
        try:
            for name, width, height in VIEWPORTS:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    is_mobile=name == "mobile", device_scale_factor=1,
                    has_touch=name == "mobile", service_workers="block", accept_downloads=False,
                )
                try:
                    deadline = time.monotonic() + timeout
                    failures: list[dict[str, str]] = []
                    blocked: list[dict[str, str]] = []
                    redirected: set[str] = set()
                    resource_sizes: dict[str, int] = {}
                    console_errors: list[str] = []
                    request_count = 0

                    def route_request(route: Any) -> None:
                        nonlocal request_count
                        request = route.request
                        request_count += 1
                        reason = None
                        if request.method not in ("GET", "HEAD"):
                            reason = f"Read-only capture blocks {request.method}."
                        elif _action_endpoint(request.url):
                            reason = "Known cart, checkout or account-action endpoint is blocked."
                        elif request_count > MAX_REQUESTS:
                            reason = "Per-view request limit reached."
                        elif time.monotonic() >= deadline:
                            reason = "Per-view time limit reached."
                        if reason:
                            if len(blocked) < 30:
                                blocked.append({"url": _display_url(request.url), "reason": reason})
                            route.abort("blockedbyclient")
                            return
                        try:
                            validate_url(request.url, allow_local=allow_local)
                            if request.url == resolved_url and request.resource_type == "document" and request.method == "GET":
                                response = initial
                            else:
                                response = SafeFetcher(
                                    timeout=max(0.1, min(8, deadline - time.monotonic())),
                                    max_bytes=8_000_000, allow_local=allow_local,
                                ).fetch(request.url, method=request.method, url_guard=_allow_capture_url)
                            if response["url"] != request.url:
                                redirected.add(request.url)
                            if request.method == "GET" and 200 <= response["status"] < 300:
                                resource_sizes[request.url] = len(response["body"])
                                resource_sizes[response["url"]] = len(response["body"])
                            route.fulfill(
                                status=response["status"],
                                headers=_response_headers(response["headers"]),
                                body=response["body"],
                            )
                        except Exception as exc:
                            if len(failures) < 30:
                                failures.append({"url": _display_url(request.url), "error": str(exc).replace(request.url, _display_url(request.url))[:500]})
                            route.abort("blockedbyclient")

                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    context.add_init_script("""(() => {
                        for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'WebTransport']) {
                            try { Object.defineProperty(window, name, {value: undefined, configurable: false}); } catch (_) {}
                        }
                    })();""")
                    page = context.new_page()
                    # Script-created popups cannot become additional unbounded captures.
                    context.on("page", lambda popup: popup.close())
                    page.on("pageerror", lambda error: console_errors.append(str(error)[:500]) if len(console_errors) < 15 else None)
                    page.on("console", lambda message: console_errors.append(message.text[:500]) if message.type == "error" and len(console_errors) < 15 else None)
                    page.on("download", lambda download: download.cancel())
                    page.set_default_timeout(min(5000, timeout * 1000))
                    load_note = None
                    try:
                        page.goto(resolved_url, wait_until="domcontentloaded", timeout=max(100, (deadline - time.monotonic()) * 1000))
                    except Exception as exc:
                        if not page.url.startswith(("http://", "https://")):
                            raise
                        load_note = f"Navigation did not finish within capture limits: {str(exc)[:300]}"
                    scroll_limit_hit = False
                    for step in range(MAX_SCROLL_STEPS):
                        if time.monotonic() >= deadline - 0.4:
                            scroll_limit_hit = True
                            break
                        page.wait_for_timeout(200)
                        at_bottom = page.evaluate("""() => {
                            const bottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
                            if (!bottom) window.scrollBy(0, Math.max(300, window.innerHeight * 0.8));
                            return bottom;
                        }""")
                        if at_bottom:
                            break
                    else:
                        scroll_limit_hit = True
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(100)
                    evidence = page.evaluate(extraction)
                    if not isinstance(evidence, dict) or not evidence.get("extraction_complete"):
                        raise RuntimeError("DOM extraction did not produce complete evidence.")
                    for image in evidence.get("images", []):
                        if image.get("src") in resource_sizes:
                            image["bytes"] = resource_sizes[image["src"]]
                            image["byte_measurement"] = "fetched_response_body"
                    evidence.update({
                        "name": name, "url": page.url, "width": width, "html": page.content(),
                        "resource_failures": failures, "blocked_requests": blocked,
                        "console_errors": console_errors, "scroll_limit_hit": scroll_limit_hit,
                    })
                    if load_note:
                        evidence["load_note"] = load_note
                    if redirected:
                        result["limitations"].append(
                            f"{name}: {len(redirected)} redirected subresources were fetched safely; relative URLs "
                            "inside those resources may resolve from the original resource URL."
                        )
                    if scroll_limit_hit:
                        result["limitations"].append(f"{name}: scrolling/time limit reached; later lazy-loaded sections may be incomplete.")
                    if destination is not None:
                        try:
                            document_height = int(page.evaluate("Math.max(document.documentElement.scrollHeight, innerHeight)"))
                            screenshot_height = min(MAX_SCREENSHOT_HEIGHT, document_height)
                            screenshot_path = destination / f"{name}.png"
                            page.screenshot(
                                path=str(screenshot_path),
                                clip={"x": 0, "y": 0, "width": width, "height": screenshot_height},
                                animations="disabled", timeout=5000,
                            )
                            result["screenshots"].append(str(screenshot_path))
                            evidence["screenshot"] = str(screenshot_path)
                            evidence["screenshot_capped"] = document_height > MAX_SCREENSHOT_HEIGHT
                        except Exception as exc:
                            result["errors"].append({"view": name, "stage": "screenshot", "error": str(exc)[:500]})
                    result["views"].append(evidence)
                except Exception as exc:
                    result["errors"].append({"view": name, "error": str(exc)[:1000]})
                finally:
                    context.close()
        finally:
            browser.close()
    return result
