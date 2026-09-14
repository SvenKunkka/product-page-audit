"""Evidence-led product-page checks. No platform credentials are required."""

from __future__ import annotations

import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .network import FetchError, SafeFetcher, UnsafeURLError, redact_url


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_MANUAL = [
    "Grammar, meaning, and claims require human or AI review of the captured copy.",
    "Product specification correctness requires an authoritative specification source; page consistency alone is insufficient.",
    "Image content, product colour, embedded text, and visual correctness require visual review.",
    "Shipping profiles, checkout behaviour, payment, and non-public admin settings were not tested.",
    "Real-device iOS/Android behaviour was not tested; browser emulation is not real-device proof.",
]


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _snippet(value, limit=240):
    return _clean(value)[:limit]


class PageParser(HTMLParser):
    """Extract auditable HTML evidence without executing page scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.links = []
        self.images = []
        self.meta = {}
        self.h1 = []
        self.titles = []
        self.canonical = []
        self.jsonld = []
        self.jsonld_errors = []
        self.scripts = []
        self._jsonld_buffer = None
        self._jsonld_line = None
        self._element_sequence = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        line, column = self.getpos()
        parent_hidden = any(item["hidden"] for item in self.stack)
        hidden = parent_hidden or "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true" or bool(
            re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)
        )
        self._element_sequence += 1
        element = {"tag": tag, "attrs": attrs, "hidden": hidden, "line": line, "column": column, "sequence": self._element_sequence}
        in_main = tag == "main" or attrs.get("role") == "main" or any(item.get("in_main") for item in self.stack)
        ancillary = tag in {"nav", "aside"} or (tag in {"header", "footer"} and not in_main) or any(item.get("ancillary") for item in self.stack) or bool(re.search(
            r"(?:product-recommendations|related-products|recently-viewed|complementary-products)",
            attrs.get("id", "") + " " + attrs.get("class", ""), re.I,
        ))
        element.update(in_main=in_main, ancillary=ancillary)
        if tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key:
                self.meta.setdefault(key, []).append({"content": attrs.get("content", ""), "line": line})
        if tag == "link" and "canonical" in attrs.get("rel", "").lower().split():
            self.canonical.append({"href": attrs.get("href", ""), "line": line})
        if tag == "a":
            element["link_index"] = len(self.links)
            self.links.append({"href": attrs.get("href", ""), "text": "", "line": line, "hidden": hidden, "in_main": in_main, "ancillary": ancillary})
        if tag == "img":
            self.images.append({
                "src": attrs.get("src") or attrs.get("data-src") or "", "alt": attrs.get("alt", ""),
                "has_alt": "alt" in attrs, "visible": not hidden, "line": line,
                "width": attrs.get("width"), "height": attrs.get("height"),
                "role": attrs.get("role", ""),
                "in_main": in_main, "ancillary": ancillary,
            })
        if tag == "script":
            self.scripts.append({"src": attrs.get("src", ""), "line": line})
            if attrs.get("type", "").lower().split(";")[0].strip() == "application/ld+json":
                self._jsonld_buffer = []
                self._jsonld_line = line
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self._jsonld_buffer is not None:
            try:
                self.jsonld.append({"data": json.loads("".join(self._jsonld_buffer)), "line": self._jsonld_line})
            except (TypeError, ValueError):
                self.jsonld_errors.append(self._jsonld_line)
            self._jsonld_buffer = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        tags = [item["tag"] for item in self.stack]
        if "script" in tags:
            if self._jsonld_buffer is not None:
                self._jsonld_buffer.append(data)
            return
        if any(tag in tags for tag in ("style", "noscript", "template", "svg")):
            return
        value = _clean(data)
        if not value:
            return
        line, _ = self.getpos()
        hidden = any(item["hidden"] for item in self.stack)
        if "title" in tags and "head" in tags:
            self.titles.append({"text": value, "line": line})
            return
        if "head" in tags:
            return
        in_main = any(item.get("in_main") for item in self.stack)
        ancillary = any(item.get("ancillary") for item in self.stack)
        self.text.append({"text": value, "line": line, "hidden": hidden, "element": tags[-1] if tags else "document", "in_main": in_main, "ancillary": ancillary})
        h1 = next((item for item in reversed(self.stack) if item["tag"] == "h1"), None)
        if h1 and not hidden:
            existing = next((item for item in self.h1 if item["sequence"] == h1["sequence"]), None)
            if existing:
                existing["text"] += " " + value
            else:
                self.h1.append({"text": value, "line": h1["line"], "sequence": h1["sequence"], "in_main": in_main, "ancillary": ancillary})
        for item in self.stack:
            if "link_index" in item:
                self.links[item["link_index"]]["text"] += " " + value

    def scope_product_content(self):
        """Prefer semantic main content over site navigation and suggestions."""
        has_main = any(item.get("in_main") for item in self.text + self.images + self.links)
        for field in ("text", "links", "images", "h1"):
            items = getattr(self, field)
            setattr(self, field, [item for item in items if not item.get("ancillary") and (not has_main or item.get("in_main"))])
        return "main content, excluding semantic navigation and explicit recommendation sections" if has_main else "document body, excluding semantic navigation and explicit recommendation sections"


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _types(node):
    value = node.get("@type", [])
    return {value} if isinstance(value, str) else set(value) if isinstance(value, list) else set()


def _platform(page, product_json=None, product_js=None):
    markers = [item["src"] for item in page.scripts]
    markers += [item["content"] for item in page.meta.get("generator", [])]
    if any(re.search(r"(?:cdn\.shopify\.com|shopifycloud|\bshopify\b)", value, re.I) for value in markers):
        return "shopify"
    for payload in (product_json, product_js):
        if isinstance(payload, dict):
            product = payload.get("product", payload)
            if isinstance(product, dict) and isinstance(product.get("variants"), list) and ("handle" in product or "id" in product):
                return "shopify"
    return "generic"


def _add(report, severity, category, title, location, detail, suggestion):
    report["findings"].append({
        "id": f"PPA-{len(report['findings']) + 1:03d}", "severity": severity,
        "category": category, "title": title,
        "evidence": [{"location": str(location), "detail": _snippet(detail, 500)}],
        "suggestion": suggestion,
    })


def _finish(report):
    counts = {severity: sum(f["severity"] == severity for f in report["findings"]) for severity in ("error", "warning", "info")}
    report["summary"] = {
        "verdict": "issues_found" if counts["error"] or counts["warning"] else "review_required",
        "counts": counts,
    }
    report["not_checked"] = list(dict.fromkeys(report["not_checked"]))
    return _redact_output(report)


def _redact_output(value):
    """Scrub URLs embedded in evidence, errors, and profile-derived text too."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        value = re.sub(r"https?://[^\s<>\"']+", lambda match: redact_url(match.group(0)), value)
        return re.sub(
            r"((?:preview[_-]?key|access[_-]?token|api[_-]?key|password|secret|signature|authorization)=)([^\s&#;]+)",
            r"\1[REDACTED]", value, flags=re.I,
        )
    if isinstance(value, list):
        return [_redact_output(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_output(item) for key, item in value.items()}
    return value


def _new_report(url, platform, mode):
    return {
        "schema_version": "1.0", "url": redact_url(url), "platform": platform, "mode": mode,
        "summary": {}, "findings": [], "not_checked": list(_MANUAL), "evidence": {},
    }


def _page_checks(page, url, profile, report, location="HTML"):
    content_scope = page.scope_product_content()
    title = " ".join(item["text"] for item in page.titles)
    description = [item for item in page.meta.get("description", []) if _clean(item["content"])]
    if not title:
        _add(report, "warning", "metadata", "Page title is missing", location, "No nonempty <title> was found", "Add a unique title identifying this product.")
    if not description:
        _add(report, "warning", "metadata", "Meta description is missing", location, "No nonempty meta description was found", "Add an accurate product description for search previews.")
    if len(page.h1) != 1:
        _add(report, "warning", "metadata", "Review the main product heading", location, f"Found {len(page.h1)} visible H1 headings", "Use one clear main product heading and review any duplicate headings.")
    if not page.canonical:
        _add(report, "warning", "metadata", "Canonical URL is missing", location, "No canonical link was found", "Declare the intended public product URL.")
    elif len(page.canonical) > 1:
        _add(report, "warning", "metadata", "Multiple canonical links", location, f"Found {len(page.canonical)} canonical links", "Keep one unambiguous canonical URL.")
    for entry in page.canonical:
        target = urljoin(url, entry["href"])
        actual, canonical = urlsplit(url), urlsplit(target)
        if canonical.scheme not in {"http", "https"} or not entry["href"]:
            _add(report, "error", "metadata", "Invalid canonical URL", f"{location} line {entry['line']}", redact_url(target), "Use a valid public HTTP(S) canonical URL.")
        elif (actual.hostname, actual.path.rstrip("/")) != (canonical.hostname, canonical.path.rstrip("/")):
            _add(report, "warning", "metadata", "Canonical points to a different product URL", f"{location} line {entry['line']}", redact_url(target), "Confirm the difference is intentional, including collection-specific URL aliases.")
    for entry in page.meta.get("robots", []) + page.meta.get("googlebot", []):
        if re.search(r"\b(?:noindex|none)\b", entry["content"], re.I):
            _add(report, "warning", "seo", "Page requests no indexing", f"{location} line {entry['line']}", entry["content"], "Keep this on preview pages; remove it only when the page should be publicly indexed.")
    products = []
    for item in page.jsonld:
        products.extend((node, item["line"]) for node in _nodes(item["data"]) if "Product" in _types(node))
    for line in page.jsonld_errors:
        _add(report, "warning", "structured_data", "Invalid JSON-LD", f"{location} line {line}", "JSON-LD could not be parsed as JSON", "Correct the structured-data syntax and revalidate.")
    if not products:
        _add(report, "info", "structured_data", "Product structured data was not observed", location, "No Product object was found, including inside @graph", "Check rendered content if structured data is injected by JavaScript.")
    observed_product_count = len(products)
    if len(products) > 1:
        def refers_to_page(node):
            candidates = [node.get("url", ""), node.get("@id", "")]
            for candidate in candidates:
                if isinstance(candidate, str) and candidate:
                    parsed = urlsplit(urljoin(url, candidate))
                    expected = urlsplit(url)
                    if (parsed.hostname, parsed.path.rstrip("/")) == (expected.hostname, expected.path.rstrip("/")):
                        return True
            return False
        selected = [item for item in products if refers_to_page(item[0])]
        if not selected:
            headings = {_clean(item["text"]).casefold() for item in page.h1}
            selected = [item for item in products if _clean(item[0].get("name")).casefold() in headings]
        if not selected:
            report["not_checked"].append("Several JSON-LD Product objects were found, but the current product could not be identified unambiguously; required-field checks were skipped.")
        products = selected
    for product, line in products:
        for key in ("name", "image", "offers"):
            if not product.get(key):
                _add(report, "warning", "structured_data", f"Product structured data lacks {key}", f"{location} line {line}", f"Product object has no usable {key}", "Complete the field from actual product data.")
    for image in page.images:
        if not image["visible"] or image["role"] in {"presentation", "none"}:
            continue
        if not image["has_alt"]:
            _add(report, "warning", "images", "Visible image lacks an alt attribute", f"{location} line {image['line']}", redact_url(urljoin(url, image["src"])), "Describe informative images; use alt=\"\" for deliberately decorative images.")
        elif image["alt"] and re.fullmatch(r"(?:\d+|(?:image|img|photo)[_ -]?\d+)(?:\.[a-z]{2,5})?", image["alt"], re.I):
            _add(report, "warning", "images", "Image alternative text is not descriptive", f"{location} line {image['line']}", image["alt"], "Use concise product-relevant alternative text.")
    model = _clean(profile.get("model"))
    if model:
        headings = " ".join(item["text"] for item in page.h1)
        if not re.search(r"(?<![\w])" + re.escape(model) + r"(?![\w])", headings, re.I):
            _add(report, "warning", "identity", "Configured model is absent from the main heading", location, f"Expected model: {model}; observed H1: {headings or '(missing)'}", "Confirm the page and heading belong to the intended exact model.")
    brand = _clean(profile.get("brand"))
    if brand and not re.search(re.escape(brand), title, re.I):
        _add(report, "info", "identity", "Configured brand is absent from the page title", location, f"Expected brand: {brand}; title: {title or '(missing)'}", "Review the title naming convention for this brand.")
    placeholders = [link for link in page.links if not link["hidden"] and (not link["href"] or link["href"] == "#" or link["href"].lower().startswith("javascript:"))]
    if placeholders:
        _add(report, "info", "links", "Interactive or placeholder links need review", f"{location} line {placeholders[0]['line']}", f"{len(placeholders)} links use an empty, #, or javascript target", "Verify these are functioning controls rather than unfinished navigation links.")
    _spec_checks(page, profile.get("expected_specs", {}), report, location)
    report["evidence"].setdefault("pages", []).append({
        "source": location, "title": title, "h1": [item["text"] for item in page.h1],
        "image_count": len(page.images), "link_count": len(page.links), "product_jsonld_count": observed_product_count,
        "selected_product_jsonld_count": len(products), "content_scope": content_scope,
        "text_segments": [{"text": _snippet(item["text"]), "line": item["line"]} for item in page.text if not item["hidden"]][:300],
        "text_evidence_capped": len([item for item in page.text if not item["hidden"]]) > 300,
    })


def _quantity(value, unit=""):
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z%]+)?\s*", str(value))
    if not match:
        return None
    number = float(match.group(1))
    actual_unit = (unit or match.group(2) or "").lower()
    factors = {"khz": (1000, "hz"), "kg": (1000, "g"), "cm": (10, "mm")}
    if actual_unit in factors:
        factor, actual_unit = factors[actual_unit]
        number *= factor
    return number, actual_unit


