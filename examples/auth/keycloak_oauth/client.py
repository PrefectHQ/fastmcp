"""OAuth client example for connecting to a Keycloak-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp import Client

SERVER_URL = "http://localhost:8000/mcp"


async def main():
    async with Client(SERVER_URL, auth="oauth") as client:
        assert await client.ping()
        print("Successfully authenticated!")

        tools = await client.list_tools()
        print(f"Available tools ({len(tools)}):")
        for tool in tools:
            print(f"   - {tool.name}: {tool.description}")

        print("Calling protected tool: get_access_token_claims")
        result = await client.call_tool("get_access_token_claims")
        claims = result.data
        print(f"   sub: {claims.get('sub', 'N/A')}")
        print(f"   scope: {claims.get('scope', 'N/A')}")
        print(f"   azp: {claims.get('azp', 'N/A')}")


if __name__ == "__main__":
    asyncio.run(main())
