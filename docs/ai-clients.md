# AI client setup

Install the Python package first using the [quick start](../README.md#quick-start). Installing or copying a Skill only provides instructions; it does not install `pdp-audit`.

Choose either a Skill with command-line access or a local MCP connection. Both use the same engine. The client must run on a computer where the package is installed, or explicitly support connecting to that computer. Configuration compatibility is distinct from an end-to-end test in a particular client version.

## Codex: Skill or MCP

Copy the whole `skills/product-page-audit` folder to `.agents/skills/product-page-audit` in your project, or `~/.agents/skills/product-page-audit` for your user. Ask Codex to use `$product-page-audit`. These locations follow the [Codex Skill documentation](https://developers.openai.com/codex/skills).

Keep the installed CLI executable on the agent's PATH, or tell it the absolute virtual-environment executable path. If it is not available, the Skill uses supplied evidence rather than claiming a live run.

For MCP, install the extra (`python -m pip install ".[mcp]"` from the repository) and merge this into `~/.codex/config.toml`, replacing the path:

```toml
[mcp_servers.product_page_audit]
command = "/absolute/path/product-page-audit/.venv/bin/pdp-audit-mcp"
```

On Windows, a TOML literal string avoids escaping backslashes:

```toml
[mcp_servers.product_page_audit]
command = 'C:\Tools\product-page-audit\.venv\Scripts\pdp-audit-mcp.exe'
```

Check the client's MCP status after loading the configuration. [Codex MCP reference](https://developers.openai.com/codex/mcp)

## Claude Code

Copy `skills/product-page-audit` to `.claude/skills/product-page-audit` in the project or `~/.claude/skills/product-page-audit` for your user. Invoke `/product-page-audit` with a product URL and any source specifications. [Claude Code Skills reference](https://code.claude.com/docs/en/skills)

Alternatively, after installing the MCP extra:

```sh
claude mcp add --transport stdio product-page-audit -- /absolute/path/product-page-audit/.venv/bin/pdp-audit-mcp
claude mcp get product-page-audit
```

Use the Windows executable path on Windows. An “added” message verifies configuration was written; the connection status verifies that the client can start the server. [Claude Code MCP reference](https://code.claude.com/docs/en/mcp)

## Cursor, Gemini CLI and Claude Desktop

These clients accept a local server definition under `mcpServers`. Merge this entry with existing entries, keeping the other settings:

```json
{
  "mcpServers": {
    "product-page-audit": {
      "command": "/absolute/path/product-page-audit/.venv/bin/pdp-audit-mcp",
      "args": []
    }
  }
}
```

For Windows, the `command` value is, for example, `"C:\\Tools\\product-page-audit\\.venv\\Scripts\\pdp-audit-mcp.exe"`.

| Client | Configuration location |
| --- | --- |
| Cursor | Project `.cursor/mcp.json`, or user `~/.cursor/mcp.json` |
| Gemini CLI | Project `.gemini/settings.json`, or user `~/.gemini/settings.json` |
| Claude Desktop | Open Settings → Developer → Edit Config; on macOS the file is under `~/Library/Application Support/Claude/`, on Windows under `%APPDATA%\Claude\` |

The configuration follows the official [Cursor MCP guide](https://docs.cursor.com/context/model-context-protocol), [Gemini CLI MCP guide](https://geminicli.com/docs/tools/mcp-server/) and [MCP desktop quickstart](https://github.com/modelcontextprotocol/docs/blob/main/quickstart/user.mdx). Reload the client as required and check that both audit tools appear. Organization policy or client version may restrict local MCP servers.

## Available MCP tools

| Tool | Inputs | Purpose |
| --- | --- | --- |
| `audit_product_page` | `url`, optional `check_links=false`, optional `render_browser=false` | Fetch a public product page and return an audit report |
| `audit_supplied_html` | `html`, `url` | Inspect HTML supplied directly in the call, without fetching the page |

Browser mode needs the browser extra and Chromium installed in the same environment as the server. MCP link checks are capped at 20 same-origin destinations. Product profiles are currently a CLI feature. MCP does not expose arbitrary local file reads, credentials, shell execution, output paths, or the CLI's `--allow-local` option. It returns findings to the AI client; the client can save or present the result within the user's requested scope.

This server uses local stdio: the client starts it and communicates over the process's input/output. It is not an internet-hosted service. Running `pdp-audit-mcp` manually may appear to wait because it is waiting for MCP messages.

## Web chat and other assistants

When the AI environment has no local command or MCP capability, generate a report separately and upload it with screenshots and specifications. Use [attachment-review.md](attachment-review.md). This workflow is model-independent and does not assume any vendor's browser, execution, or mobile-testing capability.

For any other MCP client, use the same executable with a **local stdio** transport and inspect its tool list. A remote-only connector cannot directly launch this local executable. Other Agent Skills clients may read the folder using the [Agent Skills specification](https://agentskills.io/specification); discovery paths and executable permissions are client-specific.

## Troubleshooting

- **Command not found:** use the absolute executable in the virtual environment, not a relative path or an alias defined only in your shell.
- **Missing MCP/browser dependency:** install the relevant extra with the same Python interpreter used by the executable.
- **Browser executable missing:** run `python -m playwright install chromium` with that interpreter.
- **Page blocked or requires login:** use an authorized HTML export and supplied screenshots; record live checks as incomplete.
- **No tools in the client:** inspect the client's MCP status/logs; configuration being saved does not prove connection.
- **Report contains unknown checks:** provide missing source documents or enable the relevant mode; do not convert unknowns into passes.
