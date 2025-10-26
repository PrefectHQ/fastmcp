"""Supabase authentication provider for FastMCP.

This module provides SupabaseProvider - a complete authentication solution that integrates
with Supabase Auth's JWT verification, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
"""

from __future__ import annotations

import httpx
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class SupabaseProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_SUPABASE_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    project_url: AnyHttpUrl
    base_url: AnyHttpUrl
    required_scopes: list[str] | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class SupabaseProvider(RemoteAuthProvider):
    """Supabase metadata provider for DCR (Dynamic Client Registration).

    This provider implements Supabase Auth integration using metadata forwarding.
    This approach allows Supabase to handle the OAuth flow directly while FastMCP acts
    as a resource server, verifying JWTs issued by Supabase Auth.

    IMPORTANT SETUP REQUIREMENTS:

    1. Supabase Project Setup:
       - Create a Supabase project at https://supabase.com
       - Note your project URL (e.g., "https://abc123.supabase.co")
       - For projects created after May 1st, 2025, asymmetric RS256 keys are used by default
       - For older projects, consider migrating to asymmetric keys for better security

    2. JWT Verification:
       - FastMCP verifies JWTs using the JWKS endpoint at {project_url}/auth/v1/.well-known/jwks.json
       - JWTs are issued by {project_url}/auth/v1
       - Tokens are cached for up to 10 minutes by Supabase's edge servers

    For detailed setup instructions, see:
    https://supabase.com/docs/guides/auth/jwts

    Example:
        ```python
        from fastmcp.server.auth.providers.supabase import SupabaseProvider

        # Create Supabase metadata provider (JWT verifier created automatically)
        supabase_auth = SupabaseProvider(
            project_url="https://abc123.supabase.co",
            base_url="https://your-fastmcp-server.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=supabase_auth)
        ```
    """

    def __init__(
        self,
        *,
        project_url: AnyHttpUrl | str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT | None = NotSet,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Supabase metadata provider.

        Args:
            project_url: Your Supabase project URL (e.g., "https://abc123.supabase.co")
            base_url: Public URL of this FastMCP server
            required_scopes: Optional list of scopes to require for all requests
            token_verifier: Optional token verifier. If None, creates JWT verifier for Supabase
        """
        settings = SupabaseProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "project_url": project_url,
                    "base_url": base_url,
                    "required_scopes": required_scopes,
                }.items()
                if v is not NotSet
            }
        )

        self.project_url = str(settings.project_url).rstrip("/")
        self.base_url = str(settings.base_url).rstrip("/")

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.project_url}/auth/v1/.well-known/jwks.json",
                issuer=f"{self.project_url}/auth/v1",
                algorithm="ES256",  # Supabase uses ES256 for asymmetric keys
                required_scopes=settings.required_scopes,
            )

        # Initialize RemoteAuthProvider with Supabase as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(f"{self.project_url}/auth/v1")],
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
                        f"{self.project_url}/auth/v1/.well-known/oauth-authorization-server"
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
