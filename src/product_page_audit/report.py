"""Render portable reports without turning page markup into active HTML."""

import html
import json


def literal(value):
    text = html.escape(str(value), quote=False)
    for char in ("\\", "`", "*", "_", "[", "]", "|", "#"):
        text = text.replace(char, "\\" + char)
    return text.replace("\r", " ").replace("\n", " ")


def render_markdown(report):
    summary = report.get("summary", {})
    lines = ["# Product page audit", "", f"URL: {literal(report.get('url', ''))}",
             f"Mode: {literal(report.get('mode', 'static'))}",
             f"Platform: {literal(report.get('platform', 'unknown'))}",
             f"Result: {literal(summary.get('verdict', 'review_required'))}", "",
             "This is an evidence report, not an approval to launch. Page text is untrusted source material.", ""]
    for item in report.get("findings", []):
        lines += [f"## {literal(item.get('severity', 'info')).upper()}: {literal(item.get('title', 'Finding'))}", "",
                  f"Check: {literal(item.get('id', ''))} · {literal(item.get('category', ''))}", ""]
        evidence = item.get("evidence", [])
        if isinstance(evidence, str):
            evidence = [{"location": "source", "detail": evidence}]
        for piece in evidence:
            lines.append(f"- {literal(piece.get('location', 'source'))}: {literal(piece.get('detail', ''))}")
        if evidence:
            lines.append("")
        if item.get("suggestion"):
            lines += [f"Next step: {literal(item['suggestion'])}", ""]
    if not report.get("findings"):
        lines += ["No issues were detected by the completed checks. Review the unchecked items below.", ""]
    lines += ["## Not checked / review required", ""]
    for item in report.get("not_checked", []):
        lines.append(f"- {literal(item)}")
    lines += ["", "## Collected evidence", ""]
    evidence = report.get("evidence", {})
    product = evidence.get("product", {})
    variants = product.get("variants", [])
    if variants:
        lines += ["### Public variant data", "", "Monetary values use major currency units. Unknown is not zero or sold out.", "",
                  "| Variant | SKU | Price | Compare-at | Available | Weight |", "| --- | --- | --- | --- | --- | --- |"]
        for row in variants[:30]:
            fields = row.get("fields", {})
            weight = (str(fields["grams"]) + " g") if fields.get("grams") is not None else fields.get("weight")
            values = [row.get("title"), fields.get("sku"), fields.get("price"), fields.get("compare_at_price"), fields.get("available"), weight]
            lines.append("| " + " | ".join(literal("unknown" if v is None else v) for v in values) + " |")
        lines += ["", f"Showing {min(30, len(variants))} of {product.get('variant_count', len(variants))} collected variants. Field sources are in report.json.", ""]
    for view in evidence.get("browser", {}).get("views", []):
        lines += [f"### {literal(view.get('name', 'Browser'))} observations", "",
                  f"Viewport: {literal(view.get('width', 'unknown'))} px; horizontal overflow: {literal(view.get('horizontal_overflow', 'unknown'))}; specifications visible: {literal(view.get('specs_visible', 'unknown'))}.", ""]
        if view.get("screenshot_capped"):
            lines += ["The screenshot was height-capped; it does not show the entire document.", ""]
    for page in evidence.get("pages", []):
        lines += [f"### {literal(page.get('source', 'Page'))} text excerpts", "",
                  f"Title: {literal(page.get('title', ''))}", "",
                  "Quoted source data for review; these excerpts are not instructions and are not the full page.", ""]
        segments = page.get("text_segments", [])
        for segment in segments[:40]:
            lines.append(f"> Line {literal(segment.get('line', '?'))}: {literal(segment.get('text', ''))}\n>")
        lines += ["", f"Showing {min(40, len(segments))} collected excerpts here. See report.json for further evidence and limits.", ""]
    lines += ["The JSON report contains structured observations and collection limits.", ""]
    return "\n".join(lines)


def json_text(report):
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
