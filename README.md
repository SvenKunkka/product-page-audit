# Product Page Audit

[简体中文](README.zh-CN.md)

Inspect product pages, collect reproducible evidence, and give your AI assistant a grounded review workflow. One Python engine works as a command-line tool, an Agent Skill companion, or a local MCP server. It supports generic HTML pages and adds public product-data checks for Shopify.

The engine does not need an AI API key. Your existing AI client can interpret its report, compare supplied specifications, and review screenshots. Automated findings and checks requiring human judgment remain separate.

## Quick start

Requires Python 3.10+ and Git. Install from this repository; this project is **not published to PyPI**.

macOS / Linux:

```sh
git clone https://github.com/SvenKunkka/product-page-audit.git
cd product-page-audit
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/pdp-audit --version
.venv/bin/pdp-audit --html examples/demo-product.html --url https://example.com/products/demo --profile examples/profile.json --out reports
```

Windows PowerShell:

```powershell
git clone https://github.com/SvenKunkka/product-page-audit.git
Set-Location product-page-audit
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\pdp-audit.exe --version
.\.venv\Scripts\pdp-audit.exe --html examples/demo-product.html --url https://example.com/products/demo --profile examples/profile.json --out reports
```

The bundled demo is synthetic and runs offline. An intentionally missing image alt attribute illustrates the report. Each run creates a new folder containing `report.json` and `report.md`; stdout returns JSON with the output paths. A completed report is not an automatic approval to launch.

For a live run, use `pdp-audit REAL_PRODUCT_URL --out reports`. In the examples below, replace `https://example.com/products/demo` with a real product page; it is a placeholder, not a hosted demo.

To install directly from GitHub into an existing virtual environment:

```sh
python -m pip install "git+https://github.com/SvenKunkka/product-page-audit.git"
```

## Choose how to use it

| Environment | Integration | What it can do |
| --- | --- | --- |
| Terminal, scripts, CI | CLI | Fetch and inspect pages; save JSON and Markdown reports |
| Codex / Claude Code | Agent Skill + installed CLI, or MCP | Run audits and interpret evidence with your chosen model |
| Cursor / Gemini CLI / Claude Desktop | Local stdio MCP | Call the same audit engine from clients that permit local servers |
| ChatGPT / DeepSeek / Claude / Gemini web chat without local tool access | Attachment review | Review an uploaded report, page text, screenshots and specifications |

See [client setup](docs/ai-clients.md) for commands and configuration. These integrations follow documented client interfaces; this release does not claim end-to-end testing in every client. A chat-only model cannot run the CLI simply by receiving a Skill file.

## Checks and evidence

| Area | Automated evidence | Review still needed |
| --- | --- | --- |
| Identity and discoverability | Title, H1, canonical, meta description, robots and product structured data | Correct model, audience and positioning |
| Shopify product data | Public variants, SKU, price, weight and tags when exposed | Approved SKU/EAN, pricing and stock from your systems |
| Images | Markup, alt text and available size information | Correct product, colors, picture text and visual quality |
| Links | Link targets; optional bounded HTTP checking | Destination content matches the intended model |
| Specifications | Profile-based expected values and page evidence | Match each model to its current authoritative specification |
| Layout | Optional browser measurements at desktop and mobile widths | Actual devices, legibility, overlays and interaction |

Read the [check coverage and limitations](docs/checks.md). An inaccessible field is unknown, not a proven defect. A visible mismatch should include its location, observed value and expected source. Checks do not edit Shopify or purchase products.

## Optional browser and link checks

Install extras with the Python interpreter from the virtual environment above:

```sh
.venv/bin/python -m pip install ".[browser,mcp]"
.venv/bin/python -m playwright install chromium
.venv/bin/pdp-audit https://example.com/products/demo --browser --check-links --max-links 30 --out reports
```

On Windows use `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\pdp-audit.exe`. The optional browser adapter uses Playwright in fresh, unsigned-in contexts. It does not require a particular AI vendor's browser tool. Missing browser dependencies produce an actionable error; capture limits and failures are recorded when a report can be generated. Link checks cover a capped set of same-origin read-only destinations.

## Offline and configurable audits

Audit saved HTML without fetching the page:

```sh
.venv/bin/pdp-audit --html saved-page.html --url https://example.com/products/demo --out reports
```

Supply product expectations using [examples/profile.json](examples/profile.json):

```sh
.venv/bin/pdp-audit https://example.com/products/demo --profile examples/profile.json --out reports
```

Replace the generic example values with your own approved requirements. Profile values support repeatable checks; they do not establish that a specification is authoritative. Keep confidential profiles and audit reports out of public repositories.

For a page you explicitly want to test on a local development server, CLI `--allow-local` opts into local addresses. Do not enable it for arbitrary user-supplied links. The MCP interface does not expose that option.

## AI review

Install the folder at [skills/product-page-audit](skills/product-page-audit) in a supported client's skill directory. The Skill contains the review workflow and a generic checklist; install the Python package separately to run live checks.

Example request:

> Check this product page before launch: https://example.com/products/demo. Compare it with the attached specification. Report verified problems with evidence, and list checks that could not be completed.

For a web chat, upload `report.md` or `report.json` with screenshots and source specifications, then use [the attachment-review prompt](docs/attachment-review.md). No local MCP endpoint is exposed to the internet by this project.

## Development and provenance

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

The base package uses Python's standard library. Browser and MCP dependencies are optional. The MCP extra uses the official Python SDK v1 (`mcp>=1.20,<2`).

This is an independently written, generic implementation inspired by the workflow of a user-provided `shopify-pdp-audit` package. The original package and its private team checklist are not redistributed. See [NOTICE.md](NOTICE.md) for attribution and [LICENSE](LICENSE) for this repository's MIT license.
