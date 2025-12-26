"""Descope authentication provider for FastMCP.

This module provides DescopeProvider - a complete authentication solution that integrates
with Descope's OAuth 2.1 and OpenID Connect services, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class DescopeProvider(RemoteAuthProvider):
    """Descope metadata provider for DCR (Dynamic Client Registration).

    This provider implements Descope integration using metadata forwarding.
    This is the recommended approach for Descope DCR
    as it allows Descope to handle the OAuth flow directly while FastMCP acts
    as a resource server.

    IMPORTANT SETUP REQUIREMENTS:

    1. Create an MCP Server in Descope Console:
       - Go to the [MCP Servers page](https://app.descope.com/mcp-servers) of the Descope Console
       - Create a new MCP Server
       - Ensure that **Dynamic Client Registration (DCR)** is enabled
       - Note your Well-Known URL

    2. Note your Well-Known URL:
       - Save your Well-Known URL from [MCP Server Settings](https://app.descope.com/mcp-servers)
       - Format: ``https://.../v1/apps/agentic/P.../M.../.well-known/openid-configuration``

    For detailed setup instructions, see:
    https://docs.descope.com/identity-federation/inbound-apps/creating-inbound-apps#method-2-dynamic-client-registration-dcr

    Example:
        ```python
        from fastmcp.server.auth.providers.descope import DescopeProvider

        # Create Descope metadata provider (JWT verifier created automatically)
        descope_auth = DescopeProvider(
            config_url="https://.../v1/apps/agentic/P.../M.../.well-known/openid-configuration",
            base_url="https://your-fastmcp-server.com",
        )

        # Use with FastMCP
        mcp = FastMCP("My App", auth=descope_auth)
        ```
    """

    def __init__(
        self,
        *,
        base_url: AnyHttpUrl | str,
        config_url: AnyHttpUrl | str | None = None,
        project_id: str | None = None,
        descope_base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Descope metadata provider.

        Args:
            base_url: Public URL of this FastMCP server
            config_url: Your Descope Well-Known URL (e.g., "https://.../v1/apps/agentic/P.../M.../.well-known/openid-configuration")
                This is the new recommended way. If provided, project_id and descope_base_url are ignored.
            project_id: Your Descope Project ID (e.g., "P2abc123"). Used with descope_base_url for backwards compatibility.
            descope_base_url: Your Descope base URL (e.g., "https://api.descope.com"). Used with project_id for backwards compatibility.
            required_scopes: Optional list of scopes that must be present in validated tokens.
                These scopes will be included in the protected resource metadata.
            token_verifier: Optional token verifier. If None, creates JWT verifier for Descope
        """
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))

        # Parse scopes if provided as string
        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )

        # Determine which API is being used
        if config_url is not None:
            # New API: use config_url
            # Strip /.well-known/openid-configuration from config_url if present
            issuer_url = str(config_url)
            if issuer_url.endswith("/.well-known/openid-configuration"):
                issuer_url = issuer_url[: -len("/.well-known/openid-configuration")]

            # Parse the issuer URL to extract descope_base_url and project_id for other uses
            parsed_url = urlparse(issuer_url)
            path_parts = parsed_url.path.strip("/").split("/")

            # Extract project_id from path (format: /v1/apps/agentic/P.../M...)
            if "agentic" in path_parts:
                agentic_index = path_parts.index("agentic")
                if agentic_index + 1 < len(path_parts):
                    self.project_id = path_parts[agentic_index + 1]
                else:
                    raise ValueError(
                        f"Could not extract project_id from config_url: {issuer_url}"
                    )
            else:
                raise ValueError(
                    f"Could not find 'agentic' in config_url path: {issuer_url}"
                )

            # Extract descope_base_url (scheme + netloc)
            self.descope_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}".rstrip(
                "/"
            )
        elif project_id is not None and descope_base_url is not None:
            # Old API: use project_id and descope_base_url
            self.project_id = project_id
            descope_base_url_str = str(descope_base_url).rstrip("/")
            # Ensure descope_base_url has a scheme
            if not descope_base_url_str.startswith(("http://", "https://")):
                descope_base_url_str = f"https://{descope_base_url_str}"
            self.descope_base_url = descope_base_url_str
            # Old issuer format
            issuer_url = f"{self.descope_base_url}/v1/apps/{self.project_id}"
        else:
            raise ValueError(
                "Either config_url (new API) or both project_id and descope_base_url (old API) must be provided"
            )

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.descope_base_url}/{self.project_id}/.well-known/jwks.json",
                issuer=issuer_url,
                algorithm="RS256",
                audience=self.project_id,
                required_scopes=parsed_scopes,
            )

        # Initialize RemoteAuthProvider with Descope as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(issuer_url)],
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
