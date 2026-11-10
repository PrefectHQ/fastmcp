"""Auth0 Auth for MCP client example."""

import asyncio

from fastmcp import Client
from fastmcp.client.auth import OAuth

auth = OAuth(
    additional_client_metadata={"token_endpoint_auth_method": "none"},
    callback_host="127.0.0.1",
)


async def main() -> None:
    async with Client("http://127.0.0.1:8000/mcp", auth=auth) as client:
        result = await client.call_tool("echo", {"message": "hello"})
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
