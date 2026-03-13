"""PropelAuth OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with PropelAuth OAuth.

Required environment variables:
- PROPELAUTH_AUTH_URL: Your PropelAuth Auth URL (from Backend Integration page)
- PROPELAUTH_INTROSPECTION_CLIENT_ID: Introspection Client ID (from MCP > Request Validation)
- PROPELAUTH_INTROSPECTION_CLIENT_SECRET: Introspection Client Secret (from MCP > Request Validation)

Optional:
- PROPELAUTH_REQUIRED_SCOPES: Comma-separated scopes tokens must include
- BASE_URL: Public URL where the FastMCP server is exposed (defaults to `http://localhost:8000/`)

To run:
    python server.py
"""

import os

from dotenv import load_dotenv

from fastmcp import FastMCP
from fastmcp.server.auth.providers.propelauth import PropelAuthProvider
from fastmcp.server.dependencies import get_access_token

load_dotenv()

auth = PropelAuthProvider(
    auth_url=os.environ["PROPELAUTH_AUTH_URL"],
    introspection_client_id=os.environ["PROPELAUTH_INTROSPECTION_CLIENT_ID"],
    introspection_client_secret=os.environ["PROPELAUTH_INTROSPECTION_CLIENT_SECRET"],
    base_url=os.getenv("BASE_URL", "http://localhost:8000/"),
)

mcp = FastMCP("PropelAuth OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
def whoami() -> dict:
    """Return the authenticated user's ID."""
    token = get_access_token()
    if token is None:
        return {"error": "Not authenticated"}
    return {"user_id": token.claims.get("sub")}


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
