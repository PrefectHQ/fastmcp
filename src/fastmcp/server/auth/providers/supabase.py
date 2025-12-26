"""Supabase authentication provider for FastMCP.

This module provides SupabaseProvider - a complete authentication solution that integrates
with Supabase Auth's JWT verification, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
"""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class SupabaseProvider(RemoteAuthProvider):
    """Supabase metadata provider for DCR (Dynamic Client Registration).

    This provider implements Supabase Auth integration using metadata forwarding.
    This approach allows Supabase to handle the OAuth flow directly while FastMCP acts
    as a resource server, verifying JWTs issued by Supabase Auth.

    IMPORTANT SETUP REQUIREMENTS:

    1. Supabase Project Setup:
       - Create a Supabase project at https://supabase.com
       - Note your project URL (e.g., "https://abc123.supabase.co")
       - Configure your JWT algorithm in Supabase Auth settings (HS256, RS256, or ES256)
       - Asymmetric keys (RS256/ES256) are recommended for production

    2. JWT Verification:
       - FastMCP verifies JWTs using the JWKS endpoint at {project_url}{auth_route}/.well-known/jwks.json
       - JWTs are issued by {project_url}{auth_route}
       - Default auth_route is "/auth/v1" (can be customized for self-hosted setups)
       - Tokens are cached for up to 10 minutes by Supabase's edge servers
       - Algorithm must match your Supabase Auth configuration

    3. Authorization:
       - Supabase uses Row Level Security (RLS) policies for database authorization
       - OAuth-level scopes are an upcoming feature in Supabase Auth
       - Both approaches will be supported once scope handling is available

    For detailed setup instructions, see:
    https://supabase.com/docs/guides/auth/jwts

    Example:
        ```python
        from fastmcp.server.auth.providers.supabase import SupabaseProvider

        # Create Supabase metadata provider (JWT verifier created automatically)
        supabase_auth = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://your-fastmcp-server.com",
            algorithm="ES256",  # Match your Supabase Auth configuration
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=supabase_auth)
        ```
    """

    def __init__(
        self,
        *,
        project_url: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        auth_route: str = "/auth/v1",
        algorithm: Literal["HS256", "RS256", "ES256"] = "ES256",
        required_scopes: list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Supabase metadata provider.

        Args:
            project_url: Your Supabase project URL (e.g., "https://abc123.supabase.co")
            base_url: Public URL of this FastMCP server
            auth_route: Supabase Auth route. Defaults to "/auth/v1". Can be customized
                for self-hosted Supabase Auth setups using custom routes.
            algorithm: JWT signing algorithm (HS256, RS256, or ES256). Must match your
                Supabase Auth configuration. Defaults to ES256.
            required_scopes: Optional list of scopes to require for all requests.
                Note: Supabase currently uses RLS policies for authorization. OAuth-level
                scopes are an upcoming feature.
            token_verifier: Optional token verifier. If None, creates JWT verifier for Supabase
        """
        self.project_url = str(project_url).rstrip("/")
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))
        self.auth_route = auth_route.strip("/")

        # Parse scopes if provided as string
        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.project_url}/{self.auth_route}/.well-known/jwks.json",
                issuer=f"{self.project_url}/{self.auth_route}",
                algorithm=algorithm,
                required_scopes=parsed_scopes,
            )

        # Initialize RemoteAuthProvider with Supabase as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(f"{self.project_url}/{self.auth_route}")],
            base_url=self.base_url,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes including Supabase authorization server metadata forwarding.

        This returns the standard protected resource routes plus an authorization server
        metadata endpoint that forwards Supabase's OAuth metadata to clients.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward Supabase OAuth authorization server metadata with FastMCP customizations."""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.project_url}/{self.auth_route}/.well-known/oauth-authorization-server"
                    )
                    response.raise_for_status()
                    metadata = response.json()
                    return JSONResponse(metadata)
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Supabase metadata: {e}",
                    },
                    status_code=500,
                )

        # Add Supabase authorization server metadata forwarding
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes
