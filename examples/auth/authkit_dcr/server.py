"""AuthKit DCR server example for FastMCP.

This example demonstrates how to protect a FastMCP server with AuthKit DCR.

Required environment variables:
- FASTMCP_SERVER_AUTH_AUTHKITPROVIDER_AUTHKIT_DOMAIN: Your AuthKit domain (e.g., "https://your-app.authkit.app")

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

auth = AuthKitProvider(
    authkit_domain=os.getenv("FASTMCP_SERVER_AUTH_AUTHKITPROVIDER_AUTHKIT_DOMAIN")
    or "",
    base_url="http://localhost:8000",
)

mcp = FastMCP("AuthKit DCR Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
