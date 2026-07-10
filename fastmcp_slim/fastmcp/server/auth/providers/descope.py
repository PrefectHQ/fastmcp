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

_OPENID_CONFIGURATION_SUFFIX = "/.well-known/openid-configuration"
_OAUTH_AUTHORIZATION_SERVER_SUFFIX = "/.well-known/oauth-authorization-server"


def _normalize_openid_configuration_url(
    config_url: str,
) -> tuple[str, str]:
    """Return (issuer_url, openid_configuration_url) from a Descope config URL."""
    config_url = config_url.rstrip("/")
    if config_url.endswith(_OPENID_CONFIGURATION_SUFFIX):
        issuer_url = config_url[: -len(_OPENID_CONFIGURATION_SUFFIX)]
        return issuer_url, config_url

    openid_configuration_url = f"{config_url}{_OPENID_CONFIGURATION_SUFFIX}"
    return config_url, openid_configuration_url


def _parse_descope_config_url(
    config_url: str,
) -> tuple[str, str, str, str]:
    """Parse a Descope well-known URL into connection details.

    Supports both resource-specific MCP Server URLs:
        /v1/apps/agentic/{project_id}/{mcp_server_id}/.well-known/openid-configuration
    and project-level inbound app URLs:
        /v1/apps/{project_id}/.well-known/openid-configuration

    Returns:
        Tuple of (descope_base_url, project_id, issuer_url, openid_configuration_url)
    """
    issuer_url, openid_configuration_url = _normalize_openid_configuration_url(config_url)
    parsed_url = urlparse(issuer_url)
    path_parts = parsed_url.path.strip("/").split("/")
    descope_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}".rstrip("/")

    if "agentic" in path_parts:
        agentic_index = path_parts.index("agentic")
        if agentic_index + 1 >= len(path_parts):
            raise ValueError(
                f"Could not extract project_id from config_url: {issuer_url}"
            )
        project_id = path_parts[agentic_index + 1]
    elif "apps" in path_parts:
        apps_index = path_parts.index("apps")
        if apps_index + 1 >= len(path_parts):
            raise ValueError(
                f"Could not extract project_id from config_url: {issuer_url}"
            )
        project_id = path_parts[apps_index + 1]
        if project_id == "agentic":
            raise ValueError(
                f"Could not extract project_id from config_url: {issuer_url}"
            )
    else:
        raise ValueError(
            "Could not parse config_url: expected a Descope apps well-known URL "
            f"(/v1/apps/{{project_id}}/... or /v1/apps/agentic/{{project_id}}/...): "
            f"{issuer_url}"
        )

    return descope_base_url, project_id, issuer_url, openid_configuration_url


def _oauth_authorization_server_url(openid_configuration_url: str) -> str:
    if openid_configuration_url.endswith(_OPENID_CONFIGURATION_SUFFIX):
        return openid_configuration_url[: -len(_OPENID_CONFIGURATION_SUFFIX)] + (
            _OAUTH_AUTHORIZATION_SERVER_SUFFIX
        )
    return f"{openid_configuration_url.rstrip('/')}{_OAUTH_AUTHORIZATION_SERVER_SUFFIX}"


