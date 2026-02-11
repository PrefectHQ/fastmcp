"""OAuth client example for connecting to FastMCP servers.

This example demonstrates how to connect to an OAuth-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client, OAuth

SERVER_URL = "http://localhost:8000/mcp"


async def main():
    try:
        async with Client(
            SERVER_URL,
            auth=OAuth(
                # Replace with your own CIMD document URL
                client_metadata_url="https://www.jlowin.dev/mcp-client.json",
            ),
        ) as client:
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
