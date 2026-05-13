"""Keycloak authentication provider for FastMCP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import AnyHttpUrl

from fastmcp.server.auth import RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from key_value.aio.protocols import AsyncKeyValue

logger = get_logger(__name__)


class KeycloakAuthProvider(RemoteAuthProvider):
    """Keycloak authentication provider using Dynamic Client Registration (DCR).

    Requires Keycloak 26.6.0 or later, which includes the fix for DCR compatibility
    with MCP clients (https://github.com/keycloak/keycloak/pull/45309).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

        auth = KeycloakAuthProvider(
            realm_url="https://keycloak.example.com/realms/myrealm",
            base_url="https://my-mcp-server.example.com",
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        realm_url: AnyHttpUrl | str,
        base_url: AnyHttpUrl | str,
        required_scopes: list[str] | str | None = None,
        audience: str | list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
    ):
        """Initialize the Keycloak auth provider.

        Args:
            realm_url: Keycloak realm URL (e.g., "https://keycloak.example.com/realms/myrealm")
            base_url: Public URL of this FastMCP server
            required_scopes: Scopes to require on incoming tokens. Defaults to
                ["openid"], which ensures the `sub` claim (user identifier) is
                present in the access token. Override to require additional scopes.
            audience: Optional audience(s) for JWT validation. Recommended for production.
            token_verifier: Optional custom token verifier. Defaults to a JWTVerifier
                configured for Keycloak's JWKS endpoint and issuer.
        """
        self.realm_url = str(realm_url).rstrip("/")
        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        if token_verifier is None:
            token_verifier = JWTVerifier(
                jwks_uri=f"{self.realm_url}/protocol/openid-connect/certs",
                issuer=self.realm_url,
                algorithm="RS256",
                required_scopes=parsed_scopes,
                audience=audience,
            )

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.realm_url)],
            base_url=AnyHttpUrl(str(base_url).rstrip("/")),
        )


class KeycloakOAuthProxy(OAuthProxy):
    """OAuth proxy for Keycloak identity providers.

    Use this instead of `OAuthProxy` when proxying to Keycloak. It handles
    Keycloak-specific token response conventions, most importantly
    `refresh_expires_in=0`, which Keycloak uses to indicate that a refresh
    token obtained with the `offline_access` scope never expires. Standard
    `OAuthProxy` treats `0` as an unknown expiry, which causes the FastMCP
    refresh token TTL to shrink on every refresh cycle until it hits zero
    and forces the user to re-authenticate — even though the Keycloak
    offline token is still valid.

    All other behaviour is identical to `OAuthProxy`. Pass `realm_url` for
    automatic endpoint discovery, or supply the individual endpoint URLs
    directly.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.keycloak import KeycloakOAuthProxy

        auth = KeycloakOAuthProxy(
            realm_url="https://keycloak.example.com/realms/myrealm",
            upstream_client_id="my-client",
            upstream_client_secret="my-secret",
            base_url="https://my-mcp-server.example.com",
            jwt_signing_key="some-secret",
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    # Keycloak uses refresh_expires_in=0 for offline_access tokens ("never expires").
    _zero_refresh_expiry_means_never_expires: bool = True

    def __init__(
        self,
        *,
        realm_url: AnyHttpUrl | str | None = None,
        upstream_client_id: str,
        upstream_client_secret: str | None = None,
        base_url: AnyHttpUrl | str,
        required_scopes: list[str] | str | None = None,
        audience: str | list[str] | None = None,
        token_verifier: TokenVerifier | None = None,
        # Direct endpoint overrides (optional if realm_url is provided)
        upstream_authorization_endpoint: str | None = None,
        upstream_token_endpoint: str | None = None,
        upstream_revocation_endpoint: str | None = None,
        # Pass-through OAuthProxy options
        jwt_signing_key: str | bytes | None = None,
        client_storage: AsyncKeyValue | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        allowed_client_redirect_uris: list[str] | None = None,
        fallback_refresh_token_expiry_seconds: int | None = None,
    ):
        """Initialize the Keycloak OAuth proxy.

        Args:
            realm_url: Keycloak realm URL (e.g., "https://keycloak.example.com/realms/myrealm").
                Used to derive authorization and token endpoints automatically. Required unless
                `upstream_authorization_endpoint` and `upstream_token_endpoint` are both provided.
            upstream_client_id: Client ID of the application registered in Keycloak.
            upstream_client_secret: Client secret. Optional for public clients.
            base_url: Public URL of this FastMCP server.
            required_scopes: Scopes to require on incoming tokens. Defaults to `["openid"]`.
            audience: Optional JWT audience for token validation. Recommended for production.
            token_verifier: Custom token verifier. Defaults to a JWTVerifier configured
                for the Keycloak realm's JWKS endpoint.
            upstream_authorization_endpoint: Override the authorization endpoint URL.
                Required if `realm_url` is not provided.
            upstream_token_endpoint: Override the token endpoint URL.
                Required if `realm_url` is not provided.
            upstream_revocation_endpoint: Optional token revocation endpoint.
            jwt_signing_key: Secret for signing FastMCP JWTs.
            client_storage: Storage backend for OAuth state.
            require_authorization_consent: Consent screen behaviour (default True).
            allowed_client_redirect_uris: Allowed MCP client redirect URI patterns.
            fallback_refresh_token_expiry_seconds: FastMCP RT lifetime when Keycloak
                returns `refresh_expires_in=0`. Defaults to 1 year. The token is
                re-issued automatically on every refresh cycle, so active sessions
                remain valid indefinitely.
        """
        if realm_url is None and (
            upstream_authorization_endpoint is None or upstream_token_endpoint is None
        ):
            raise ValueError(
                "Either realm_url or both upstream_authorization_endpoint and "
                "upstream_token_endpoint must be provided."
            )

        realm = str(realm_url).rstrip("/") if realm_url else None
        oidc_base = f"{realm}/protocol/openid-connect" if realm else None

        resolved_auth_endpoint = upstream_authorization_endpoint or f"{oidc_base}/auth"
        resolved_token_endpoint = upstream_token_endpoint or f"{oidc_base}/token"
        resolved_revocation_endpoint = upstream_revocation_endpoint or (
            f"{oidc_base}/revoke" if oidc_base else None
        )

        parsed_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        if token_verifier is None:
            if realm is None:
                raise ValueError(
                    "token_verifier must be provided when realm_url is not set."
                )
            token_verifier = JWTVerifier(
                jwks_uri=f"{realm}/protocol/openid-connect/certs",
                issuer=realm,
                algorithm="RS256",
                required_scopes=parsed_scopes,
                audience=audience,
            )

        super().__init__(
            upstream_authorization_endpoint=resolved_auth_endpoint,
            upstream_token_endpoint=resolved_token_endpoint,
            upstream_revocation_endpoint=resolved_revocation_endpoint,
            upstream_client_id=upstream_client_id,
            upstream_client_secret=upstream_client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            jwt_signing_key=jwt_signing_key,
            client_storage=client_storage,
            require_authorization_consent=require_authorization_consent,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            fallback_refresh_token_expiry_seconds=fallback_refresh_token_expiry_seconds,
        )
