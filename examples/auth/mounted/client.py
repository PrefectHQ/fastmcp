"""Mounted OAuth servers client example for FastMCP.

This example demonstrates connecting to multiple mounted OAuth-protected MCP servers.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client

GITHUB_URL = "http://127.0.0.1:8000/api/mcp/github/mcp"
GOOGLE_URL = "http://127.0.0.1:8000/api/mcp/google/mcp"


async def main():
    # Connect to GitHub server
    print("\n--- GitHub Server ---")
    try:
        async with Client(GITHUB_URL, auth="oauth") as client:
            assert await client.ping()
            print("✅ Successfully authenticated!")

            tools = await client.list_tools()
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise

    # Connect to Google server
    print("\n--- Google Server ---")
    try:
        async with Client(GOOGLE_URL, auth="oauth") as client:
            assert await client.ping()
            print("✅ Successfully authenticated!")

            tools = await client.list_tools()
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
