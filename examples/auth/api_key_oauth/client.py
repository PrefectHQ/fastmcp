"""Connect to the API-key-backed OAuth server.

Running this triggers the OAuth flow: a browser window opens to the server's
"paste your API key" page. Enter any non-empty key (the demo server accepts
anything) and the connection completes. The `whoami` tool then echoes the key
the server recovered from your token.

To run (with server.py already running):
    python client.py
"""

import asyncio

from fastmcp.client import Client

SERVER_URL = "http://127.0.0.1:8000/mcp"


async def main():
    async with Client(SERVER_URL, auth="oauth") as client:
        assert await client.ping()
        print("✅ Authenticated")
        result = await client.call_tool("whoami", {})
        print(f"🔑 {result.data}")


if __name__ == "__main__":
    asyncio.run(main())
