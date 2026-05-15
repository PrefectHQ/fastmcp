"""OAuth client example for connecting to FastMCP servers.

This example demonstrates how to connect to an OAuth-protected FastMCP server.

To run:
    python client.py
"""

import asyncio

from fastmcp.client import Client
from fastmcp.client.auth import OAuth

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def main():
    # AuthKit defaults DCR clients to client_secret_basic, which conflicts
    # with how MCP SDKs send credentials. Force "none" to register as a
    # public client and avoid token exchange errors.
    auth = OAuth(additional_client_metadata={"token_endpoint_auth_method": "none"})
    async with Client(SERVER_URL, auth=auth) as client:
        assert await client.ping()
        print("Successfully authenticated!")

        tools = await client.list_tools()
        print(f"Available tools ({len(tools)}):")
        for tool in tools:
            print(f"   - {tool.name}: {tool.description}")


if __name__ == "__main__":
    asyncio.run(main())
