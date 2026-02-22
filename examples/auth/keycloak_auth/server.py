"""Keycloak OAuth server example for FastMCP.

This example demonstrates how to protect a FastMCP server with Keycloak.

Configuration:
    The example uses environment variables for convenience, but you can also
    pass values directly to KeycloakAuthProvider. The following environment
    variables can be set in a .env file:

    - FASTMCP_SERVER_AUTH_KEYCLOAK_REALM_URL: Your Keycloak realm URL
      (default: http://localhost:8080/realms/fastmcp)
    - FASTMCP_SERVER_AUTH_KEYCLOAK_BASE_URL: Your FastMCP server base URL
      (default: http://localhost:8000)
    - FASTMCP_SERVER_AUTH_KEYCLOAK_REQUIRED_SCOPES: Required OAuth scopes
      (default: openid,profile)
    - FASTMCP_SERVER_AUTH_KEYCLOAK_AUDIENCE: Audience for JWT validation
      (default: None for development)

To run:
    python server.py

    Or with custom configuration:
    FASTMCP_SERVER_AUTH_KEYCLOAK_REALM_URL=https://your-keycloak.com/realms/myrealm python server.py
"""

import os

from dotenv import load_dotenv
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from fastmcp import FastMCP
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import configure_logging

# Load environment overrides before configuring logging
load_dotenv(".env", override=True)

# Configure FastMCP logging to INFO
configure_logging(level="INFO")

# Configuration - can be overridden with environment variables
realm_url = os.getenv(
    "FASTMCP_SERVER_AUTH_KEYCLOAK_REALM_URL", "http://localhost:8080/realms/fastmcp"
)
base_url = os.getenv("FASTMCP_SERVER_AUTH_KEYCLOAK_BASE_URL", "http://localhost:8000")
required_scopes = os.getenv(
    "FASTMCP_SERVER_AUTH_KEYCLOAK_REQUIRED_SCOPES", "openid,profile"
)
# Note: Audience validation is disabled by default for this development example.
# For production, configure Keycloak audience mappers and set FASTMCP_SERVER_AUTH_KEYCLOAK_AUDIENCE
audience = os.getenv("FASTMCP_SERVER_AUTH_KEYCLOAK_AUDIENCE")

# Create Keycloak auth provider
auth = KeycloakAuthProvider(
    realm_url=realm_url,
    base_url=base_url,
    required_scopes=required_scopes,
    audience=audience,  # None by default for development
)

mcp = FastMCP("Keycloak OAuth Example Server", auth=auth)


@mcp.tool
def echo(message: str) -> str:
    """Echo the provided message."""
    return message


@mcp.tool
async def get_access_token_claims() -> dict:
    """Get the authenticated user's access token claims."""
    token = get_access_token()
    if token is None or token.claims is None:
        raise RuntimeError("No valid access token found. Authentication required.")

    return {
        "sub": token.claims.get("sub"),
        "name": token.claims.get("name"),
        "given_name": token.claims.get("given_name"),
        "family_name": token.claims.get("family_name"),
        "preferred_username": token.claims.get("preferred_username"),
        "scope": token.claims.get("scope"),
    }


if __name__ == "__main__":
    try:
        # Enable CORS for MCP Inspector and other browser-based clients
        # See: https://gofastmcp.com/deployment/http#cors-for-browser-based-clients
        cors_middleware = Middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Allow all origins for development
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "mcp-protocol-version",
                "mcp-session-id",
                "Authorization",
                "Content-Type",
            ],
            expose_headers=["mcp-session-id"],  # Required for MCP Inspector
        )

        mcp.run(transport="http", port=8000, middleware=[cors_middleware])
    except KeyboardInterrupt:
        # Graceful shutdown, suppress noisy logs resulting from asyncio.run task cancellation propagation
        pass
    except Exception as e:
        # Unexpected internal error
        print(f"❌ Internal error: {e}")
