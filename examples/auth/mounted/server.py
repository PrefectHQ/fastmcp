"""Mounted OAuth servers example for FastMCP.

This example demonstrates mounting multiple OAuth-protected MCP servers in a single
application, each with its own provider. It showcases RFC 8414 path-aware discovery
where each server has its own authorization server metadata endpoint.

URL structure:
- GitHub MCP: http://localhost:8000/api/mcp/github/mcp
- Google MCP: http://localhost:8000/api/mcp/google/mcp
- GitHub discovery: http://localhost:8000/.well-known/oauth-authorization-server/api/mcp/github
- Google discovery: http://localhost:8000/.well-known/oauth-authorization-server/api/mcp/google

Required environment variables:
- FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID: Your GitHub OAuth app client ID
- FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET: Your GitHub OAuth app client secret
- FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID: Your Google OAuth client ID
- FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET: Your Google OAuth client secret

To run:
    python server.py
"""

import os

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.providers.google import GoogleProvider

# Configuration
ROOT_URL = "http://localhost:8000"
API_PREFIX = "/api/mcp"

# --- GitHub OAuth Server ---
github_auth = GitHubProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET") or "",
    base_url=f"{ROOT_URL}{API_PREFIX}/github",
    redirect_path="/auth/callback/github",
)

github_mcp = FastMCP("GitHub Server", auth=github_auth)


@github_mcp.tool
def github_echo(message: str) -> str:
    """Echo from the GitHub-authenticated server."""
    return f"[GitHub] {message}"


@github_mcp.tool
def github_info() -> str:
    """Get info about the GitHub server."""
    return "This is the GitHub OAuth protected MCP server"


# --- Google OAuth Server ---
google_auth = GoogleProvider(
    client_id=os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID") or "",
    client_secret=os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET") or "",
    base_url=f"{ROOT_URL}{API_PREFIX}/google",
    redirect_path="/auth/callback/google",
)

google_mcp = FastMCP("Google Server", auth=google_auth)


@google_mcp.tool
def google_echo(message: str) -> str:
    """Echo from the Google-authenticated server."""
    return f"[Google] {message}"


@google_mcp.tool
def google_info() -> str:
    """Get info about the Google server."""
    return "This is the Google OAuth protected MCP server"


# --- Create ASGI apps ---
github_app = github_mcp.http_app(path="/mcp")
google_app = google_mcp.http_app(path="/mcp")

# Get well-known routes for each provider (path-aware per RFC 8414)
github_well_known = github_auth.get_well_known_routes(mcp_path="/mcp")
google_well_known = google_auth.get_well_known_routes(mcp_path="/mcp")

# --- Combine into single application ---
# Note: Each provider has its own path-aware discovery endpoint:
# - /.well-known/oauth-authorization-server/api/mcp/github
# - /.well-known/oauth-authorization-server/api/mcp/google
app = Starlette(
    routes=[
        # Well-known routes at root level (path-aware)
        *github_well_known,
        *google_well_known,
        # MCP servers under /api/mcp prefix
        Mount(f"{API_PREFIX}/github", app=github_app),
        Mount(f"{API_PREFIX}/google", app=google_app),
    ],
    # Use one of the app lifespans (they're functionally equivalent)
    lifespan=github_app.lifespan,
)

if __name__ == "__main__":
    print("Starting mounted OAuth servers...")
    print(f"  GitHub MCP:     {ROOT_URL}{API_PREFIX}/github/mcp")
    print(f"  Google MCP:     {ROOT_URL}{API_PREFIX}/google/mcp")
    print()
    print("Discovery endpoints (RFC 8414 path-aware):")
    print(
        f"  GitHub:  {ROOT_URL}/.well-known/oauth-authorization-server{API_PREFIX}/github"
    )
    print(
        f"  Google:  {ROOT_URL}/.well-known/oauth-authorization-server{API_PREFIX}/google"
    )
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000)
