# Product Page Audit · 产品页检查工具

[English](README.md)

把产品页检查变成可重复执行的流程：自动读取网页证据、输出检查报告，再交给你正在使用的 AI 分析文案、核对规格书和审查截图。支持普通产品网页，并对 Shopify 增加公开商品数据检查。

工具提供命令行、Agent Skill 和本地 MCP 三种入口，不绑定某一家 AI，也不需要配置 AI API 密钥。

## 安装和使用

需要 Python 3.10 及以上版本和 Git。当前从 GitHub 安装，**尚未发布到 PyPI**。

macOS / Linux：

```sh
git clone https://github.com/SvenKunkka/product-page-audit.git
cd product-page-audit
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/pdp-audit --html examples/demo-product.html --url https://example.com/products/demo --profile examples/profile.json --out reports
```

Windows PowerShell：

```powershell
git clone https://github.com/SvenKunkka/product-page-audit.git
Set-Location product-page-audit
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\pdp-audit.exe --html examples/demo-product.html --url https://example.com/products/demo --profile examples/profile.json --out reports
```

上面的命令检查仓库内的虚构示例，离线即可运行；示例特意保留一处图片 alt 缺失，用于展示报告。每次检查在输出目录创建独立文件夹，保存 `report.json` 和 `report.md`，终端返回文件路径。报告生成成功不代表产品页已经符合上线条件。

检查真实网页时，运行 `pdp-audit 真实产品页地址 --out reports`。下文的 `https://example.com/products/demo` 是占位地址，请换成真实产品页。

## 不同 AI 怎么使用

| 工具类型 | 使用方式 |
| --- | --- |
| Codex、Claude Code | 安装 Skill 并配合命令行，也可以接入 MCP |
| Cursor、Gemini CLI、Claude Desktop | 配置本地 MCP，调用检查工具 |
| 有终端能力的其他 AI | 直接运行同一套命令行 |
| 没有本地工具能力的 ChatGPT、DeepSeek、Claude、Gemini 网页对话 | 上传报告、页面文字、截图和规格书，让 AI 审查附件 |

具体设置见 [AI 客户端接入指南](docs/ai-clients.md)。这里的适配基于客户端公开接口，不表示每一个客户端都已完成实机端到端测试。仅上传一个 Skill 文件，不会让没有执行环境的网页对话自动获得本机浏览器或脚本运行能力。

## 检查哪些问题

- 页面标题、H1、描述、canonical、robots 和商品结构化数据。
- Shopify 公开数据中的变体、SKU、价格、重量和标签，缺失数据注明无法核实。
- 图片 alt、图片标记及可获得的尺寸信息。
- 链接目标，以及可选的限量链接连通性检查。
- 按产品配置文件核对指定型号和规格值。
- 可选浏览器模式下的桌面、手机宽度测量结果。
- 由 AI 结合证据审查型号混用、文案矛盾、图片内容和规格书差异。

检查结果明确区分“发现问题”和“尚未检查”。SKU/EAN 真伪、实际库存、运费、审批价格、真机效果等，需要对应的权威资料或人工验证，不能用公开页面数据代替。[完整检查边界](docs/checks.md)

## 开启浏览器检查

```sh
.venv/bin/python -m pip install ".[browser,mcp]"
.venv/bin/python -m playwright install chromium
.venv/bin/pdp-audit https://example.com/products/demo --browser --check-links --max-links 30 --out reports
```

Windows 将执行路径换为 `.\.venv\Scripts\python.exe` 和 `.\.venv\Scripts\pdp-audit.exe`。浏览器适配使用 Playwright，不依赖某个 AI 的专有工具名称。网页需要登录或受访问限制时，报告应保留未完成项。

## 本地资料和产品要求

已有网页 HTML 可以离线检查：

```sh
.venv/bin/pdp-audit --html saved-page.html --url https://example.com/products/demo --out reports
```

用 [示例配置](examples/profile.json) 指定品牌、型号、预期参数、图片要求和互斥标签：

```sh
.venv/bin/pdp-audit https://example.com/products/demo --profile examples/profile.json --out reports
```

示例均为虚构数据，请替换成自己已经确认的要求。没有对应规格书时，只能检查页面自身或配置值的一致性，不能认定产品事实已经正确。公司内部资料和真实报告不要放进公开仓库。

## 给 AI 的使用示例

> 检查这个产品页是否存在影响上线的问题：https://example.com/products/demo。请结合我提供的规格书核对参数，给每个问题附上证据，并说明哪些项目没有检查完成。

Skill 位于 [skills/product-page-audit](skills/product-page-audit)，可复制到客户端支持的 Skill 目录；Python 工具需要另行安装。只有网页对话能力时，使用 [附件审查提示词](docs/attachment-review.md)。

## 来源与许可

本项目受用户提供的 `shopify-pdp-audit` 工作流程启发，重新编写了通用实现和公开检查规则。原包、原作者私有清单和本机配置不随仓库发布；详见 [来源说明](NOTICE.md)。本仓库的新代码和文档采用 [MIT 许可](LICENSE)。
