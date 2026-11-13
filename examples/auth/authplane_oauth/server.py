"""Authplane OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Authplane,
a self-hosted OAuth 2.1 authorization server for MCP.

Before running, register this server's resource URL in Authplane (see README).

To run:
    AUTHPLANE_ISSUER=https://your-authplane.com python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.authplane import AuthplaneAuthProvider
from fastmcp.server.dependencies import get_access_token

auth = AuthplaneAuthProvider(
    issuer=os.getenv("AUTHPLANE_ISSUER") or "http://localhost:9000",
    base_url="http://127.0.0.1:8000",
    required_scopes=["tools/read"],
)

mcp = FastMCP("Authplane Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
async def get_access_token_claims() -> dict:
    """Get the authenticated user's access token claims."""
    token = get_access_token()
    if token is None:
        return {"error": "Not authenticated"}
    return {
        "sub": token.claims.get("sub"),
        "scope": token.claims.get("scope"),
        "aud": token.claims.get("aud"),
        "client_id": token.claims.get("client_id"),
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
