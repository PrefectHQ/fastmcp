"""OAuth client example for connecting to FastMCP servers.

This example demonstrates how to connect to an OAuth-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def main():
    try:
        async with Client(SERVER_URL, auth="oauth") as client:
            tools = await client.list_tools()
            print("✅ Successfully authenticated!")
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")

            # Test the protected tool
            print("🔒 Calling protected tool: get_user_info")
            result = await client.call_tool("get_user_info")
            print(f"📄 ZITADEL user: {result.data}")

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
