"""WorkOS OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with WorkOS OAuth.

Required environment variables:
- WORKOS_CLIENT_ID: Your WorkOS Connect application client ID
- WORKOS_CLIENT_SECRET: Your WorkOS Connect application client secret
- WORKOS_AUTHKIT_DOMAIN: Your AuthKit domain (e.g., "https://your-app.authkit.app")

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import WorkOSProvider

auth = WorkOSProvider(
    client_id=os.getenv("WORKOS_CLIENT_ID") or "",
    client_secret=os.getenv("WORKOS_CLIENT_SECRET") or "",
    authkit_domain=os.getenv("WORKOS_AUTHKIT_DOMAIN") or "https://your-app.authkit.app",
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
)

mcp = FastMCP("WorkOS OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
