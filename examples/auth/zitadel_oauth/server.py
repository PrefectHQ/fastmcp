"""ZITADEL OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with ZITADEL.

Required environment variables:
- FASTMCP_SERVER_AUTH_ZITADEL_DOMAIN: Your ZITADEL instance domain
  (e.g., "https://my-instance-abc123.zitadel.cloud")
- FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_ID: Your ZITADEL web application client ID
- FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_SECRET: Your ZITADEL web application client secret

To run:
    python server.py
"""

import logging
import os

from dotenv import load_dotenv

from fastmcp import FastMCP
from fastmcp.server.auth.providers.zitadel import ZitadelProvider
from fastmcp.server.dependencies import get_access_token

logging.basicConfig(level=logging.DEBUG)

load_dotenv(".env", override=True)

auth = ZitadelProvider(
    domain=os.getenv("FASTMCP_SERVER_AUTH_ZITADEL_DOMAIN") or "",
    client_id=os.getenv("FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_ZITADEL_CLIENT_SECRET") or "",
    base_url="http://127.0.0.1:8000",
    # Add "offline_access" once the Refresh Token grant is enabled in ZITADEL
    required_scopes=["openid", "profile", "email"],
)

mcp = FastMCP("ZITADEL OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
async def get_user_info() -> dict:
    """Get information about the authenticated ZITADEL user."""
    token = get_access_token()
    if token is None:
        return {"error": "Not authenticated"}
    return {
        "sub": token.claims.get("sub"),
        "username": token.claims.get("username"),
        "email": token.claims.get("email"),
        "roles": list(token.claims.get("urn:zitadel:iam:org:project:roles", {})),
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
