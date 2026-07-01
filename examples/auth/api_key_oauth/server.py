"""A FastMCP server protected by API-key-backed OAuth.

OAuth-only clients (Claude Desktop, ChatGPT) connect, get redirected to a
consent page that asks for an API key, and from then on the server can read each
user's key inside tools — without those clients ever needing to send a custom
header.

Two integration points are yours to fill in:

- `validate_api_key` verifies the pasted key against your backend before a token
  is minted. It may be async, so it can make an HTTP call.
- Inside a tool, the access token's claims carry the key the user authenticated
  with, ready to construct whatever client you need.

To run:
    python server.py

Set API_VERIFY_URL to point validation at a real backend; without it the demo
accepts any non-empty key. Then point an OAuth-capable MCP client at
http://127.0.0.1:8000/mcp, or use the companion client.py.
"""

import os

import httpx
from provider import API_KEY_CLAIM, APIKeyOAuthProvider

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken

SERVER_URL = "http://127.0.0.1:8000"

# The endpoint that confirms a key is valid. Yours might be a "get current user"
# route that returns 200 for a good key and 401 for a bad one.
API_VERIFY_URL = os.environ.get("API_VERIFY_URL")


async def validate_api_key(key: str) -> bool:
    """Confirm the pasted key is real before issuing a token.

    Rejecting a bad key here produces a clear failure on the consent page
    instead of a token that breaks on the first tool call. The demo accepts any
    non-empty key when no backend is configured.
    """
    if not key:
        return False
    if API_VERIFY_URL is None:
        return True
    async with httpx.AsyncClient() as client:
        response = await client.get(
            API_VERIFY_URL, headers={"Authorization": f"Bearer {key}"}
        )
        return response.is_success


auth = APIKeyOAuthProvider(
    base_url=SERVER_URL,
    # Derives the token signing key and the storage encryption key. Load this
    # from your secret store in production; the same secret keeps previously
    # issued tokens valid across restarts.
    jwt_signing_key="change-me-to-a-real-secret",
    validate_api_key=validate_api_key,
)

mcp = FastMCP("API-Key OAuth Demo", auth=auth)


@mcp.tool
async def list_files(token: AccessToken = CurrentAccessToken()) -> list[str]:
    """List the caller's files, using the key they authenticated with."""
    api_key = token.claims[API_KEY_CLAIM]
    # Construct your own client from the key and call your service. Here we just
    # echo a masked key so the demo runs without a backend.
    #   client = my_service.Client(api_key=api_key)
    #   return await client.list_files()
    return [f"(demo) authenticated with {api_key[:4]}…"]


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)
