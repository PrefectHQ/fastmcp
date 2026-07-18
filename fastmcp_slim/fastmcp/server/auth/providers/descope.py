"""Descope authentication provider for FastMCP.

This module provides DescopeProvider - a complete authentication solution that integrates
with Descope's OAuth 2.1 and OpenID Connect services, supporting Dynamic Client Registration (DCR)
for seamless MCP client authentication.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx2
from mcp.server.auth.json_response import PydanticJSONResponse
from mcp.server.auth.routes import build_resource_metadata_url, cors_middleware
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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


async def _discover_scopes(openid_configuration_url: str) -> list[str] | None:
    try:
        async with httpx2.AsyncClient() as client:
            response = await client.get(openid_configuration_url, timeout=10.0)
            response.raise_for_status()
        scopes = response.json().get("scopes_supported")
        if isinstance(scopes, list):
            parsed = [scope for scope in scopes if isinstance(scope, str)]
            if not scopes or parsed:
                return parsed
    except Exception:
        logger.warning(
            "Failed to fetch Descope OpenID configuration from %s",
            openid_configuration_url,
            exc_info=True,
        )
    return None


class DescopeProvider(RemoteAuthProvider):
    """Descope metadata provider for Dynamic Client Registration (DCR).

    The provider accepts either a resource-specific Descope MCP Server URL such
    as `/v1/apps/agentic/P.../M.../.well-known/openid-configuration` or a
    project-level inbound app URL such as
    `/v1/apps/P.../.well-known/openid-configuration`.

    When neither `scopes_supported` nor `required_scopes` is provided, advertised
    scopes are discovered lazily from the OpenID configuration. Use
    `scopes_supported` and `required_scopes` together when the scopes clients
    should request differ from the scopes enforced during token validation.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.descope import DescopeProvider

        auth = DescopeProvider(
            config_url=(
                "https://api.descope.com/v1/apps/P.../"
                ".well-known/openid-configuration"
            ),
            base_url="https://your-fastmcp-server.com",
        )

        mcp = FastMCP("My App", auth=auth)
        ```

    See [Descope's inbound app documentation](https://docs.descope.com/identity-federation/inbound-apps/creating-inbound-apps#method-2-dynamic-client-registration-dcr)
    for DCR setup instructions.
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
        """Initialize the Descope provider.

        Args:
            base_url: Public URL of this FastMCP server.
            config_url: A resource-specific or project-level Descope OpenID
                configuration URL. When provided, `project_id` and
                `descope_base_url` are ignored.
            project_id: Descope project ID. Used with `descope_base_url` for
                backwards compatibility.
            descope_base_url: Descope API base URL. Used with `project_id` for
                backwards compatibility.
            required_scopes: Scopes required during token validation. When
                `scopes_supported` is omitted, these are also advertised to clients.
            scopes_supported: Scopes advertised to OAuth clients. When both this
                and `required_scopes` are omitted, scopes are discovered lazily
                from `config_url`.
            resource_name: Optional protected resource name.
            resource_documentation: Optional protected resource documentation URL.
            token_verifier: Optional custom token verifier. A Descope JWT verifier
                is created when omitted.
        """
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

        # Advertised scopes are discovered from Descope's OpenID configuration
        # only when the caller supplied neither explicit advertised scopes nor
        # required scopes. Discovery is deferred to the first protected resource
        # metadata request (see get_routes) so construction never performs I/O
        # and a transient failure can be retried instead of being frozen for the
        # provider's lifetime.
        custom_verifier_scopes = (
            token_verifier.scopes_supported if token_verifier is not None else []
        )
        self._scopes_discovery_enabled = (
            parsed_scopes_supported is None
            and parsed_required_scopes is None
            and not custom_verifier_scopes
        )
        self._discovered_scopes: list[str] | None = None
        self._scopes_discovered = False
        self._scopes_discovery_lock = asyncio.Lock()

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

    async def _get_scopes_supported(self) -> list[str] | None:
        """Return the advertised scopes, discovering them lazily if enabled.

        The result of a successful discovery is cached for the provider's
        lifetime. Transient failures return ``None`` without caching, so the
        next protected resource metadata request retries discovery.
        """
        if self._scopes_discovered:
            return self._discovered_scopes

        async with self._scopes_discovery_lock:
            if self._scopes_discovered:
                return self._discovered_scopes

            scopes = await _discover_scopes(self.openid_configuration_url)
            if scopes is not None:
                self._discovered_scopes = scopes
                self._scopes_discovered = True
            return scopes

    def _create_protected_resource_route(self, resource_url: AnyHttpUrl) -> Route:
        """Build a protected resource metadata route that discovers scopes lazily.

        Mirrors ``create_protected_resource_routes`` (RFC 9728) but resolves
        ``scopes_supported`` per request so the value can be discovered from
        Descope after construction.
        """

        async def protected_resource_metadata(request: Request) -> Response:
            scopes_supported = await self._get_scopes_supported()
            metadata = ProtectedResourceMetadata(
                resource=resource_url,
                authorization_servers=self.authorization_servers,
                scopes_supported=scopes_supported,
                resource_name=self.resource_name,
                resource_documentation=self.resource_documentation,
            )
            cache_control = (
                "public, max-age=3600" if self._scopes_discovered else "no-store"
            )
            return PydanticJSONResponse(
                content=metadata,
                headers={"Cache-Control": cache_control},
            )

        well_known_path = urlparse(str(build_resource_metadata_url(resource_url))).path
        return Route(
            well_known_path,
            endpoint=cors_middleware(protected_resource_metadata, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        if self._scopes_discovery_enabled:
            # Serve protected resource metadata from an async handler that
            # discovers scopes_supported lazily. The parent's static route would
            # freeze the (as-yet-unknown) scopes at startup.
            self.set_mcp_path(mcp_path)
            routes = []
            resource_url = self._get_resource_url(mcp_path)
            if resource_url:
                routes.append(self._create_protected_resource_route(resource_url))
        else:
            # Advertised scopes are already known; the parent builds the static
            # protected resource metadata route with no network access.
            routes = super().get_routes(mcp_path)

        async def oauth_authorization_server_metadata(request):
            metadata_urls = [self.oauth_authorization_server_metadata_url]
            project_metadata_url = (
                f"{self.descope_base_url}/v1/apps/{self.project_id}{_OAUTH_WK}"
            )
            if project_metadata_url != self.oauth_authorization_server_metadata_url:
                metadata_urls.append(project_metadata_url)
            try:
                async with httpx2.AsyncClient() as client:
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
