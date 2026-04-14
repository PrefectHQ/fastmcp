"""Clerk OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Clerk OAuth.

Required environment variables:
- FASTMCP_SERVER_AUTH_CLERK_DOMAIN: Your Clerk instance domain
    (e.g., "saving-primate-16.clerk.accounts.dev")
- FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID: Your Clerk OAuth client ID
- FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET: Your Clerk OAuth client secret

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.clerk import ClerkProvider

auth = ClerkProvider(
    domain=os.getenv("FASTMCP_SERVER_AUTH_CLERK_DOMAIN") or "",
    client_id=os.getenv("FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET") or "",
    base_url="http://127.0.0.1:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
    # Optional: specify required scopes (defaults to ["openid", "email", "profile"])
    # required_scopes=["openid", "email", "profile", "public_metadata"],
)

mcp = FastMCP("Clerk OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
