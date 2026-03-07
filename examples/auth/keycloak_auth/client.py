"""OAuth client example for connecting to FastMCP servers.

This example demonstrates how to connect to a Keycloak-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp import Client

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
            claims = result.data
            print("📄 Available access token claims:")
            print(f"   - sub: {claims.get('sub', 'N/A')}")
            print(f"   - name: {claims.get('name', 'N/A')}")
            print(f"   - given_name: {claims.get('given_name', 'N/A')}")
            print(f"   - family_name: {claims.get('family_name', 'N/A')}")
            print(f"   - preferred_username: {claims.get('preferred_username', 'N/A')}")
            print(f"   - scope: {claims.get('scope', [])}")

    except Exception as e:
        print(f"❌ Authentication failed: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Graceful shutdown, suppress noisy logs resulting from asyncio.run task cancellation propagation
        pass
