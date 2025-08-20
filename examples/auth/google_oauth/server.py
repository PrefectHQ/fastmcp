"""Google OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Google OAuth.

Required environment variables:
- FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID: Your Google OAuth client ID
- FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET: Your Google OAuth client secret

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider

auth = GoogleProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET") or "",
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
    # Optional: specify required scopes
    # required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
)

mcp = FastMCP("Google OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
