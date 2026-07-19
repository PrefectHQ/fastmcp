"""Auth0 Auth for MCP server example.

Required environment variables:
- AUTH0_CONFIG_URL: OIDC discovery URL for your Auth0 tenant

To run:
    export AUTH0_CONFIG_URL="https://YOUR_TENANT.auth0.com/.well-known/openid-configuration"
    python server.py
"""

import os
import sys

from fastmcp import FastMCP
from fastmcp.server.auth.providers.auth0 import Auth0MCPProvider

config_url = os.getenv("AUTH0_CONFIG_URL")
if not config_url:
    sys.exit(
        "AUTH0_CONFIG_URL must be set to your Auth0 OIDC discovery URL, "
        'e.g. "https://YOUR_TENANT.auth0.com/.well-known/openid-configuration"'
    )

auth = Auth0MCPProvider(
    config_url=config_url,
    base_url="http://127.0.0.1:8000",
)

mcp = FastMCP("Auth0 MCP Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
