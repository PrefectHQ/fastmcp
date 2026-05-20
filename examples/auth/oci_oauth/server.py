"""Oracle OCI IAM OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Oracle OCI IAM OAuth.

Required environment variables:
- FASTMCP_SERVER_AUTH_IDCS_CLIENT_ID: Your IDCS OAuth Application clientID
- FASTMCP_SERVER_AUTH_IDCS_CLIENT_SECRET: Your IDCS client secret
- FASTMCP_SERVER_AUTH_IDCS_DOMAIN: IDCS domain URL  for example idcs-abscasdwdac3432rdwsda.identity.oraclecloud.com

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.oci import OCIProvider

auth = OCIProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_IDCS_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_IDCS_CLIENT_SECRET") or "",
    config_url=f"https://{os.getenv('FASTMCP_SERVER_AUTH_IDCS_DOMAIN')}/.well-known/openid-configuration"
    or "",
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
)

mcp = FastMCP("OCI OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000, host="localhost")
