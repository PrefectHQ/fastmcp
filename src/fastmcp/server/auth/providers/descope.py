"""Descope authentication provider for FastMCP.

This module provides DescopeProvider - a complete authentication solution that integrates
with Descope's OAuth 2.1 and OpenID Connect services, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
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


class DescopeProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_DESCOPEPROVIDER_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    project_id: str
    base_url: AnyHttpUrl
    descope_base_url: AnyHttpUrl = AnyHttpUrl("https://api.descope.com")


class DescopeProvider(RemoteAuthProvider):
    """Descope metadata provider for DCR (Dynamic Client Registration).

    This provider implements Descope integration using metadata forwarding.
    This is the recommended approach for Descope DCR
    as it allows Descope to handle the OAuth flow directly while FastMCP acts
    as a resource server.

    IMPORTANT SETUP REQUIREMENTS:

    1. Enable Dynamic Client Registration in Descope Console:
       - Go to the [Inbound Apps page](https://app.descope.com/apps/inbound) of the Descope Console
       - Click **DCR Settings**
       - Enable **Dynamic Client Registration (DCR)**
       - Define allowed scopes

    2. Note your Project ID:
       - Save your Project ID from [Project Settings](https://app.descope.com/settings/project)
       - Example: P2abc...123

    For detailed setup instructions, see:
    https://docs.descope.com/identity-federation/inbound-apps/creating-inbound-apps#method-2-dynamic-client-registration-dcr

    Example:
        ```python
        from fastmcp.server.auth.providers.descope import DescopeProvider

        # Create Descope metadata provider (JWT verifier created automatically)
        descope_auth = DescopeProvider(
            project_id="P2abc...123",
            base_url="https://your-fastmcp-server.com",
            descope_base_url="https://api.descope.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=descope_auth)
        ```
    """

    def __init__(
        self,
        *,
        project_id: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        descope_base_url: AnyHttpUrl | str | NotSetT = NotSet,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Descope metadata provider.

        Args:
            project_id: Your Descope Project ID (e.g., "P2abc...123")
            base_url: Public URL of this FastMCP server
            descope_base_url: Descope API base URL (defaults to https://api.descope.com)
            token_verifier: Optional token verifier. If None, creates JWT verifier for Descope
        """
        settings = DescopeProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "project_id": project_id,
                    "base_url": base_url,
                    "descope_base_url": descope_base_url,
                }.items()
                if v is not NotSet
            }
        )

        self.project_id = settings.project_id
        self.base_url = str(settings.base_url).rstrip("/")
        self.descope_base_url = str(settings.descope_base_url).rstrip("/")

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.descope_base_url}/{self.project_id}/.well-known/jwks.json",
                issuer=f"{self.descope_base_url}/v1/apps/{self.project_id}",
                algorithm="RS256",
                audience=self.project_id,
            )

        # Initialize RemoteAuthProvider with Descope as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[
                AnyHttpUrl(f"{self.descope_base_url}/v1/apps/{self.project_id}")
            ],
            base_url=self.base_url,
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes including Descope authorization server metadata forwarding.

        This returns the standard protected resource routes plus an authorization server
        metadata endpoint that forwards Descope's OAuth metadata to clients.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        # Get the standard protected resource routes from RemoteAuthProvider
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            """Forward Descope OAuth authorization server metadata with FastMCP customizations."""
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.descope_base_url}/v1/apps/{self.project_id}/.well-known/oauth-authorization-server"
                    )
                    response.raise_for_status()
                    metadata = response.json()
                    return JSONResponse(metadata)
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Descope metadata: {e}",
                    },
                    status_code=500,
                )

        # Add Descope authorization server metadata forwarding
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes
