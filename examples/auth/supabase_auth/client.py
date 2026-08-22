# For testing since MCPJam doesn't support DCR ..

import asyncio
import logging
import os
import webbrowser

from dotenv import load_dotenv

from fastmcp import Client

load_dotenv()

os.environ["FASTMCP_LOG_LEVEL"] = os.getenv("FASTMCP_LOG_LEVEL", "DEBUG")
os.environ["MCP_LOG_LEVEL"] = os.getenv("MCP_LOG_LEVEL", "DEBUG")
os.environ["HTTPX_LOG_LEVEL"] = os.getenv("HTTPX_LOG_LEVEL", "DEBUG")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_original_open = webbrowser.open


def debug_browser_open(url, *args, **kwargs):
    print("\n================ BROWSER OPEN ================")
    print(url)
    print("==============================================\n")
    return _original_open(url, *args, **kwargs)


webbrowser.open = debug_browser_open


async def main():
    print("🚀 Starting FastMCP OAuth client")

    async with Client(
        "http://localhost:8000/mcp",
        auth="oauth",  # 🔥 THIS triggers proper OAuth + DCR
    ) as client:
        print("✅ Connected to MCP server")

        result = await client.call_tool("hello_supabase", {})
        print("🎉 Tool result:", result)


if __name__ == "__main__":
    asyncio.run(main())