def _spec_checks(page, specs, report, location):
    if not isinstance(specs, dict):
        return
    segments = [item for item in page.text if not item["hidden"]]
    for name, specification in specs.items():
        if isinstance(specification, dict):
            expected = specification.get("value", specification.get("expected"))
            label = str(specification.get("label", name))
            unit = str(specification.get("unit", ""))
        else:
            expected, label, unit = specification, str(name), ""
        if expected is None:
            continue
        hits = []
        for index, segment in enumerate(segments):
            if re.search(re.escape(label), segment["text"], re.I):
                # A label cell and the immediately following value cell are
                # common in specification tables. Keep the window small.
                window = " ".join(item["text"] for item in segments[index:index + 2])
                hits.append((segment["line"], window[:300]))
        if not hits:
            report["not_checked"].append(f"Expected specification '{label}' was not found in captured text and could not be compared.")
            continue
        expected_quantity = _quantity(expected, unit)
        checked = False
        for line, window in hits:
            # Remove the label itself so a model number in a label cannot be
            # mistaken for the specification's numeric value.
            value_text = re.split(re.escape(label), window, maxsplit=1, flags=re.I)[-1]
            if expected_quantity:
                candidates = [_quantity(m.group(0)) for m in re.finditer(r"-?\d+(?:\.\d+)?\s*[A-Za-z%]*", value_text)]
                candidates = [q for q in candidates if q and q[1] == expected_quantity[1]]
                if candidates:
                    checked = True
                    incorrect = [q for q in candidates if abs(q[0] - expected_quantity[0]) > 1e-8]
                    if incorrect:
                        severity = "error" if len(candidates) == 1 else "warning"
                        _add(report, severity, "specifications", f"Specification differs from supplied expectation: {label}", f"{location} line {line}", f"Expected {expected}{' ' + unit if unit else ''}; observed: {window}", "Compare this field with the authoritative supplied specification and correct the page or profile.")
            elif _clean(expected).casefold() in _clean(value_text).casefold():
                checked = True
            else:
                checked = True
                _add(report, "warning", "specifications", f"Review supplied specification: {label}", f"{location} line {line}", f"Expected {expected}; observed: {window}", "Confirm the text is semantically consistent with the supplied authoritative value.")
        if not checked:
            report["not_checked"].append(f"Specification '{label}' was found but no unambiguous comparable value was extracted.")


