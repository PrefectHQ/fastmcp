"""AuthKit server example for FastMCP.

Demonstrates an MCP server secured by WorkOS AuthKit. FastMCP binds the JWT
audience to this server's resource URL automatically; you configure the same
URL as an MCP resource indicator in the WorkOS Dashboard.

Required environment variables:
- AUTHKIT_DOMAIN: Your AuthKit domain (e.g., "https://your-app.authkit.app")

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

auth = AuthKitProvider(
    authkit_domain=os.getenv("AUTHKIT_DOMAIN") or "",
    base_url="http://127.0.0.1:8000",
)

mcp = FastMCP("AuthKit Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
