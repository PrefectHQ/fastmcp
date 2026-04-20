"""Keycloak OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Keycloak OAuth.

Required: Keycloak 26.6.0 or later with Dynamic Client Registration enabled.

To run:
    KEYCLOAK_REALM_URL=https://your-keycloak.com/realms/myrealm python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from fastmcp.server.dependencies import get_access_token

auth = KeycloakAuthProvider(
    realm_url=os.getenv("KEYCLOAK_REALM_URL") or "http://localhost:8080/realms/fastmcp",
    base_url="http://127.0.0.1:8000",
    # audience="http://127.0.0.1:8000",  # Recommended for production
)

mcp = FastMCP("Keycloak Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
async def get_access_token_claims() -> dict:
    """Get the authenticated user's access token claims."""
    token = get_access_token()
    return {
        "sub": token.claims.get("sub"),
        "scope": token.claims.get("scope"),
        "azp": token.claims.get("azp"),
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
