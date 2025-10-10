"""OAuth client example for connecting to FastMCP servers.

This example demonstrates how to connect to an OAuth-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client

SERVER_URL = "http://localhost:8000/mcp"


async def main():
    try:
        async with Client(SERVER_URL, auth="oauth") as client:
            assert await client.ping()
            print("✅ Successfully authenticated!")

            tools = await client.list_tools()
            print(f"🔧 Available tools ({len(tools)}):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")

            # Test the protected tool
            print("🔒 Calling protected tool: get_access_token_claims")
            result = await client.call_tool("get_access_token_claims")
            user_data = result.data
            print("📄 Available access token claims:")
            print(f"   - sub: {user_data.get('sub', 'N/A')}")
            print(f"   - username: {user_data.get('username', 'N/A')}")
            print(f"   - cognito:groups: {user_data.get('cognito:groups', [])}")

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
