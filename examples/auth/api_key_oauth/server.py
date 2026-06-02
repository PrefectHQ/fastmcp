"""A FastMCP server protected by API-key-backed OAuth.

OAuth-only clients (Claude Desktop, ChatGPT) connect, get redirected to a
"paste your API key" page, and from then on the server can read each user's key
inside tools — without those clients ever needing to send a custom header.

To run:
    python server.py

Then point an OAuth-capable MCP client at http://127.0.0.1:8000/mcp, or use the
companion client.py.
"""

from provider import API_KEY_CLAIM, APIKeyOAuthProvider

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

SERVER_URL = "http://127.0.0.1:8000"


def validate_api_key(key: str) -> bool:
    # Replace with a real check against your service. Returning True here means
    # any non-empty key is accepted at the authorize step.
    return bool(key)


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
def whoami() -> str:
    """Return the API key the current user authenticated with."""
    token = get_access_token()
    api_key = token.claims[API_KEY_CLAIM]
    # In a real server you would instantiate your client here, e.g.
    #   client = my_service.Client(api_key=api_key)
    return f"Authenticated with API key: {api_key[:4]}…"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)
