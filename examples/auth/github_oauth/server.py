"""GitHub OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with GitHub OAuth.

Required environment variables:
- FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID: Your GitHub OAuth app client ID
- FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET: Your GitHub OAuth app client secret

To run:
    python server.py
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider

auth = GitHubProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET") or "",
    base_url="http://localhost:8000",
    # redirect_path="/auth/callback",  # Default path - change if using a different callback URL
)

mcp = FastMCP(
    auth=auth,
    # "GitHub OAuth Example Server",
    # icons=[
    #     Icon(
    #         src="https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Octicons-mark-github.svg/2048px-Octicons-mark-github.svg.png"
    #     )
    # ],
    # website_url="https://example.com",
)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
