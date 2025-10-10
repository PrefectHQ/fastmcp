"""Scalekit authentication provider for FastMCP.

This module provides ScalekitProvider - a complete authentication solution that integrates
with Scalekit's OAuth 2.1 and OpenID Connect services, supporting Resource Server
authentication for seamless MCP client authentication.
"""

from __future__ import annotations

import httpx
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class ScalekitProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_SCALEKITPROVIDER_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    environment_url: AnyHttpUrl
    client_id: str
    resource_id: str
    mcp_url: AnyHttpUrl


class ScalekitProvider(RemoteAuthProvider):
    """Scalekit resource server provider for OAuth 2.1 authentication.

    This provider implements Scalekit integration using resource server pattern.
    FastMCP acts as a protected resource server that validates access tokens issued
    by Scalekit's authorization server.

    IMPORTANT SETUP REQUIREMENTS:

    1. Create an MCP Server in Scalekit Dashboard:
       - Go to your [Scalekit Dashboard](https://app.scalekit.com/)
       - Navigate to MCP Servers section
       - Register a new MCP Server with appropriate scopes
       - Ensure the Resource Identifier matches exactly what you configure as MCP URL
       - Note the Resource ID

    2. Environment Configuration:
       - Set SCALEKIT_ENVIRONMENT_URL (e.g., https://your-env.scalekit.com)
       - Set SCALEKIT_CLIENT_ID from your OAuth application
       - Set SCALEKIT_RESOURCE_ID from your created resource
       - Set MCP_URL to your FastMCP server's public URL

    For detailed setup instructions, see:
    https://docs.scalekit.com/mcp/overview/

    Example:
        ```python
        from fastmcp.server.auth.providers.scalekit import ScalekitProvider

        # Create Scalekit resource server provider
        scalekit_auth = ScalekitProvider(
            environment_url="https://your-env.scalekit.com",
            client_id="sk_client_...",
            resource_id="sk_resource_...",
            mcp_url="https://your-fastmcp-server.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=scalekit_auth)
        ```
    """

    def __init__(
        self,
        *,
        environment_url: AnyHttpUrl | str | NotSetT = NotSet,
        client_id: str | NotSetT = NotSet,
        resource_id: str | NotSetT = NotSet,
        mcp_url: AnyHttpUrl | str | NotSetT = NotSet,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Scalekit resource server provider.

        Args:
            environment_url: Your Scalekit environment URL (e.g., "https://your-env.scalekit.com")
            client_id: Your Scalekit OAuth client ID
            resource_id: Your Scalekit resource ID
            mcp_url: Public URL of this FastMCP server (used as audience)
            token_verifier: Optional token verifier. If None, creates JWT verifier for Scalekit
        """
        settings = ScalekitProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "environment_url": environment_url,
                    "client_id": client_id,
                    "resource_id": resource_id,
                    "mcp_url": mcp_url,
                }.items()
                if v is not NotSet
            }
        )

        self.environment_url = str(settings.environment_url).rstrip("/")
        self.client_id = settings.client_id
        self.resource_id = settings.resource_id
        self.mcp_url = str(settings.mcp_url)

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.environment_url}/keys",
                issuer=self.environment_url,
                algorithm="RS256",
                audience=self.mcp_url,
            )

        # Initialize RemoteAuthProvider with Scalekit as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[
                AnyHttpUrl(f"{self.environment_url}/resources/{self.resource_id}")
            ],
            base_url=self.mcp_url,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes including Scalekit authorization server metadata forwarding.

        This returns the standard protected resource routes plus an authorization server
        metadata endpoint that forwards Scalekit's OAuth metadata to clients.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward Scalekit OAuth authorization server metadata with FastMCP customizations."""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.environment_url}/.well-known/oauth-authorization-server/resources/{self.resource_id}"
                    )
                    response.raise_for_status()
                    metadata = response.json()
                    return JSONResponse(metadata)
            except Exception as e:
                logger.error(f"Failed to fetch Scalekit metadata: {e}")
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Scalekit metadata: {e}",
                    },
                    status_code=500,
                )

        # Add Scalekit authorization server metadata forwarding
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes
