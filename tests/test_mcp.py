import asyncio
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP extra not installed")
class MCPTests(unittest.TestCase):
    def test_browser_tool_can_use_sync_browser_api(self):
        from product_page_audit.mcp_server import create_server

        def capture_without_running_event_loop(*args, **kwargs):
            # Like Playwright's sync API, this rejects execution on an active async loop.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return {"views": [], "limitations": ["fixture capture"]}
            raise RuntimeError("Sync browser cannot run in an active event loop")

        async def exercise():
            with patch("product_page_audit.browser.capture", side_effect=capture_without_running_event_loop), \
                 patch("product_page_audit.engine.audit_url", return_value={"schema_version": "1.0", "not_checked": ["fixture"]}):
                server = create_server()
                result = await server.call_tool("audit_product_page", {"url": "https://example.com", "render_browser": True})
                self.assertTrue(result)
        asyncio.run(asyncio.wait_for(exercise(), timeout=10))

    def test_stdio_discovery_and_offline_tool(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def exercise():
            server = StdioServerParameters(command=sys.executable,
                args=["-m", "product_page_audit.mcp_server"], env=dict(os.environ))
            async with stdio_client(server) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual({t.name for t in listing.tools}, {"audit_product_page", "audit_supplied_html"})
                    result = await session.call_tool("audit_supplied_html", {"html": "<h1>Sample</h1>"})
                    self.assertFalse(result.isError)
                    data = result.structuredContent
                    if not data:
                        data = json.loads(next(c.text for c in result.content if c.type == "text"))
                    self.assertEqual(data["schema_version"], "1.0")
                    self.assertTrue(data["not_checked"])
                    blocked = await session.call_tool("audit_product_page", {"url": "http://127.0.0.1/products/test"})
                    self.assertTrue(blocked.isError)
        asyncio.run(asyncio.wait_for(exercise(), timeout=30))