def _fetch_openid_configuration(openid_configuration_url: str) -> dict | None:
    try:
        response = httpx.get(openid_configuration_url, timeout=10.0)
        response.raise_for_status()
        metadata = response.json()
        if isinstance(metadata, dict):
            return metadata
    except Exception:
        logger.warning(
            "Failed to fetch Descope OpenID configuration from %s",
            openid_configuration_url,
            exc_info=True,
        )
    return None


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
       - Recommended format: ``https://.../v1/apps/agentic/P.../M.../.well-known/openid-configuration``
       - Project-level format is also supported:
         ``https://.../v1/apps/P.../.well-known/openid-configuration``

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
        scopes_supported: list[str] | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize Descope metadata provider.

        Args:
            base_url: Public URL of this FastMCP server
            config_url: Your Descope Well-Known URL. Prefer the MCP Server-specific URL
                (``/v1/apps/agentic/P.../M.../.well-known/openid-configuration``). Project-level
                URLs (``/v1/apps/P.../.well-known/openid-configuration``) are also supported.
                If provided, project_id and descope_base_url are ignored.
            project_id: Your Descope Project ID (e.g., "P2abc123"). Used with descope_base_url for backwards compatibility.
            descope_base_url: Your Descope base URL (e.g., "https://api.descope.com"). Used with project_id for backwards compatibility.
            required_scopes: Scopes that must be present in validated tokens. When omitted,
                tokens are not rejected for missing global scopes.
            scopes_supported: Scopes advertised to MCP clients via protected resource metadata.
                When omitted, scopes are discovered from ``scopes_supported`` in the configured
                well-known OpenID document. Defaults to ``required_scopes`` when neither is
                provided.
            resource_name: Optional name for the protected resource metadata.
            resource_documentation: Optional documentation URL for the protected resource.
            token_verifier: Optional token verifier. If None, creates JWT verifier for Descope
        """
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))
        self.openid_configuration_url: str | None = None
        self.oauth_authorization_server_metadata_url: str | None = None

        parsed_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )
        parsed_scopes_supported = (
            parse_scopes(scopes_supported) if scopes_supported is not None else None
        )

        # Determine which API is being used
        if config_url is not None:
            (
                self.descope_base_url,
                self.project_id,
                issuer_url,
                self.openid_configuration_url,
            ) = _parse_descope_config_url(str(config_url))
            self.oauth_authorization_server_metadata_url = (
                _oauth_authorization_server_url(self.openid_configuration_url)
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
            self.openid_configuration_url = (
                f"{issuer_url}{_OPENID_CONFIGURATION_SUFFIX}"
            )
            self.oauth_authorization_server_metadata_url = (
                f"{issuer_url}{_OAUTH_AUTHORIZATION_SERVER_SUFFIX}"
            )
        else:
            raise ValueError(
                "Either config_url (new API) or both project_id and descope_base_url (old API) must be provided"
            )

        if (
            parsed_scopes_supported is None
            and parsed_required_scopes is None
            and self.openid_configuration_url is not None
        ):
            openid_configuration = _fetch_openid_configuration(
                self.openid_configuration_url
            )
            if openid_configuration is not None:
                discovered_scopes = openid_configuration.get("scopes_supported")
                if isinstance(discovered_scopes, list):
                    parsed_scopes_supported = [
                        scope for scope in discovered_scopes if isinstance(scope, str)
                    ]
                    if parsed_scopes_supported:
                        logger.info(
                            "Discovered scopes_supported from Descope well-known: %s",
                            parsed_scopes_supported,
                        )

        # Create default JWT verifier if none provided
        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.descope_base_url}/{self.project_id}/.well-known/jwks.json",
                issuer=issuer_url,
                algorithm="RS256",
                audience=self.project_id,
                required_scopes=parsed_required_scopes,
            )

        # Initialize RemoteAuthProvider with Descope as the authorization server
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(issuer_url)],
            base_url=self.base_url,
            scopes_supported=parsed_scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
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
            metadata_urls = [
                self.oauth_authorization_server_metadata_url,
                f"{self.descope_base_url}/v1/apps/{self.project_id}{_OAUTH_AUTHORIZATION_SERVER_SUFFIX}",
            ]
            try:
                async with httpx.AsyncClient() as client:
                    last_error: Exception | None = None
                    for metadata_url in metadata_urls:
                        if not metadata_url:
                            continue
                        try:
                            response = await client.get(metadata_url)
                            response.raise_for_status()
                            metadata = response.json()
                            return JSONResponse(metadata)
                        except Exception as exc:
                            last_error = exc
                            continue
                    raise last_error or RuntimeError(
                        "No Descope authorization server metadata URL configured"
                    )
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
