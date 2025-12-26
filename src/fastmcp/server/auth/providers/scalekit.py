"""Scalekit authentication provider for FastMCP.

This module provides ScalekitProvider - a complete authentication solution that integrates
with Scalekit's OAuth 2.1 and OpenID Connect services, supporting Resource Server
authentication for seamless MCP client authentication.
"""

from __future__ import annotations

import httpx
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


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
       - Set SCALEKIT_RESOURCE_ID from your created resource
       - Set BASE_URL to your FastMCP server's public URL

    For detailed setup instructions, see:
    https://docs.scalekit.com/mcp/overview/

    Example:
        ```python
        from fastmcp.server.auth.providers.scalekit import ScalekitProvider

        # Create Scalekit resource server provider
        scalekit_auth = ScalekitProvider(
            environment_url="https://your-env.scalekit.com",
            resource_id="sk_resource_...",
            base_url="https://your-fastmcp-server.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=scalekit_auth)
        ```
    """

    def __init__(
        self,
        *,
        environment_url: AnyHttpUrl | str,
        resource_id: str,
        base_url: AnyHttpUrl | str | None = None,
        mcp_url: AnyHttpUrl | str | None = None,
        client_id: str | None = None,
        required_scopes: list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Scalekit resource server provider.

        Args:
            environment_url: Your Scalekit environment URL (e.g., "https://your-env.scalekit.com")
            resource_id: Your Scalekit resource ID
            base_url: Public URL of this FastMCP server (or use mcp_url for backwards compatibility)
            mcp_url: Deprecated alias for base_url. Will be removed in a future release.
            client_id: Deprecated parameter, no longer required. Will be removed in a future release.
            required_scopes: Optional list of scopes that must be present in tokens
            token_verifier: Optional token verifier. If None, creates JWT verifier for Scalekit
        """
        # Resolve base_url from mcp_url if needed (backwards compatibility)
        resolved_base_url = base_url or mcp_url
        if not resolved_base_url:
            raise ValueError("Either base_url or mcp_url must be provided")

        if mcp_url is not None:
            logger.warning(
                "ScalekitProvider parameter 'mcp_url' is deprecated and will be removed in a future release. "
                "Rename it to 'base_url'."
            )

        if client_id is not None:
            logger.warning(
                "ScalekitProvider no longer requires 'client_id'. The parameter is accepted only for backward "
                "compatibility and will be removed in a future release."
            )

        self.environment_url = str(environment_url).rstrip("/")
        self.resource_id = resource_id
        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else []
        )
        self.required_scopes = parsed_scopes
        base_url_value = str(resolved_base_url)

        logger.debug(
            "Initializing ScalekitProvider: environment_url=%s resource_id=%s base_url=%s required_scopes=%s",
            self.environment_url,
            self.resource_id,
            base_url_value,
            self.required_scopes,
        )

        # Create default JWT verifier if none provided
        if token_verifier is None:
            logger.debug(
                "Creating default JWTVerifier for Scalekit: jwks_uri=%s issuer=%s required_scopes=%s",
                f"{self.environment_url}/keys",
                self.environment_url,
                self.required_scopes,
            )
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.environment_url}/keys",
                issuer=self.environment_url,
                algorithm="RS256",
                required_scopes=self.required_scopes or None,
            )
        else:
            logger.debug("Using custom token verifier for ScalekitProvider")

        # Initialize RemoteAuthProvider with Scalekit as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[
                AnyHttpUrl(f"{self.environment_url}/resources/{self.resource_id}")
            ],
            base_url=base_url_value,
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
        logger.debug(
            "Preparing Scalekit metadata routes: mcp_path=%s resource_id=%s",
            mcp_path,
            self.resource_id,
        )

        async def oauth_authorization_server_metadata(request):
            """Forward Scalekit OAuth authorization server metadata with FastMCP customizations."""
            try:
                metadata_url = f"{self.environment_url}/.well-known/oauth-authorization-server/resources/{self.resource_id}"
                logger.debug(
                    "Fetching Scalekit OAuth metadata: metadata_url=%s", metadata_url
                )
                async with httpx.AsyncClient() as client:
                    response = await client.get(metadata_url)
                    response.raise_for_status()
                    metadata = response.json()
                    logger.debug(
                        "Scalekit metadata fetched successfully: metadata_keys=%s",
                        list(metadata.keys()),
                    )
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