def _product_payload(payload):
    if not isinstance(payload, dict):
        return {}
    product = payload.get("product", payload)
    return product if isinstance(product, dict) and isinstance(product.get("variants"), list) else {}


def _number(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _product_checks(product_json, product_js, profile, report):
    sources = [("Shopify .json", _product_payload(product_json)), ("Shopify .js", _product_payload(product_js))]
    products = [(source, product) for source, product in sources if product]
    if not products:
        report["not_checked"].append("Variant SKU, price, stock, tags, and weight could not be verified from a public product endpoint.")
        if profile.get("min_gallery_images") is not None:
            report["not_checked"].append("The product gallery could not be distinguished from other page images; min_gallery_images was not verified.")
        return
    variants = {}
    for source, product in products:
        for index, variant in enumerate(product.get("variants", []) if isinstance(product.get("variants"), list) else []):
            if not isinstance(variant, dict):
                continue
            key = str(variant.get("id", f"{source}:{index}"))
            record = variants.setdefault(key, {"values": {}, "source": {}, "title": variant.get("title", key)})
            for field, value in variant.items():
                # .json monetary strings are in major currency units; .js
                # numeric monetary values are cents. Do not mix the units.
                if field in {"price", "compare_at_price"}:
                    value = _number(value)
                    if source.endswith(".js") and value is not None:
                        value /= 100
                if field not in record["values"] or record["values"][field] is None:
                    record["values"][field] = value
                    record["source"][field] = source
    known_availability, unknown_availability = 0, 0
    for key, variant in variants.items():
        values = variant["values"]
        label = _snippet(variant["title"])
        source = f"product endpoint variant {key} ({label})"
        if "sku" in values and not _clean(values["sku"]):
            _add(report, "error", "variants", "Variant has an explicitly empty SKU", variant["source"]["sku"] + " " + source, "sku is empty", "Assign the verified SKU for this exact variant.")
        elif "sku" not in values:
            report["not_checked"].append("Some variant SKU fields are not exposed by collected public endpoints.")
        price = _number(values.get("price"))
        compare = _number(values.get("compare_at_price"))
        if price is not None and price < 0:
            _add(report, "error", "pricing", "Variant price is negative", source, f"price={price}", "Correct the price in the authoritative product configuration.")
        if compare is not None and price is not None and compare > 0 and compare <= price:
            _add(report, "warning", "pricing", "Compare-at price is not above the selling price", source, f"price={price}; compare_at_price={compare}", "Confirm the intended discount presentation.")
        if price is None:
            report["not_checked"].append("Some variant prices are absent or could not be interpreted; they were not treated as zero.")
        if "grams" in values:
            weight = _number(values["grams"])
            field = "grams"
        elif "weight" in values:
            weight = _number(values["weight"])
            field = "weight"
        else:
            weight, field = None, "weight"
        if weight is not None and weight <= 0:
            _add(report, "warning", "shipping_data", "Variant weight is zero or negative", source, f"{field}={weight}", "For physical goods, confirm the shipping weight with authoritative logistics data; digital goods may be exempt.")
        if weight is None:
            report["not_checked"].append("Some variant weights are not exposed; absence was not treated as zero.")
        if isinstance(values.get("available"), bool):
            known_availability += 1
        else:
            unknown_availability += 1
    if unknown_availability:
        report["not_checked"].append("Some variant availability is not exposed; absence was not treated as sold out.")
    product = products[0][1]
    tags = product.get("tags")
    if isinstance(tags, str):
        tags = [value.strip() for value in tags.split(",")]
    if not isinstance(tags, list):
        tags = []
        report["not_checked"].append("Product tags were not available from collected public endpoints.")
    dimensions = profile.get("exclusive_tag_dimensions", {})
    if isinstance(dimensions, dict):
        observed = {str(tag).casefold(): str(tag) for tag in tags}
        for dimension, choices in dimensions.items():
            if not isinstance(choices, list):
                continue
            matches = [observed[str(tag).casefold()] for tag in choices if str(tag).casefold() in observed]
            if len(matches) > 1:
                _add(report, "error", "tags", f"Conflicting configured tags: {dimension}", products[0][0] + " tags", ", ".join(matches), "Keep only the applicable tag for this mutually exclusive dimension.")
    gallery = product.get("images")
    minimum = profile.get("min_gallery_images")
    if isinstance(minimum, (int, float)) and isinstance(gallery, list) and len(gallery) < minimum:
        _add(report, "warning", "images", "Product gallery is below the configured minimum", products[0][0] + " images", f"Observed {len(gallery)} images; expected at least {minimum}", "Complete the intended product gallery or revise the explicit threshold.")
    report["evidence"]["product"] = {
        "sources": [source for source, _ in products], "title": product.get("title"),
        "variant_count": len(variants), "availability_known": known_availability,
        "availability_unknown": unknown_availability, "tags": tags,
        "gallery_count": len(gallery) if isinstance(gallery, list) else None,
        "variants": [{
            "id": key, "title": variant["title"],
            "fields": {field: variant["values"].get(field) for field in ("sku", "price", "compare_at_price", "available", "grams", "weight", "weight_unit")},
            "sources": {field: variant["source"].get(field) for field in ("sku", "price", "compare_at_price", "available", "grams", "weight", "weight_unit")},
            "money_unit": "major currency units",
        } for key, variant in list(variants.items())[:300]],
    }
    if isinstance(minimum, (int, float)) and not isinstance(gallery, list):
        report["not_checked"].append("Product gallery data was not exposed, so min_gallery_images could not be verified.")
    if len(variants) > 300:
        report["not_checked"].append("Variant evidence rows are capped at 300; checks still include all collected variants.")


def _rendered_checks(rendered, profile, report):
    if not rendered:
        report["not_checked"].append("JavaScript-rendered desktop/mobile layout, computed visibility, image loading, and typography were not collected.")
        return
    for error in rendered.get("errors", []):
        _add(report, "warning", "browser", "Browser collection reported a problem", "browser capture", str(error), "Review the browser evidence and retry collection if necessary.")
    report["not_checked"].extend(str(item) for item in rendered.get("limitations", []))
    views = rendered.get("views", [])
    image_sizes_known, image_sizes_unknown = 0, 0
    for view in views:
        name = str(view.get("name", "browser"))
        if view.get("horizontal_overflow") is True:
            _add(report, "warning", "layout", "Horizontal page overflow", name, f"Overflow observed at viewport width {view.get('width', 'unknown')}", "Inspect the screenshot and fix overflowing elements or responsive sizing.")
        if view.get("specs_present") is True and view.get("specs_visible") is False:
            _add(report, "warning", "layout", "Specification content is present but hidden", name, "Capture detected specification content but did not find it visible", "Verify specification content is reachable in this viewport, including tabs and accordions.")
        for image in view.get("images", []):
            if image.get("visible") is True and image.get("loaded") is False:
                _add(report, "warning", "images", "A visible image did not finish loading", name, redact_url(str(image.get("src", ""))), "Check lazy loading, consent, network status, and the image resource before declaring it broken.")
            max_kb = profile.get("max_image_kb")
            size = image.get("bytes", image.get("size_bytes"))
            if isinstance(size, (int, float)):
                image_sizes_known += 1
            else:
                image_sizes_unknown += 1
            if isinstance(max_kb, (int, float)) and isinstance(size, (int, float)) and size > max_kb * 1024:
                _add(report, "warning", "images", "Image exceeds the configured size threshold", name, f"{redact_url(str(image.get('src', '')))}: {size} bytes; limit {max_kb} KiB", "Resize/compress the delivered asset while preserving the required visual quality.")
        if view.get("console_errors"):
            _add(report, "warning", "browser", "Browser console errors were observed", name, f"{len(view['console_errors'])} errors; first: {view['console_errors'][0]}", "Review the captured errors to distinguish product-page faults from optional third-party failures.")
        if view.get("resource_failures"):
            _add(report, "info", "browser", "Some browser resources could not be collected", name, f"{len(view['resource_failures'])} resource failures", "Review failure evidence; blocked collection does not establish that a production resource is broken.")
    names = {view.get("name") for view in views}
    if not {"desktop", "mobile"}.issubset(names):
        report["not_checked"].append("Both desktop and mobile captures were not available, so cross-viewport comparison is incomplete.")
    desktop = next((v for v in views if v.get("name") == "desktop"), {})
    mobile = next((v for v in views if v.get("name") == "mobile"), {})
    if desktop.get("specs_visible") is True and mobile.get("specs_present") is False:
        _add(report, "warning", "layout", "Specification content was not detected on mobile", "desktop/mobile comparison", "Desktop specifications visible; mobile specifications not detected", "Review the mobile screenshot and DOM; headings and selectors may differ between layouts.")
    if profile.get("max_image_kb") is not None and (image_sizes_unknown or not image_sizes_known):
        report["not_checked"].append(f"Image size evidence is incomplete ({image_sizes_known} measured, {image_sizes_unknown} unknown); max_image_kb could not be checked for unknown sizes.")
    report["evidence"]["browser"] = {
        "views": [{key: (view[key][:100] if isinstance(view[key], list) else view[key]) for key in (
            "name", "url", "title", "width", "horizontal_overflow", "specs_present", "specs_visible",
            "extraction_complete", "load_note", "scroll_limit_hit", "typography", "console_errors",
            "resource_failures", "blocked_requests", "overflow_elements", "spec_sections", "images",
            "viewport_width", "document_width", "viewport_height", "document_height", "screenshot", "screenshot_capped",
        ) if key in view} for view in views],
        "screenshots": rendered.get("screenshots", []),
    }


def audit_html(html, url="https://example.com/products/demo", *, profile=None, product_json=None, product_js=None, rendered=None):
    """Audit supplied evidence without performing any network requests."""
    profile = profile or {}
    page = PageParser()
    page.feed(html.decode("utf-8", errors="replace") if isinstance(html, bytes) else str(html))
    page.close()
    report = _new_report(url, _platform(page, product_json, product_js), "rendered" if rendered else "static")
    report["not_checked"].append("Link HTTP status was not checked.")
    report["evidence"]["source_html"] = {"title": " ".join(item["text"] for item in page.titles), "image_count": len(page.images), "link_count": len(page.links)}
    page_location = "HTML"
    # Dynamic stores may populate the title, product copy and structured data
    # after hydration. Use captured desktop DOM when it exists, retaining a
    # static-source summary for traceability.
    main_view = next((view for view in (rendered or {}).get("views", []) if view.get("name") == "desktop" and view.get("html")), None)
    if main_view:
        page = PageParser()
        page.feed(str(main_view["html"]))
        page.close()
        visibility = {str(image.get("src")): image for image in main_view.get("images", [])}
        for image in page.images:
            measured = visibility.get(urljoin(main_view.get("url", url), image["src"]))
            if measured is not None and isinstance(measured.get("visible"), bool):
                image["visible"] = measured["visible"]
        page_location = "desktop rendered HTML"
        if report["platform"] == "generic":
            report["platform"] = _platform(page, product_json, product_js)
    # Captcha/interstitial titles are stronger evidence than a generic word in
    # product copy. Do not audit a verification page as if it were the PDP.
    title = " ".join(item["text"] for item in page.titles).strip().lower()
    if title in {"just a moment...", "access denied", "403 forbidden", "verify you are human", "security check", "attention required! | cloudflare"}:
        _add(report, "warning", "collection", "Access challenge captured instead of a product page", "HTML title", title, "Use an authorised browser session or retry after the access challenge is resolved.")
        report["not_checked"].append("Product-page content could not be evaluated because collection returned an access challenge.")
        _rendered_checks(rendered, profile, report)
        return _finish(report)
    _page_checks(page, main_view.get("url", url) if main_view else url, profile, report, location=page_location)
    _product_checks(product_json, product_js, profile, report)
    _rendered_checks(rendered, profile, report)
    if profile.get("max_image_kb") and not rendered:
        report["not_checked"].append("Image transfer sizes were not collected, so max_image_kb could not be checked.")
    return _finish(report)


def _decode(response):
    content_type = response.get("headers", {}).get("content-type", "")
    match = re.search(r"charset=[\"']?([^\s;\"']+)", content_type, re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        return response["body"].decode(encoding, errors="replace")
    except LookupError:
        return response["body"].decode("utf-8", errors="replace")


def _product_endpoints(url):
    parts = urlsplit(url)
    match = re.search(r"/products/([^/]+?)(?:\.(?:json|js))?/?$", parts.path)
    if not match:
        return []
    # Keep locale prefixes but drop collection aliases to address the canonical
    # public product endpoints. Preserve the preview query unchanged.
    prefix = parts.path[:match.start()]
    if "/collections/" in prefix:
        prefix = prefix.split("/collections/", 1)[0]
    return [(extension, urlunsplit((parts.scheme, parts.netloc, f"{prefix}/products/{match.group(1)}.{extension}", parts.query, ""))) for extension in ("json", "js")]


def _same_origin_readonly_url(target, origin_url):
    try:
        parts, origin = urlsplit(target), urlsplit(origin_url)
        if parts.scheme not in {"http", "https"} or parts.username is not None or parts.password is not None:
            return False
        actual_port = parts.port or (443 if parts.scheme == "https" else 80)
        origin_port = origin.port or (443 if origin.scheme == "https" else 80)
        if (parts.scheme, parts.hostname, actual_port) != (origin.scheme, origin.hostname, origin_port):
            return False
        path = unquote(unquote(parts.path))
        if re.search(r"/(?:cart|checkout|account|login|logout|admin|apps|orders)(?:/|$)|\.(?:json|js)$", path, re.I):
            return False
        if re.search(r"(?:^|[?&])(?:action|add|delete|remove|logout|token|auth|key|signature|preview_key)=", unquote(parts.query), re.I):
            return False
        return True
    except (ValueError, TypeError):
        return False


def _safe_link_candidates(page, url):
    origin = urlsplit(url)
    seen = set()
    for link in page.links:
        href = link["href"].strip()
        if link["hidden"] or not href or href.startswith("#"):
            continue
        target = urljoin(url, href)
        if not _same_origin_readonly_url(target, url):
            continue
        parts = urlsplit(target)
        target = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        if target == urlunsplit((origin.scheme, origin.netloc, origin.path or "/", origin.query, "")) or target in seen:
            continue
        seen.add(target)
        yield target, link


def audit_url(url, *, fetcher=None, profile=None, check_links=False, max_links=30, rendered=None):
    """Collect a page and independent public product endpoints, then audit it."""
    if not isinstance(max_links, int) or max_links < 0 or max_links > 500:
        raise ValueError("max_links must be an integer between 0 and 500")
    fetcher = fetcher or SafeFetcher()
    response = fetcher.fetch(url)
    final_url = response.get("url", url)
    html = _decode(response)
    page = PageParser()
    page.feed(html)
    page.close()
    data, collection = {}, []
    if 200 <= response["status"] < 300:
        for extension, endpoint in _product_endpoints(final_url):
            try:
                product_response = fetcher.fetch(endpoint)
                item = {"endpoint": redact_url(endpoint), "final_url": redact_url(product_response.get("url", endpoint)), "status": product_response["status"]}
                if 200 <= product_response["status"] < 300:
                    try:
                        payload = json.loads(_decode(product_response))
                        if isinstance(payload, dict):
                            data[extension] = payload
                        else:
                            item["note"] = "Not a product JSON object"
                    except ValueError:
                        item["note"] = "Response was not valid JSON"
                collection.append(item)
            except (FetchError, UnsafeURLError, OSError, ValueError) as exc:
                collection.append({"endpoint": redact_url(endpoint), "note": type(exc).__name__ + ": collection unavailable"})
    if not 200 <= response["status"] < 300:
        report = _new_report(final_url, "unknown", "rendered" if rendered else "static")
        report["not_checked"].append("Normal product-page checks were skipped because the page response was unsuccessful.")
        _rendered_checks(rendered, profile or {}, report)
    else:
        report = audit_html(html, final_url, profile=profile, product_json=data.get("json"), product_js=data.get("js"), rendered=rendered)
    report["evidence"]["collection"] = {
        "requested_url": redact_url(url), "final_url": redact_url(final_url), "http_status": response["status"],
        "content_type": response.get("headers", {}).get("content-type"), "product_endpoints": collection,
    }
    if response["status"] in {404, 410}:
        _add(report, "error", "network", "Product page is unavailable", redact_url(final_url), f"HTTP {response['status']}", "Check the product URL and publication status.")
    elif not 200 <= response["status"] < 300:
        _add(report, "warning", "network", "Page collection did not return a successful response", redact_url(final_url), f"HTTP {response['status']}", "Check authentication, rate limits, consent, or temporary server issues; page content may be incomplete.")
    robots = response.get("headers", {}).get("x-robots-tag", "")
    if re.search(r"\b(?:noindex|none)\b", robots, re.I):
        _add(report, "warning", "seo", "HTTP header requests no indexing", "HTTP X-Robots-Tag", robots, "Confirm whether this is a preview or intended public product page.")
    if check_links and 200 <= response["status"] < 300:
        report["not_checked"] = [item for item in report["not_checked"] if item != "Link HTTP status was not checked."]
        main_view = next((view for view in (rendered or {}).get("views", []) if view.get("name") == "desktop" and view.get("html")), None)
        if main_view:
            page = PageParser()
            page.feed(str(main_view["html"]))
            page.close()
        page.scope_product_content()
        candidates = list(_safe_link_candidates(page, final_url))
        results = []
        for target, link in candidates[:max_links]:
            try:
                guard = lambda candidate: _same_origin_readonly_url(candidate, final_url)
                linked = fetcher.fetch(target, method="HEAD", url_guard=guard)
                if linked["status"] in {405, 501}:
                    linked = fetcher.fetch(target, method="GET", url_guard=guard)
                status = linked["status"]
                results.append({"url": redact_url(target), "status": status, "final_url": redact_url(linked.get("url", target))})
                if status in {404, 410}:
                    _add(report, "error", "links", "Same-origin link is unavailable", f"HTML line {link['line']}", f"{redact_url(target)} returned HTTP {status}", "Fix or remove the destination link.")
                elif not 200 <= status < 400:
                    _add(report, "warning", "links", "Link needs manual verification", f"HTML line {link['line']}", f"{redact_url(target)} returned HTTP {status}", "Check access controls, rate limiting, and server status; this response alone does not prove a broken link.")
            except (FetchError, UnsafeURLError, OSError, ValueError) as exc:
                results.append({"url": redact_url(target), "note": type(exc).__name__ + ": collection unavailable"})
                _add(report, "info", "links", "Link could not be checked", f"HTML line {link['line']}", redact_url(target), "Review this link manually; network failure is not evidence that the destination is broken.")
        report["evidence"]["link_checks"] = {"eligible": len(candidates), "checked": len(results), "limit": max_links, "results": results}
        report["not_checked"].append("Link checking excludes external destinations and transactional/account/action links; no cart or checkout actions were executed.")
        if len(candidates) > max_links:
            report["not_checked"].append(f"{len(candidates) - max_links} eligible links exceeded the configured link-check limit.")
    else:
        report["not_checked"].append("Link HTTP status was not checked (enable check_links for capped read-only same-origin checks).")
    return _finish(report)
