"""Azure (Microsoft Entra) OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Azure/Microsoft OAuth.

Required environment variables:
- AZURE_CLIENT_ID: Your Azure application (client) ID
- AZURE_CLIENT_SECRET: Your Azure client secret
- AZURE_TENANT_ID: Tenant ID
  Options: "organizations" (work/school), "consumers" (personal), or specific tenant ID

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider

auth = AzureProvider(
    client_id=os.getenv("AZURE_CLIENT_ID") or "",
    client_secret=os.getenv("AZURE_CLIENT_SECRET") or "",
    tenant_id=os.getenv("AZURE_TENANT_ID")
    or "",  # Required for single-tenant apps - get from Azure Portal
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
)

mcp = FastMCP("Azure OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
