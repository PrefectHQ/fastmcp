"""OAuth client example for connecting to Scalekit-protected FastMCP servers.

This example demonstrates how to connect to a Scalekit OAuth-protected FastMCP server.

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
            print("✅ Successfully authenticated with Scalekit!")

            tools = await client.list_tools()
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")

            # Test calling a tool
            result = await client.call_tool("echo", {"message": "Hello from Scalekit!"})
            print(f"🎯 Echo result: {result}")

            # Test calling auth status tool
            auth_status = await client.call_tool("auth_status", {})
            print(f"👤 Auth status: {auth_status}")

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
