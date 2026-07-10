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

_OPENID_WK = "/.well-known/openid-configuration"
_OAUTH_WK = "/.well-known/oauth-authorization-server"


def _parse_descope_config_url(config_url: str) -> tuple[str, str, str, str]:
    openid_url = config_url.rstrip("/")
    if not openid_url.endswith(_OPENID_WK):
        openid_url = f"{openid_url}{_OPENID_WK}"

    issuer_url = openid_url[: -len(_OPENID_WK)]
    parsed = urlparse(issuer_url)
    parts = parsed.path.strip("/").split("/")
    descope_base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    if "agentic" in parts:
        index = parts.index("agentic") + 1
        project_id = parts[index] if index < len(parts) else ""
    elif "apps" in parts:
        index = parts.index("apps") + 1
        project_id = parts[index] if index < len(parts) else ""
        if project_id == "agentic":
            project_id = ""
    else:
        project_id = ""

    if not project_id:
        raise ValueError(f"Could not extract project_id from config_url: {issuer_url}")

    return descope_base_url, project_id, issuer_url, openid_url


def _discover_scopes(openid_configuration_url: str) -> list[str] | None:
    try:
        response = httpx.get(openid_configuration_url, timeout=10.0)
        response.raise_for_status()
        scopes = response.json().get("scopes_supported")
        if isinstance(scopes, list):
            parsed = [scope for scope in scopes if isinstance(scope, str)]
            return parsed or None
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
        scopes_supported: list[str] | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        self.base_url = AnyHttpUrl(str(base_url).rstrip("/"))

        parsed_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else None
        )
        parsed_scopes_supported = (
            parse_scopes(scopes_supported) if scopes_supported is not None else None
        )

        if config_url is not None:
            (
                self.descope_base_url,
                self.project_id,
                issuer_url,
                self.openid_configuration_url,
            ) = _parse_descope_config_url(str(config_url))
        elif project_id is not None and descope_base_url is not None:
            self.project_id = project_id
            descope_base_url_str = str(descope_base_url).rstrip("/")
            if not descope_base_url_str.startswith(("http://", "https://")):
                descope_base_url_str = f"https://{descope_base_url_str}"
            self.descope_base_url = descope_base_url_str
            issuer_url = f"{self.descope_base_url}/v1/apps/{self.project_id}"
            self.openid_configuration_url = f"{issuer_url}{_OPENID_WK}"
        else:
            raise ValueError(
                "Either config_url (new API) or both project_id and descope_base_url (old API) must be provided"
            )

        self.oauth_authorization_server_metadata_url = (
            self.openid_configuration_url.replace(_OPENID_WK, _OAUTH_WK)
        )

        if (
            parsed_scopes_supported is None
            and parsed_required_scopes is None
        ):
            parsed_scopes_supported = _discover_scopes(self.openid_configuration_url)

        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.descope_base_url}/{self.project_id}/.well-known/jwks.json",
                issuer=issuer_url,
                algorithm="RS256",
                audience=self.project_id,
                required_scopes=parsed_required_scopes,
            )

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
        routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            metadata_urls = [
                self.oauth_authorization_server_metadata_url,
                f"{self.descope_base_url}/v1/apps/{self.project_id}{_OAUTH_WK}",
            ]
            try:
                async with httpx.AsyncClient() as client:
                    for metadata_url in metadata_urls:
                        try:
                            response = await client.get(metadata_url)
                            response.raise_for_status()
                            return JSONResponse(response.json())
                        except Exception:
                            continue
            except Exception as e:
                return JSONResponse(
                    {
                        "error": "server_error",
                        "error_description": f"Failed to fetch Descope metadata: {e}",
                    },
                    status_code=500,
                )

            return JSONResponse(
                {
                    "error": "server_error",
                    "error_description": "Failed to fetch Descope metadata",
                },
                status_code=500,
            )

        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_authorization_server_metadata,
                methods=["GET"],
            )
        )

        return routes
