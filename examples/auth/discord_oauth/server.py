"""Discord OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Discord OAuth.

Required environment variables:
- FASTMCP_SERVER_AUTH_DISCORD_CLIENT_ID: Your Discord OAuth app client ID
- FASTMCP_SERVER_AUTH_DISCORD_CLIENT_SECRET: Your Discord OAuth app client secret

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.discord import DiscordProvider

auth = DiscordProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_DISCORD_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_DISCORD_CLIENT_SECRET") or "",
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
)

mcp = FastMCP("Discord OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
