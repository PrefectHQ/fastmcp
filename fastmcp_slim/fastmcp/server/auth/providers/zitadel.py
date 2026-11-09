"""ZITADEL OAuth provider for FastMCP.

This module provides a ZITADEL integration that fronts a static ZITADEL web
application with FastMCP's OAuth proxy. MCP clients register with FastMCP
(through Dynamic Client Registration or CIMD), while every user login is
delegated to the single, pre-registered ZITADEL application.

ZITADEL's own Dynamic Client Registration does not bind tokens to the MCP
server via the `aud` claim, and ZITADEL does not support CIMD, so a static
application behind the OAuth proxy is the robust choice.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.zitadel import ZitadelProvider

    auth = ZitadelProvider(
        domain="https://my-instance-abc123.zitadel.cloud",
        client_id="your-zitadel-client-id",
        client_secret="your-zitadel-client-secret",
        base_url="http://localhost:8000",
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

from typing import Literal

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth.identity_assertion import IdentityAssertion
from fastmcp.server.auth.oidc_proxy import (
    DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS,
    OIDCProxy,
)
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def _normalize_domain(domain: str) -> str:
    """Turn a ZITADEL domain into a base URL without trailing slash.

    Accepts a bare host ("my-instance.zitadel.cloud", defaults to HTTPS) or a
    full URL ("http://localhost:8080" for a local self-hosted instance).
    """
    domain = domain.strip().rstrip("/")
    if not domain.startswith(("https://", "http://")):
        domain = f"https://{domain}"
    return domain


class ZitadelProvider(OIDCProxy):
    """ZITADEL OAuth provider for FastMCP.

    Bridges MCP clients to a static ZITADEL web application through the OAuth
    proxy. Endpoints are discovered from the instance's OpenID configuration.

    Upstream access tokens are validated through ZITADEL's token introspection
    endpoint rather than as JWTs, for two reasons:

    - ZITADEL issues opaque access tokens by default; JWT access tokens are an
      opt-in application setting.
    - ZITADEL's JWT access tokens carry no `scope` claim, so required scopes
      could not be enforced on them.

    Introspection works for both token types, returns the granted scopes, and
    reflects revocations immediately. ZITADEL only reports a token as active
    when its audience contains the calling application's client ID or project
    ID; the verifier additionally requires the client ID, so tokens issued to
    sibling applications of the same ZITADEL project are rejected.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.zitadel import ZitadelProvider

        auth = ZitadelProvider(
            domain="https://my-instance-abc123.zitadel.cloud",
            client_id="123456789012345678",
            client_secret="your-zitadel-client-secret",
            base_url="https://my-server.com",
            required_scopes=["openid", "offline_access"],
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        required_scopes: list[str] | str | None = None,
        valid_scopes: list[str] | None = None,
        token_endpoint_auth_method: Literal[
            "client_secret_basic", "client_secret_post"
        ] = "client_secret_basic",
        introspection_cache_ttl_seconds: int | None = None,
        timeout_seconds: int | None = DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        fallback_refresh_token_expiry_seconds: int | None = None,
        fastmcp_access_token_expiry_seconds: int | None = None,
        token_expiry_threshold_seconds: int = 0,
        enable_cimd: bool = True,
        identity_assertion: IdentityAssertion | None = None,
    ) -> None:
        """Initialize the ZITADEL OAuth provider.

        Args:
            domain: ZITADEL instance domain, e.g. "my-instance-abc123.zitadel.cloud"
                or a full URL such as "https://auth.example.com". Bare hosts default
                to HTTPS; pass "http://localhost:8080" for a local self-hosted
                instance.
            client_id: Client ID of the ZITADEL web application
            client_secret: Client secret of the ZITADEL web application. Also used to
                authenticate to ZITADEL's introspection endpoint.
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            resource_base_url: Optional public base URL for the protected resource metadata
                and token audience. Defaults to `base_url`.
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path registered in the ZITADEL application
                (defaults to "/auth/callback")
            required_scopes: Scopes required on every token (defaults to ["openid"]).
                Add "offline_access" to receive ZITADEL refresh tokens; the
                application must then have the Refresh Token grant enabled.
            valid_scopes: The complete set of scopes MCP clients may request,
                advertised in the `/.well-known` metadata. Defaults to
                `required_scopes`.
            token_endpoint_auth_method: How FastMCP authenticates to ZITADEL's token
                endpoint. Must match the application's authentication method in
                ZITADEL: "client_secret_basic" (ZITADEL "Basic", the default) or
                "client_secret_post" (ZITADEL "Post"). Introspection always uses
                HTTP Basic, the only secret-based method ZITADEL accepts there.
            introspection_cache_ttl_seconds: How long to cache introspection
                results. Disabled by default (None) so revocations take effect
                immediately; set e.g. 60 to reduce load on ZITADEL.
            timeout_seconds: Timeout, in seconds, for the OIDC discovery request made
                during construction and for introspection requests. Defaults to 10
                seconds. Pass None to fall back to the HTTP client's own default
                timeout for discovery (introspection then uses 10 seconds).
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, an encrypted file store will be created in the data directory
                (derived from `platformdirs`).
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to ZITADEL.
                When False, authorization proceeds directly without user confirmation.
                When "external", authorization follows the same direct path as False,
                but the warning is suppressed as an operator acknowledgment that
                equivalent protections are enforced externally.
                SECURITY WARNING: Only set to False for local development or testing environments.
            consent_csp_policy: Content Security Policy for the consent page.
                See `OAuthProxy` for details.
            forward_resource: Whether to forward the RFC 8707 `resource` parameter to
                ZITADEL (default True). ZITADEL currently ignores it at the
                authorization endpoint.
            fallback_refresh_token_expiry_seconds: Lifetime for the FastMCP-issued
                refresh token when the upstream provider omits `refresh_expires_in`,
                which ZITADEL does. Defaults to 1 year. The upstream refresh remains
                the source of truth. See `OAuthProxy` for details.
            fastmcp_access_token_expiry_seconds: Lifetime for the FastMCP-issued access
                token, decoupling it from ZITADEL's `expires_in`. Defaults to None
                (mirror the upstream lifetime). See `OAuthProxy` for details.
            token_expiry_threshold_seconds: Number of seconds before actual expiry to
                treat a token as expired, refreshing early to avoid races. Defaults to 0.
            enable_cimd: Enable CIMD (Client ID Metadata Document) support for URL-based
                client IDs (default True). CIMD is handled by FastMCP, so it works even
                though ZITADEL does not support it. Set to False to disable.
            identity_assertion: Optional SEP-990 ID-JAG configuration for
                identity-bound token exchange. Disabled by default.
        """
        if not domain:
            raise ValueError("Missing required ZITADEL domain")
        if not client_secret:
            raise ValueError(
                "Missing required client secret: ZITADEL's introspection endpoint "
                "requires client authentication"
            )

        self.domain = _normalize_domain(domain)
        self.client_id = client_id
        self._client_secret = client_secret
        self._introspection_cache_ttl_seconds = introspection_cache_ttl_seconds
        self._introspection_timeout_seconds = (
            timeout_seconds or DEFAULT_OIDC_DISCOVERY_TIMEOUT_SECONDS
        )

        zitadel_required_scopes = (
            parse_scopes(required_scopes) if required_scopes is not None else ["openid"]
        )

        # `audience` is deliberately not passed: ZITADEL ignores an `audience`
        # request parameter (project audiences are requested through
        # `urn:zitadel:iam:org:project:id:{projectId}:aud` scopes instead).
        super().__init__(
            config_url=f"{self.domain}/.well-known/openid-configuration",
            client_id=client_id,
            client_secret=client_secret,
            timeout_seconds=timeout_seconds,
            required_scopes=zitadel_required_scopes,
            base_url=base_url,
            resource_base_url=resource_base_url,
            issuer_url=issuer_url,
            redirect_path=redirect_path,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            valid_scopes=valid_scopes,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            token_endpoint_auth_method=token_endpoint_auth_method,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            fallback_refresh_token_expiry_seconds=fallback_refresh_token_expiry_seconds,
            fastmcp_access_token_expiry_seconds=fastmcp_access_token_expiry_seconds,
            token_expiry_threshold_seconds=token_expiry_threshold_seconds,
            enable_cimd=enable_cimd,
            identity_assertion=identity_assertion,
        )

        logger.debug(
            "Initialized ZITADEL OAuth provider for client %s with scopes: %s",
            client_id,
            zitadel_required_scopes,
        )

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> IntrospectionTokenVerifier:
        """Create an introspection-based verifier for ZITADEL access tokens.

        Args:
            algorithm: Ignored; introspection does not verify signatures locally
            audience: Ignored; tokens are always bound to this application's client ID
            required_scopes: Scopes required on every token
            timeout_seconds: Ignored; the provider's own timeout is used
        """
        introspection_endpoint = self.oidc_config.introspection_endpoint
        if not introspection_endpoint:
            raise ValueError(
                "ZITADEL OpenID configuration does not advertise an "
                "introspection_endpoint"
            )

        return IntrospectionTokenVerifier(
            introspection_url=str(introspection_endpoint),
            client_id=self.client_id,
            client_secret=self._client_secret,
            client_auth_method="client_secret_basic",
            audience=self.client_id,
            required_scopes=required_scopes,
            timeout_seconds=self._introspection_timeout_seconds,
            cache_ttl_seconds=self._introspection_cache_ttl_seconds,
        )
