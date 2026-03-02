"""OAuth client example for connecting to PropelAuth-protected FastMCP servers.

This example demonstrates how to connect to a PropelAuth OAuth-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def main():
    try:
        async with Client(SERVER_URL, auth="oauth") as client:
            assert await client.ping()
            print("✅ Successfully authenticated with PropelAuth!")

            tools = await client.list_tools()
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")

            # Test calling a tool
            result = await client.call_tool(
                "echo", {"message": "Hello from PropelAuth!"}
            )
            print(f"🎯 Echo result: {result}")

            # Test calling whoami tool
            whoami = await client.call_tool("whoami", {})
            print(f"👤 Who am I: {whoami}")

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
