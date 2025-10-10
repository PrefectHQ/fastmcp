"""Scalekit OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Scalekit OAuth.

Required environment variables:
- SCALEKIT_ENVIRONMENT_URL: Your Scalekit environment URL (e.g., "https://your-env.scalekit.com")
- SCALEKIT_CLIENT_ID: Your Scalekit OAuth application client ID
- SCALEKIT_RESOURCE_ID: Your Scalekit resource ID

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.scalekit import ScalekitProvider

auth = ScalekitProvider(
    environment_url=os.getenv("SCALEKIT_ENVIRONMENT_URL")
    or "https://your-env.scalekit.com",
    client_id=os.getenv("SCALEKIT_CLIENT_ID") or "",
    resource_id=os.getenv("SCALEKIT_RESOURCE_ID") or "",
    mcp_url=os.getenv("MCP_URL", "http://localhost:8000/mcp"),
)

mcp = FastMCP("Scalekit OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
def auth_status() -> dict:
    """Show Scalekit authentication status."""
    # In a real implementation, you would extract user info from the JWT token
    return {
        "message": "This tool requires authentication via Scalekit",
        "authenticated": True,
        "provider": "Scalekit",
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
