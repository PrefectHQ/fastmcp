"""Scalekit OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Scalekit OAuth.

Required environment variables:
- SCALEKIT_ENVIRONMENT_URL: Your Scalekit environment URL (e.g., "https://your-env.scalekit.com")
- SCALEKIT_RESOURCE_ID: Your Scalekit resource ID

Optional:
- SCALEKIT_REQUIRED_SCOPES: Comma-separated scopes tokens must include
- BASE_URL: Public URL where the FastMCP server is exposed (defaults to `http://localhost:8000/`)

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.scalekit import ScalekitProvider

required_scopes_env = os.getenv("SCALEKIT_REQUIRED_SCOPES")
required_scopes = (
    [scope.strip() for scope in required_scopes_env.split(",") if scope.strip()]
    if required_scopes_env
    else None
)

auth = ScalekitProvider(
    environment_url=os.getenv("SCALEKIT_ENVIRONMENT_URL")
    or "https://your-env.scalekit.com",
    resource_id=os.getenv("SCALEKIT_RESOURCE_ID") or "",
    base_url=os.getenv("BASE_URL", "http://localhost:8000/"),
    required_scopes=required_scopes,
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
