"""OAuth Proxy Provider for FastMCP.

This provider acts as a transparent proxy to an upstream OAuth Authorization Server,
handling Dynamic Client Registration locally while forwarding all other OAuth flows.
This enables authentication with upstream providers that don't support DCR or have
restricted client registration policies.

Key features:
- Proxies authorization and token endpoints to upstream server
- Implements local Dynamic Client Registration with fixed upstream credentials
- Validates tokens using upstream JWKS
- Maintains minimal local state for bookkeeping
- Enhanced logging with request correlation

This implementation is based on the OAuth 2.1 specification and is designed for
production use with enterprise identity providers.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from base64 import urlsafe_b64encode
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import anyio
import httpx2
from authlib.common.security import generate_token
from cryptography.fernet import Fernet
from joserfc.errors import JoseError
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, SecretStr
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from typing_extensions import override

from fastmcp import settings
from fastmcp.server.auth.auth import (
    OAuthProvider,
    PrivateKeyJWTClientAuthenticator,
    TokenHandler,
    TokenVerifier,
)
from fastmcp.server.auth.cimd import CIMDClientManager
from fastmcp.server.auth.handlers.authorize import AuthorizationHandler
from fastmcp.server.auth.identity_assertion import (
    JWT_BEARER_GRANT_TYPE,
    IdentityAssertion,
    IdentityAssertionError,
    IdentityAssertionValidator,
    normalize_resource_url,
    server_url_has_query,
)
from fastmcp.server.auth.jwt_issuer import (
    JWTIssuer,
    derive_jwt_key,
)
from fastmcp.server.auth.oauth_proxy.consent import ConsentMixin
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS,
    DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
    DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    ClientCode,
    JTIMapping,
    OAuthTransaction,
    ProxyDCRClient,
    RefreshTokenMetadata,
    UpstreamTokenSet,
    _hash_token,
)
from fastmcp.server.auth.oauth_proxy.ui import create_error_html
from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client
from fastmcp.server.auth.redirect_validation import (
    build_client_redirect,
    validate_redirect_uri,
)
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_REFRESH_LOCK_CACHE_SIZE = 10_000

#: Marker claim identifying a FastMCP access token minted from a SEP-990 ID-JAG.
#: These tokens are self-contained (they carry the asserted subject directly) and
#: are validated without the upstream token-swap that regular proxy tokens use.
_ID_JAG_GRANT_MARKER = "id_jag"


def _assertion_granted_scopes(claims: dict[str, Any]) -> list[str]:
    """Scopes granted by an ID-JAG, from its `scope` or `scp` claim."""
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp]
    if isinstance(scp, str):
        return scp.split()
    return []


class OAuthProxy(OAuthProvider, ConsentMixin):
    """OAuth provider that presents a DCR-compliant interface while proxying to non-DCR IDPs.

    Purpose
    -------
    MCP clients expect OAuth providers to support Dynamic Client Registration (DCR),
    where clients can register themselves dynamically and receive unique credentials.
    Most enterprise IDPs (Google, GitHub, Azure AD, etc.) don't support DCR and require
    pre-registered OAuth applications with fixed credentials.

    This proxy bridges that gap by:
    - Presenting a full DCR-compliant OAuth interface to MCP clients
    - Translating DCR registration requests to use pre-configured upstream credentials
    - Proxying all OAuth flows to the upstream IDP with appropriate translations
    - Managing the state and security requirements of both protocols

    Architecture Overview
    --------------------
    The proxy maintains a single OAuth app registration with the upstream provider
    while allowing unlimited MCP clients to register and authenticate dynamically.
    It implements the complete OAuth 2.1 + DCR specification for clients while
    translating to whatever OAuth variant the upstream provider requires.

    Key Translation Challenges Solved
    ---------------------------------
    1. Dynamic Client Registration:
       - MCP clients expect to register dynamically and get unique credentials
       - Upstream IDPs require pre-registered apps with fixed credentials
       - Solution: Accept DCR requests, return shared upstream credentials

    2. Dynamic Redirect URIs:
       - MCP clients use random localhost ports that change between sessions
       - Upstream IDPs require fixed, pre-registered redirect URIs
       - Solution: Use proxy's fixed callback URL with upstream, forward to client's dynamic URI

    3. Authorization Code Mapping:
       - Upstream returns codes for the proxy's redirect URI
       - Clients expect codes for their own redirect URIs
       - Solution: Exchange upstream code server-side, issue new code to client

    4. State Parameter Collision:
       - Both client and proxy need to maintain state through the flow
       - Only one state parameter available in OAuth
       - Solution: Use transaction ID as state with upstream, preserve client's state

    5. Token Management:
       - Clients may expect different token formats/claims than upstream provides
       - Need to track tokens for revocation and refresh
       - Solution: Store token relationships, forward upstream tokens transparently

    OAuth Flow Implementation
    ------------------------
    1. Client Registration (DCR):
       - Accept any client registration request
       - Store ProxyDCRClient that accepts dynamic redirect URIs

    2. Authorization:
       - Store transaction mapping client details to proxy flow
       - Redirect to upstream with proxy's fixed redirect URI
       - Use transaction ID as state parameter with upstream

    3. Upstream Callback:
       - Exchange upstream authorization code for tokens (server-side)
       - Generate new authorization code bound to client's PKCE challenge
       - Redirect to client's original dynamic redirect URI

    4. Token Exchange:
       - Validate client's code and PKCE verifier
       - Return previously obtained upstream tokens
       - Clean up one-time use authorization code

    5. Token Refresh:
       - Forward refresh requests to upstream
       - Handle token rotation if upstream issues new refresh token
       - Update local token mappings

    State Management
    ---------------
    The proxy maintains minimal but crucial state via pluggable storage (client_storage):
    - _oauth_transactions: Active authorization flows with client context
    - _client_codes: Authorization codes with PKCE challenges and upstream tokens
    - _jti_mapping_store: Maps FastMCP token JTIs to upstream token IDs
    - _refresh_token_store: Refresh token metadata (keyed by token hash)

    All state is stored in the configured client_storage backend (Redis, disk, etc.)
    enabling horizontal scaling across multiple instances.

    Security Considerations
    ----------------------
    - Refresh tokens stored by hash only (defense in depth if storage compromised)
    - PKCE enforced end-to-end (client to proxy, proxy to upstream)
    - Authorization codes are single-use with short expiry
    - Transaction IDs are cryptographically random
    - All state is cleaned up after use to prevent replay
    - Token validation delegates to upstream provider

    Provider Compatibility
    ---------------------
    Works with any OAuth 2.0 provider that supports:
    - Authorization code flow
    - Fixed redirect URI (configured in provider's app settings)
    - Standard token endpoint

    Handles provider-specific requirements:
    - Google: Ensures minimum scope requirements
    - GitHub: Compatible with OAuth Apps and GitHub Apps
    - Azure AD: Handles tenant-specific endpoints
    - Generic: Works with any spec-compliant provider
    """

    def __init__(
        self,
        *,
        # Upstream server configuration
        upstream_authorization_endpoint: str,
        upstream_token_endpoint: str,
        upstream_client_id: str,
        upstream_client_secret: str | None = None,
        upstream_revocation_endpoint: str | None = None,
        # Token validation
        token_verifier: TokenVerifier,
        # FastMCP server configuration
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        # Client redirect URI validation
        allowed_client_redirect_uris: list[str] | None = None,
        valid_scopes: list[str] | None = None,
        # PKCE configuration
        forward_pkce: bool = True,
        # Resource indicator (RFC 8707)
        forward_resource: bool = True,
        # Token endpoint authentication
        token_endpoint_auth_method: str | None = None,
        # Extra parameters to forward to authorization endpoint
        extra_authorize_params: dict[str, str] | None = None,
        # Extra parameters to forward to token endpoint
        extra_token_params: dict[str, str] | None = None,
        # Client storage
        client_storage: AsyncKeyValue | None = None,
        # JWT signing key
        jwt_signing_key: str | bytes | None = None,
        # Consent screen configuration
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        # Token expiry fallback
        fallback_access_token_expiry_seconds: int | None = None,
        fallback_refresh_token_expiry_seconds: int | None = None,
        # FastMCP-issued access token lifetime (decoupled from upstream)
        fastmcp_access_token_expiry_seconds: int | None = None,
        # Token refresh threshold
        token_expiry_threshold_seconds: int = 0,
        # CIMD (Client ID Metadata Document) support
        enable_cimd: bool = True,
        # Identity assertion (SEP-990 ID-JAG) support
        identity_assertion: IdentityAssertion | None = None,
    ):
        """Initialize the OAuth proxy provider.

        Args:
            upstream_authorization_endpoint: URL of upstream authorization endpoint
            upstream_token_endpoint: URL of upstream token endpoint
            upstream_client_id: Client ID registered with upstream server
            upstream_client_secret: Client secret for upstream server. Optional for
                PKCE public clients or when using alternative credentials (e.g.,
                managed identity). When omitted, jwt_signing_key must be provided.
            upstream_revocation_endpoint: Optional upstream revocation endpoint
            token_verifier: Token verifier for validating access tokens
            base_url: Public URL of the server that exposes this FastMCP server; redirect path is
                relative to this URL
            resource_base_url: Optional public base URL for the protected resource metadata
                and token audience. Defaults to ``base_url``.
            redirect_path: Redirect path configured in upstream OAuth app (defaults to "/auth/callback")
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url)
            service_documentation_url: Optional service documentation URL
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                Patterns support wildcards (e.g., "http://localhost:*", "https://*.example.com/*").
                If None (default), DCR clients use registered redirect URIs, with loopback
                ports allowed to vary for MCP compatibility. Unsafe browser schemes are rejected.
                If empty list, no redirect URIs are allowed.
                These are for MCP clients performing loopback redirects, NOT for the upstream OAuth app.
            valid_scopes: List of all the possible valid scopes for a client.
                These are advertised to clients through the `/.well-known` endpoints. Defaults to `required_scopes` if not provided.
            forward_pkce: Whether to forward PKCE to upstream server (default True).
                Enable for providers that support/require PKCE (Google, Azure, AWS, etc.).
                Disable only if upstream provider doesn't support PKCE.
            token_endpoint_auth_method: Token endpoint authentication method for upstream server.
                Common values: "client_secret_basic", "client_secret_post", "none".
                Defaults to "client_secret_basic".
            extra_authorize_params: Additional parameters to forward to the upstream authorization endpoint.
                Useful for provider-specific parameters like Auth0's "audience".
                Example: {"audience": "https://api.example.com"}
            extra_token_params: Additional parameters to forward to the upstream token endpoint.
                Useful for provider-specific parameters during token exchange.
            client_storage: Storage backend for OAuth state (client registrations, tokens).
                If None, an encrypted file store will be created in the data directory.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes).
                If bytes are provided, they will be used as-is.
                If a string is provided, it will be derived into a 32-byte key using PBKDF2 (1.2M iterations).
                If not provided, it will be derived from the upstream client secret using HKDF.
            require_authorization_consent: Consent screen behavior (default True).
                - True: always show the consent screen before redirecting to the
                  upstream IdP. Strongest protection against AS-in-the-middle
                  attacks.
                - "remember": show the consent screen the first time, then silently
                  approve subsequent authorizations for the same (client_id,
                  redirect_uri) in the same browser. Cross-site navigations are
                  still prompted to block AS-in-the-middle attacks. Lower UX
                  friction, but weaker protection than True.
                - "external": skip the built-in consent screen; consent is handled
                  externally (e.g. by the upstream IdP or a custom login page).
                - False: skip consent entirely. SECURITY WARNING: only set to
                  False for local development or testing environments.
            consent_csp_policy: Content Security Policy for the consent page.
                If None (default), uses the built-in CSP policy with appropriate directives.
                If empty string "", disables CSP entirely (no meta tag is rendered).
                If a non-empty string, uses that as the CSP policy value.
                This allows organizations with their own CSP policies to override or disable
                the built-in CSP directives.
            fallback_access_token_expiry_seconds: Expiry time to use when upstream provider
                doesn't return `expires_in` in the token response. If not set, uses smart
                defaults: 1 hour if a refresh token is available (since we can refresh),
                or 1 year if no refresh token (for API-key-style tokens like GitHub OAuth Apps).
                Set explicitly to override these defaults.
            fallback_refresh_token_expiry_seconds: Expiry time to use when upstream provider
                doesn't return `refresh_expires_in` (e.g. Cognito, GitHub, many OIDC IdPs).
                Defaults to 1 year. Note: this only controls the FastMCP-issued refresh token
                lifetime — the actual upstream refresh remains the source of truth. If the
                upstream rejects the refresh, the client gets `invalid_grant` and re-auths,
                regardless of how much life is left on the FastMCP refresh token.
            fastmcp_access_token_expiry_seconds: Lifetime for the FastMCP-issued access
                token (JWT), decoupling it from the upstream provider's `expires_in`. By
                default (None) the FastMCP access token mirrors the upstream access token
                lifetime. The FastMCP JWT is a reference token — `load_access_token`
                re-validates the upstream token on every request and transparently refreshes
                it when expired — so issuing a longer-lived FastMCP token does not extend
                upstream access: a revoked or expired upstream session still fails validation
                and forces re-auth. Set this for bridges whose upstream issues short-lived
                access tokens (5-60 min) that some MCP clients can't refresh gracefully
                (e.g. `mcp-remote`), where the short client-facing TTL forces a full re-auth
                on every idle period. Only affects the FastMCP-issued token; the upstream
                token's real expiry is preserved internally to drive transparent refresh.
                When the upstream provider issues no refresh token there is no way to renew
                the access token, so the lifetime is capped at the upstream `expires_in`
                regardless of this value (it can still be used to shorten it).
            token_expiry_threshold_seconds: Number of seconds before actual expiry to consider
                a token as expired (default 0). This prevents race conditions where a token
                passes the expiry check but expires before the next operation completes.
                For example, set to 30 to refresh tokens that will expire within 30 seconds.
            enable_cimd: Enable CIMD (Client ID Metadata Document) support for URL-based
                client IDs. When True, clients can authenticate using HTTPS URLs as client
                IDs, with metadata fetched from the URL. Supports private_key_jwt auth.
            identity_assertion: Optional SEP-990 identity assertion (ID-JAG) configuration.
                When provided, the token endpoint accepts the RFC 7523 jwt-bearer grant
                carrying an ID-JAG issued by one of the configured trusted issuers, and
                mints a short-lived access token (no refresh token) for the asserted
                subject. When omitted, the grant is rejected as unsupported.
        """

        default_scopes = valid_scopes or token_verifier.required_scopes

        # Always enable DCR since we implement it locally for MCP clients
        client_registration_options = ClientRegistrationOptions(
            enabled=True,
            valid_scopes=list(default_scopes) if default_scopes is not None else None,
            default_scopes=list(default_scopes) if default_scopes is not None else None,
        )

        # Enable revocation only if upstream endpoint provided
        revocation_options = (
            RevocationOptions(enabled=True) if upstream_revocation_endpoint else None
        )

        super().__init__(
            base_url=base_url,
            resource_base_url=resource_base_url,
            issuer_url=issuer_url,
            service_documentation_url=service_documentation_url,
            client_registration_options=client_registration_options,
            revocation_options=revocation_options,
            required_scopes=token_verifier.required_scopes,
        )

        # Store upstream configuration
        self._upstream_authorization_endpoint: str = upstream_authorization_endpoint
        self._upstream_token_endpoint: str = upstream_token_endpoint
        self._upstream_client_id: str = upstream_client_id
        self._upstream_client_secret: SecretStr | None = (
            SecretStr(secret_value=upstream_client_secret)
            if upstream_client_secret is not None
            else None
        )
        self._upstream_revocation_endpoint: str | None = upstream_revocation_endpoint
        self._default_scope_str: str = " ".join(default_scopes or [])

        # Store redirect configuration
        if not redirect_path:
            self._redirect_path = "/auth/callback"
        else:
            self._redirect_path = (
                redirect_path if redirect_path.startswith("/") else f"/{redirect_path}"
            )

        if (
            isinstance(allowed_client_redirect_uris, list)
            and not allowed_client_redirect_uris
        ):
            logger.warning(
                "allowed_client_redirect_uris is empty list; no redirect URIs will be accepted. "
                "This will block all OAuth clients."
            )
        self._allowed_client_redirect_uris: list[str] | None = (
            allowed_client_redirect_uris
        )

        # PKCE configuration
        self._forward_pkce: bool = forward_pkce
        # Resource indicator (RFC 8707)
        self._forward_resource: bool = forward_resource

        # Token endpoint authentication
        self._token_endpoint_auth_method: str | None = token_endpoint_auth_method

        # Consent screen configuration
        self._require_authorization_consent: bool | Literal["remember", "external"] = (
            require_authorization_consent
        )
        self._consent_csp_policy: str | None = consent_csp_policy
        if require_authorization_consent == "external":
            logger.info(
                "Built-in consent screen disabled; consent is handled externally."
            )
        elif require_authorization_consent == "remember":
            logger.info(
                "Consent screen in 'remember' mode: silent consent on return visits "
                "for previously-approved clients (with Sec-Fetch-Site gating). "
                "Set require_authorization_consent=True for strongest protection "
                "against AS-in-the-middle attacks."
            )
        elif not require_authorization_consent:
            logger.warning(
                "Authorization consent screen disabled - only use for local development or testing. "
                "In production, this screen protects against confused deputy attacks."
            )

        # Extra parameters for authorization and token endpoints
        self._extra_authorize_params: dict[str, str] = extra_authorize_params or {}
        self._extra_token_params: dict[str, str] = extra_token_params or {}

        # Token expiry fallback (None means use smart default based on refresh token)
        self._fallback_access_token_expiry_seconds: int | None = (
            fallback_access_token_expiry_seconds
        )
        self._fallback_refresh_token_expiry_seconds: int = (
            fallback_refresh_token_expiry_seconds
            if fallback_refresh_token_expiry_seconds is not None
            else DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS
        )
        self._fastmcp_access_token_expiry_seconds: int | None = (
            fastmcp_access_token_expiry_seconds
        )
        self._token_expiry_threshold_seconds: int = token_expiry_threshold_seconds

        if jwt_signing_key is None:
            if upstream_client_secret is None:
                raise ValueError(
                    "jwt_signing_key is required when upstream_client_secret is not provided. "
                    "The JWT signing key cannot be derived without a client secret."
                )
            jwt_signing_key = derive_jwt_key(
                high_entropy_material=upstream_client_secret,
                salt="fastmcp-jwt-signing-key",
            )

        if isinstance(jwt_signing_key, str):
            if len(jwt_signing_key) < 12:
                logger.warning(
                    "jwt_signing_key is less than 12 characters; it is recommended to use a longer. "
                    "string for the key derivation."
                )
            jwt_signing_key = derive_jwt_key(
                low_entropy_material=jwt_signing_key,
                salt="fastmcp-jwt-signing-key",
            )

        # Store JWT signing key for deferred JWTIssuer creation in set_mcp_path()
        self._jwt_signing_key: bytes = jwt_signing_key
        # JWTIssuer will be created in set_mcp_path() with correct audience
        self._jwt_issuer: JWTIssuer | None = None

        # If the user does not provide a store, we will provide an encrypted file store.
        # The storage directory is derived from the encryption key so that different
        # keys get isolated directories (e.g. two servers on the same machine with
        # different keys won't collide). Decryption errors are treated as cache misses
        # rather than hard failures, so key rotation just causes re-registration.
        if client_storage is None:
            storage_encryption_key = derive_jwt_key(
                high_entropy_material=jwt_signing_key.decode(),
                salt="fastmcp-storage-encryption-key",
            )

            key_fingerprint = hashlib.sha256(storage_encryption_key).hexdigest()[:12]
            storage_dir = settings.home / "oauth-proxy" / key_fingerprint
            storage_dir.mkdir(parents=True, exist_ok=True)

            file_store = FileTreeStore(
                data_directory=storage_dir,
                key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(
                    storage_dir
                ),
                collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                    storage_dir
                ),
            )

            client_storage = FernetEncryptionWrapper(
                key_value=file_store,
                fernet=Fernet(key=storage_encryption_key),
                raise_on_decryption_error=False,
            )

        self._client_storage: AsyncKeyValue = client_storage

        # Cache HTTPS check to avoid repeated logging
        self._is_https: bool = str(self.base_url).startswith("https://")
        if not self._is_https:
            logger.warning(
                "Using non-secure cookies for development; deploy with HTTPS for production."
            )

        self._upstream_token_store: PydanticAdapter[UpstreamTokenSet] = PydanticAdapter[
            UpstreamTokenSet
        ](
            key_value=self._client_storage,
            pydantic_model=UpstreamTokenSet,
            default_collection="mcp-upstream-tokens",
            raise_on_validation_error=True,
        )

        self._client_store: PydanticAdapter[ProxyDCRClient] = PydanticAdapter[
            ProxyDCRClient
        ](
            key_value=self._client_storage,
            pydantic_model=ProxyDCRClient,
            default_collection="mcp-oauth-proxy-clients",
            raise_on_validation_error=True,
        )

        # OAuth transaction storage for IdP callback forwarding
        # Reuse client_storage with different collections for state management
        self._transaction_store: PydanticAdapter[OAuthTransaction] = PydanticAdapter[
            OAuthTransaction
        ](
            key_value=self._client_storage,
            pydantic_model=OAuthTransaction,
            default_collection="mcp-oauth-transactions",
            raise_on_validation_error=True,
        )

        self._code_store: PydanticAdapter[ClientCode] = PydanticAdapter[ClientCode](
            key_value=self._client_storage,
            pydantic_model=ClientCode,
            default_collection="mcp-authorization-codes",
            raise_on_validation_error=True,
        )

        # Storage for JTI mappings (FastMCP token -> upstream token)
        self._jti_mapping_store: PydanticAdapter[JTIMapping] = PydanticAdapter[
            JTIMapping
        ](
            key_value=self._client_storage,
            pydantic_model=JTIMapping,
            default_collection="mcp-jti-mappings",
            raise_on_validation_error=True,
        )

        # Refresh token metadata storage, keyed by token hash for security.
        # We only store metadata (not the token itself) - if storage is compromised,
        # attackers get hashes they can't reverse into usable tokens.
        self._refresh_token_store: PydanticAdapter[RefreshTokenMetadata] = (
            PydanticAdapter[RefreshTokenMetadata](
                key_value=self._client_storage,
                pydantic_model=RefreshTokenMetadata,
                default_collection="mcp-refresh-tokens",
                raise_on_validation_error=True,
            )
        )

        # Use the provided token validator
        self._token_validator: TokenVerifier = token_verifier

        # CIMD (Client ID Metadata Document) support
        self._cimd_manager: CIMDClientManager | None = None
        if enable_cimd:
            self._cimd_manager = CIMDClientManager(
                enable_cimd=True,
                default_scope=self._default_scope_str,
                allowed_redirect_uri_patterns=self._allowed_client_redirect_uris,
            )

        # Identity assertion (SEP-990 ID-JAG): the audience the ID-JAG must be
        # bound to is this authorization server's own issuer URL (base_url).
        self._identity_assertion: IdentityAssertion | None = identity_assertion
        self._identity_assertion_validator: IdentityAssertionValidator | None = None
        if identity_assertion is not None:
            self._identity_assertion_validator = IdentityAssertionValidator(
                config=identity_assertion,
                audience=str(self.base_url),
            )
        # ID-JAG access tokens are self-contained (no upstream token or JTI
        # mapping to delete), so revocation tracks their jtis here until the
        # token would expire anyway. Per-process, like ID-JAG replay tracking.
        self._revoked_id_jag_jtis: dict[str, float] = {}

        # Advisory locks for transparent upstream token refresh, keyed by
        # upstream_token_id. Prevents concurrent async tasks from racing to
        # refresh the same token within a single process. Does not protect
        # against cross-process races in distributed deployments — those are
        # handled by re-reading from storage after refresh failure.
        self._refresh_locks: OrderedDict[str, anyio.Lock] = OrderedDict()

        logger.debug(
            "Initialized OAuth proxy provider with upstream server %s",
            self._upstream_authorization_endpoint,
        )

    # -------------------------------------------------------------------------
    # Scope Management
    # -------------------------------------------------------------------------

    def update_default_scopes(self, scopes: list[str]) -> None:
        """Update the default scopes advertised to clients and used for DCR/CIMD fallback.

        Use this method when the available scope set is determined after provider
        initialization (e.g., after tool registration widens the required scopes).

        This updates all internal state derived from scopes:
        - The default scope string used for DCR client registration fallback
        - The CIMD manager's default scope (if CIMD is enabled)
        - The client registration options default_scopes and valid_scopes (if registration options exist)

        Args:
            scopes: The new list of valid scopes to advertise and use as defaults.
        """
        scopes = list(scopes)
        self._default_scope_str = " ".join(scopes)
        if self._cimd_manager is not None:
            self._cimd_manager.default_scope = self._default_scope_str
        if self.client_registration_options is not None:
            self.client_registration_options.default_scopes = list(scopes)
            self.client_registration_options.valid_scopes = list(scopes)

    # -------------------------------------------------------------------------
    # MCP Path Configuration
    # -------------------------------------------------------------------------

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Set the MCP endpoint path and create JWTIssuer with correct audience.

        This method is called by get_routes() to configure the resource URL
        and create the JWTIssuer. The JWT audience is set to the full resource
        URL (e.g., http://localhost:8000/mcp) to ensure tokens are bound to
        this specific MCP endpoint.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
        """
        super().set_mcp_path(mcp_path)

        # Create JWT issuer with correct audience based on actual MCP path
        # This ensures tokens are bound to the specific resource URL
        self._jwt_issuer = JWTIssuer(
            issuer=str(self.base_url),
            audience=str(self._resource_url),
            signing_key=self._jwt_signing_key,
        )

        logger.debug("Configured OAuth proxy for resource URL: %s", self._resource_url)

    @property
    def jwt_issuer(self) -> JWTIssuer:
        """Get the JWT issuer, ensuring it has been initialized.

        The JWT issuer is created when set_mcp_path() is called (via get_routes()).
        This property ensures a clear error if used before initialization.
        """
        if self._jwt_issuer is None:
            raise RuntimeError(
                "JWT issuer not initialized. Ensure get_routes() is called "
                "before token operations."
            )
        return self._jwt_issuer

    # -------------------------------------------------------------------------
    # Upstream OAuth Client
    # -------------------------------------------------------------------------

    def _create_upstream_oauth_client(self) -> AsyncOAuth2Client:
        """Create an OAuth2 client for communicating with the upstream IdP.

        This is the single point for constructing the client used in token
        exchange, refresh, and other upstream interactions. Subclasses can
        override this to provide alternative authentication methods (e.g.,
        managed-identity client assertions instead of a static client secret).
        """
        return AsyncOAuth2Client(
            client_id=self._upstream_client_id,
            client_secret=(
                self._upstream_client_secret.get_secret_value()
                if self._upstream_client_secret is not None
                else None
            ),
            token_endpoint_auth_method=self._token_endpoint_auth_method,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

    @asynccontextmanager
    async def _upstream_oauth_client(self) -> AsyncIterator[AsyncOAuth2Client]:
        oauth_client = self._create_upstream_oauth_client()
        try:
            yield oauth_client
        finally:
            await oauth_client.aclose()

    def _get_refresh_lock(self, token_id: str) -> anyio.Lock:
        """Get or create a per-token refresh lock, evicting LRU entries when at capacity."""
        lock = self._refresh_locks.get(token_id)
        if lock is None:
            lock = anyio.Lock()
            self._refresh_locks[token_id] = lock
            if len(self._refresh_locks) > _REFRESH_LOCK_CACHE_SIZE:
                self._refresh_locks.popitem(last=False)
        else:
            self._refresh_locks.move_to_end(token_id)
        return lock

    # -------------------------------------------------------------------------
    # PKCE Helper Methods
    # -------------------------------------------------------------------------

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge pair.

        Returns:
            Tuple of (code_verifier, code_challenge) using S256 method
        """
        # Generate code verifier: 43-128 characters from unreserved set
        code_verifier = generate_token(48)

        # Generate code challenge using S256 (SHA256 + base64url)
        challenge_bytes = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = urlsafe_b64encode(challenge_bytes).decode().rstrip("=")

        return code_verifier, code_challenge

    # -------------------------------------------------------------------------
    # Client Registration (Local Implementation)
    # -------------------------------------------------------------------------

    @override
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get client information by ID. This is generally the random ID
        provided to the DCR client during registration, not the upstream client ID.

        For unregistered clients, returns None (which will raise an error in the SDK).
        CIMD clients (URL-based client IDs) are looked up and cached automatically.
        """
        # Load from storage
        client = await self._client_store.get(key=client_id)

        if client is not None:
            if self._allowed_client_redirect_uris is not None:
                client.allowed_redirect_uri_patterns = (
                    self._allowed_client_redirect_uris
                )

            # Refresh CIMD clients using HTTP cache-aware fetcher.
            if self._cimd_manager is not None and client.cimd_document is not None:
                try:
                    refreshed = await self._cimd_manager.get_client(client_id)
                    if refreshed is not None:
                        await self._client_store.put(key=client_id, value=refreshed)
                        return refreshed
                except Exception as e:
                    logger.debug(
                        "CIMD refresh failed for %s, using cached client: %s",
                        client_id,
                        e,
                    )

            return client

        # Client not in storage — try CIMD lookup for URL-based client IDs
        if self._cimd_manager is not None and self._cimd_manager.is_cimd_client_id(
            client_id
        ):
            cimd_client = await self._cimd_manager.get_client(client_id)
            if cimd_client is not None:
                await self._client_store.put(key=client_id, value=cimd_client)
                return cimd_client

        # Some MCP clients (e.g. claude.ai) skip Dynamic Client Registration and
        # send the upstream OAuth App's client_id directly in the /authorize request.
        # Synthesize a client on-the-fly so these clients aren't rejected with 400.
        if client_id == self._upstream_client_id:
            logger.debug(
                "Client %s matched upstream client_id — synthesizing client without DCR",
                client_id,
            )
            synthesized_grant_types = ["authorization_code", "refresh_token"]
            if self._identity_assertion is not None:
                synthesized_grant_types.append(JWT_BEARER_GRANT_TYPE)
            return ProxyDCRClient(
                client_id=client_id,
                client_secret=None,
                redirect_uris=[AnyUrl("http://localhost")],
                grant_types=synthesized_grant_types,
                scope=self._default_scope_str,
                token_endpoint_auth_method="none",
                allowed_redirect_uri_patterns=self._allowed_client_redirect_uris,
                allow_unregistered_redirect_uris=True,
            )

        return None

    @override
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a client locally

        When a client registers, we create a ProxyDCRClient that is more
        forgiving about validating redirect URIs, since the DCR client's
        redirect URI will likely be localhost or unknown to the proxied IDP. The
        proxied IDP only knows about this server's fixed redirect URI.
        """

        # Create a ProxyDCRClient with configured redirect URI validation
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")
        if client_info.redirect_uris:
            for redirect_uri in client_info.redirect_uris:
                if not validate_redirect_uri(
                    redirect_uri=redirect_uri,
                    allowed_patterns=self._allowed_client_redirect_uris,
                ):
                    raise RegistrationError(
                        "invalid_redirect_uri",
                        f"Redirect URI '{redirect_uri}' is not allowed.",
                    )

        redirect_uris = client_info.redirect_uris or [AnyUrl("http://localhost")]

        # When identity assertion is enabled, registered clients are allowed to
        # present the SEP-990 jwt-bearer (ID-JAG) grant. Add it to the client's
        # registered grant types so the token endpoint's grant-type check accepts
        # it — clients that never register (or register without it while identity
        # assertion is disabled) remain unable to use the grant.
        registered_grant_types = list(
            client_info.grant_types or ["authorization_code", "refresh_token"]
        )
        if (
            self._identity_assertion is not None
            and JWT_BEARER_GRANT_TYPE not in registered_grant_types
        ):
            registered_grant_types.append(JWT_BEARER_GRANT_TYPE)

        # We use token_endpoint_auth_method="none" because the proxy handles
        # all upstream authentication. The client_secret must also be None
        # because the SDK requires secrets to be provided if they're set,
        # regardless of auth method.
        proxy_client: ProxyDCRClient = ProxyDCRClient(
            client_id=client_info.client_id,
            client_secret=None,
            redirect_uris=redirect_uris,
            grant_types=registered_grant_types,
            scope=client_info.scope or self._default_scope_str,
            token_endpoint_auth_method="none",
            allowed_redirect_uri_patterns=self._allowed_client_redirect_uris,
            client_name=getattr(client_info, "client_name", None),
        )

        await self._client_store.put(
            key=client_info.client_id,
            value=proxy_client,
        )

        # Log redirect URIs to help users discover what patterns they might need
        if client_info.redirect_uris:
            for uri in client_info.redirect_uris:
                logger.debug(
                    "Client registered with redirect_uri: %s - if restricting redirect URIs, "
                    "ensure this pattern is allowed in allowed_client_redirect_uris",
                    uri,
                )

        logger.debug(
            "Registered client %s with %d redirect URIs",
            client_info.client_id,
            len(proxy_client.redirect_uris) if proxy_client.redirect_uris else 0,
        )

    # -------------------------------------------------------------------------
    # Authorization Flow (Proxy to Upstream)
    # -------------------------------------------------------------------------

    @override
    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Start OAuth transaction and route through consent interstitial.

        Flow:
        1. Validate client's resource matches server's resource URL (security check)
        2. Store transaction with client details and PKCE (if forwarding)
        3. Return local /consent URL; browser visits consent first
        4. Consent handler redirects to upstream IdP if approved/already approved

        If consent is disabled (require_authorization_consent=False or "external"),
        skip the consent screen and redirect directly to the upstream IdP. In
        "remember" mode, still route through /consent so the cookie lookup and
        Sec-Fetch-Site gating can run.
        """
        # Security check: validate client's requested resource matches this server
        # This prevents tokens intended for one server from being used on another
        #
        # Per RFC 8707, clients may include query parameters in resource URLs (e.g.,
        # ChatGPT sends ?kb_name=X). We handle two cases:
        #
        # 1. Server URL has NO query params: normalize both URLs (strip query/fragment)
        #    to allow clients like ChatGPT that add query params to still match.
        #
        # 2. Server URL HAS query params (e.g., multi-tenant ?tenant=X): require exact
        #    match to prevent clients from bypassing tenant isolation by changing params.
        #
        # Claude doesn't send a resource parameter at all, so this check is skipped.
        client_resource = getattr(params, "resource", None)
        if client_resource and self._resource_url:
            server_url = str(self._resource_url)
            client_url = str(client_resource)

            if server_url_has_query(server_url):
                # Server has query params - require exact match for security
                urls_match = client_url.rstrip("/") == server_url.rstrip("/")
            else:
                # Server has no query params - normalize both for comparison
                urls_match = normalize_resource_url(
                    client_url
                ) == normalize_resource_url(server_url)

            if not urls_match:
                logger.warning(
                    "Resource mismatch: client requested %s but server is %s",
                    client_resource,
                    self._resource_url,
                )
                raise AuthorizeError(
                    error="invalid_target",  # type: ignore[arg-type]
                    error_description="Resource does not match this server",
                )

        # Generate transaction ID for this authorization request
        txn_id = secrets.token_urlsafe(32)

        # Generate proxy's own PKCE parameters if forwarding is enabled
        proxy_code_verifier = None
        proxy_code_challenge = None
        if self._forward_pkce and params.code_challenge:
            proxy_code_verifier, proxy_code_challenge = self._generate_pkce_pair()
            logger.debug(
                "Generated proxy PKCE for transaction %s (forwarding client PKCE to upstream)",
                txn_id,
            )

        # Store transaction data for IdP callback processing
        if client.client_id is None:
            raise AuthorizeError(
                error="invalid_client",  # type: ignore[arg-type]  # "invalid_client" is valid OAuth error but not in Literal type  # ty:ignore[invalid-argument-type]
                error_description="Client ID is required",
            )
        transaction = OAuthTransaction(
            txn_id=txn_id,
            client_id=client.client_id,
            client_redirect_uri=str(params.redirect_uri),
            client_state=params.state or "",
            code_challenge=params.code_challenge,
            code_challenge_method=getattr(params, "code_challenge_method", "S256"),
            scopes=params.scopes or [],
            created_at=time.time(),
            resource=getattr(params, "resource", None),
            proxy_code_verifier=proxy_code_verifier,
        )
        await self._transaction_store.put(
            key=txn_id,
            value=transaction,
            ttl=15 * 60,  # Auto-expire after 15 minutes
        )

        # If consent is disabled or handled externally, skip consent screen.
        # "remember" mode still routes through /consent so cookie lookup and
        # Sec-Fetch-Site gating can run.
        if self._require_authorization_consent in (False, "external"):
            upstream_url = self._build_upstream_authorize_url(
                txn_id, transaction.model_dump()
            )
            logger.debug(
                "Starting OAuth transaction %s for client %s, redirecting directly to upstream IdP (consent disabled, PKCE forwarding: %s)",
                txn_id,
                client.client_id,
                "enabled" if proxy_code_challenge else "disabled",
            )
            return upstream_url

        consent_url = f"{str(self.base_url).rstrip('/')}/consent?txn_id={txn_id}"

        logger.debug(
            "Starting OAuth transaction %s for client %s, redirecting to consent page (PKCE forwarding: %s)",
            txn_id,
            client.client_id,
            "enabled" if proxy_code_challenge else "disabled",
        )
        return consent_url

    # -------------------------------------------------------------------------
    # Authorization Code Handling
    # -------------------------------------------------------------------------

    @override
    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load authorization code for validation.

        Look up our client code and return authorization code object
        with PKCE challenge for validation.
        """
        # Look up client code data
        code_model = await self._code_store.get(key=authorization_code)
        if not code_model:
            logger.debug("Authorization code not found: %s", authorization_code)
            return None

        # Check if code expired
        if time.time() > code_model.expires_at:
            logger.debug("Authorization code expired: %s", authorization_code)
            _ = await self._code_store.delete(key=authorization_code)
            return None

        # Verify client ID matches
        if code_model.client_id != client.client_id:
            logger.debug(
                "Authorization code client ID mismatch: %s vs %s",
                code_model.client_id,
                client.client_id,
            )
            return None

        # Create authorization code object with PKCE challenge
        if client.client_id is None:
            raise AuthorizeError(
                error="invalid_client",  # type: ignore[arg-type]  # "invalid_client" is valid OAuth error but not in Literal type  # ty:ignore[invalid-argument-type]
                error_description="Client ID is required",
            )
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            redirect_uri=AnyUrl(url=code_model.redirect_uri),
            redirect_uri_provided_explicitly=True,
            scopes=code_model.scopes,
            expires_at=code_model.expires_at,
            code_challenge=code_model.code_challenge or "",
        )

    @override
    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Exchange authorization code for FastMCP-issued tokens.

        Implements the token factory pattern:
        1. Retrieves upstream tokens from stored authorization code
        2. Extracts user identity from upstream token
        3. Encrypts and stores upstream tokens
        4. Issues FastMCP-signed JWT tokens
        5. Returns FastMCP tokens (NOT upstream tokens)

        PKCE validation is handled by the MCP framework before this method is called.
        """
        # Look up stored code data
        code_model = await self._code_store.get(key=authorization_code.code)
        if not code_model:
            logger.error(
                "Authorization code not found in client codes: %s",
                authorization_code.code,
            )
            raise TokenError("invalid_grant", "Authorization code not found")

        # Get stored upstream tokens
        idp_tokens = code_model.idp_tokens

        # Use IdP-granted scopes when available (RFC 6749 §5.1: the IdP MUST
        # include a scope parameter when the granted scope differs from the
        # requested scope).  Fall back to requested scopes only when the IdP
        # omits scope, meaning it granted exactly what was requested.
        granted_scopes: list[str] = (
            parse_scopes(idp_tokens["scope"]) or []
            if "scope" in idp_tokens
            else list(authorization_code.scopes)
        )
        # Translate IdP-wire scopes into the client-facing form before they
        # propagate to storage, the FastMCP JWT, and the response body. Default
        # implementation is identity; AzureProvider overrides this to strip the
        # identifier_uri prefix Azure echoes back on custom API scopes.
        granted_scopes = self._translate_scopes_from_idp(granted_scopes)

        # Clean up client code (one-time use)
        await self._code_store.delete(key=authorization_code.code)

        # Generate IDs for token storage
        upstream_token_id = secrets.token_urlsafe(32)
        access_jti = secrets.token_urlsafe(32)
        refresh_jti = (
            secrets.token_urlsafe(32) if idp_tokens.get("refresh_token") else None
        )

        # Calculate token expiry times
        # If upstream provides expires_in, use it. Otherwise use fallback based on:
        # - User-provided fallback if set
        # - 1 hour if refresh token available (can refresh when expired)
        # - 1 year if no refresh token (likely API-key-style token like GitHub OAuth Apps)
        if "expires_in" in idp_tokens:
            expires_in = int(idp_tokens["expires_in"])
            logger.debug(
                "Access token TTL: %d seconds (from IdP expires_in)", expires_in
            )
        elif self._fallback_access_token_expiry_seconds is not None:
            expires_in = self._fallback_access_token_expiry_seconds
            logger.debug(
                "Access token TTL: %d seconds (using configured fallback)", expires_in
            )
        elif idp_tokens.get("refresh_token"):
            expires_in = DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS
            logger.debug(
                "Access token TTL: %d seconds (default, has refresh token)", expires_in
            )
        else:
            expires_in = DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS
            logger.debug(
                "Access token TTL: %d seconds (default, no refresh token)", expires_in
            )

        # The FastMCP-issued access token is a reference into our storage and may
        # outlive the upstream access token, because transparent refresh keeps the
        # upstream token fresh on each request. `expires_in` stays the upstream
        # lifetime; this drives only the FastMCP JWT, its JTI mapping, and the
        # response's expires_in. Extending past the upstream lifetime is only safe
        # when we can refresh: without an upstream refresh token there is no way to
        # renew the access token, so the FastMCP token must not claim to outlive the
        # upstream token it points at.
        fastmcp_access_expires_in = expires_in
        if self._fastmcp_access_token_expiry_seconds is not None:
            fastmcp_access_expires_in = self._fastmcp_access_token_expiry_seconds
            if not idp_tokens.get("refresh_token"):
                fastmcp_access_expires_in = min(fastmcp_access_expires_in, expires_in)

        # Calculate refresh token expiry if provided by upstream
        # Some providers include refresh_expires_in, some don't
        refresh_expires_in = None
        refresh_token_expires_at = None
        if idp_tokens.get("refresh_token"):
            if "refresh_expires_in" in idp_tokens and int(
                idp_tokens["refresh_expires_in"]
            ):
                refresh_expires_in = int(idp_tokens["refresh_expires_in"])
                refresh_token_expires_at = time.time() + refresh_expires_in
                logger.debug(
                    "Upstream refresh token expires in %d seconds", refresh_expires_in
                )
            else:
                # Upstream didn't specify; use configured fallback (default 1 year).
                # The FastMCP refresh JWT is just a signed pointer — if the real
                # upstream refresh has expired or been revoked, the next refresh
                # call to upstream will fail and the client re-auths.
                refresh_expires_in = self._fallback_refresh_token_expiry_seconds
                refresh_token_expires_at = time.time() + refresh_expires_in
                logger.debug(
                    "Upstream refresh token expiry unknown, using fallback %d seconds",
                    refresh_expires_in,
                )

        # Encrypt and store upstream tokens
        upstream_token_set = UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token=idp_tokens["access_token"],
            refresh_token=idp_tokens["refresh_token"]
            if idp_tokens.get("refresh_token")
            else None,
            refresh_token_expires_at=refresh_token_expires_at,
            expires_at=time.time() + expires_in,
            token_type=idp_tokens.get("token_type", "Bearer"),
            scope=" ".join(granted_scopes),
            client_id=client.client_id or "",
            created_at=time.time(),
            raw_token_data=idp_tokens,
        )
        await self._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=max(
                refresh_expires_in or 0, expires_in, fastmcp_access_expires_in, 1
            ),  # Keep until longest-lived token expires (min 1s for safety)
        )
        logger.debug("Stored encrypted upstream tokens (jti=%s)", access_jti[:8])

        # Extract upstream claims to embed in FastMCP JWT (if subclass implements)
        upstream_claims = await self._extract_upstream_claims(idp_tokens)

        # Issue minimal FastMCP access token (just a reference via JTI)
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        fastmcp_access_token = self.jwt_issuer.issue_access_token(
            client_id=client.client_id,
            scopes=granted_scopes,
            jti=access_jti,
            expires_in=fastmcp_access_expires_in,
            upstream_claims=upstream_claims,
        )

        # Issue minimal FastMCP refresh token if upstream provided one
        # Use upstream refresh token expiry to align lifetimes
        fastmcp_refresh_token = None
        if refresh_jti and refresh_expires_in:
            fastmcp_refresh_token = self.jwt_issuer.issue_refresh_token(
                client_id=client.client_id,
                scopes=granted_scopes,
                jti=refresh_jti,
                expires_in=refresh_expires_in,
                upstream_claims=upstream_claims,
            )

        # Store JTI mappings
        await self._jti_mapping_store.put(
            key=access_jti,
            value=JTIMapping(
                jti=access_jti,
                upstream_token_id=upstream_token_id,
                created_at=time.time(),
            ),
            ttl=fastmcp_access_expires_in,  # Auto-expire with FastMCP access token
        )
        if refresh_jti:
            await self._jti_mapping_store.put(
                key=refresh_jti,
                value=JTIMapping(
                    jti=refresh_jti,
                    upstream_token_id=upstream_token_id,
                    created_at=time.time(),
                ),
                ttl=refresh_expires_in or self._fallback_refresh_token_expiry_seconds,
            )

        # Store refresh token metadata (keyed by hash for security)
        if fastmcp_refresh_token and refresh_expires_in:
            await self._refresh_token_store.put(
                key=_hash_token(fastmcp_refresh_token),
                value=RefreshTokenMetadata(
                    client_id=client.client_id,
                    scopes=granted_scopes,
                    expires_at=int(time.time()) + refresh_expires_in,
                    created_at=time.time(),
                ),
                ttl=refresh_expires_in,
            )

        logger.debug(
            "Issued FastMCP tokens for client=%s (access_jti=%s, refresh_jti=%s)",
            client.client_id,
            access_jti[:8],
            refresh_jti[:8] if refresh_jti else "none",
        )

        # Return FastMCP-issued tokens (NOT upstream tokens!)
        return OAuthToken(
            access_token=fastmcp_access_token,
            token_type="Bearer",
            expires_in=fastmcp_access_expires_in,
            refresh_token=fastmcp_refresh_token,
            scope=" ".join(granted_scopes),
        )

    # -------------------------------------------------------------------------
    # Identity Assertion Flow (SEP-990 ID-JAG)
    # -------------------------------------------------------------------------

    async def exchange_identity_assertion(
        self,
        client: OAuthClientInformationFull,
        params: IdentityAssertionParams,
    ) -> OAuthToken:
        """Exchange a SEP-990 ID-JAG for a short-lived FastMCP access token.

        Validates the ID-JAG against the configured trusted issuers (signature,
        `iss`, `aud`, `exp`, `typ`, `sub`, and `jti` replay), then mints a
        self-contained FastMCP access token carrying the asserted subject. No
        refresh token is issued — the client re-exchanges a fresh assertion.

        Raises:
            TokenError: ``invalid_grant`` if the assertion is rejected, or
                ``unsupported_grant_type`` if identity assertion is not configured.
        """
        if (
            self._identity_assertion is None
            or self._identity_assertion_validator is None
        ):
            raise TokenError(
                "unsupported_grant_type",
                "The JWT bearer grant is not supported by this authorization server",
            )

        # RFC 8707: when the request names a resource, it must be this server —
        # the same invariant (and the same skip-when-unconfigured behavior)
        # authorize() enforces for authorization requests.
        if params.resource and self._resource_url:
            server_url = str(self._resource_url)
            client_url = str(params.resource)
            if server_url_has_query(server_url):
                # Server has query params - require exact match for security
                resource_matches = client_url.rstrip("/") == server_url.rstrip("/")
            else:
                resource_matches = normalize_resource_url(
                    client_url
                ) == normalize_resource_url(server_url)
            if not resource_matches:
                logger.warning(
                    "ID-JAG resource mismatch: client requested %s but server is %s",
                    client_url,
                    self._resource_url,
                )
                raise TokenError(
                    "invalid_target", "Resource does not match this server"
                )

        # SEP-990: the assertion's signed client_id and resource claims (checked
        # against the authenticated client and this server) bind the assertion
        # before its jti is recorded as consumed — passed into validate() itself
        # so that binding happens ahead of replay-tracking, not after.
        try:
            claims = await self._identity_assertion_validator.validate(
                params.assertion,
                client_id=client.client_id or "",
                resource_url=str(self._resource_url) if self._resource_url else None,
            )
        except IdentityAssertionError as e:
            # Log detail server-side; return a generic error to the client so we
            # do not leak which validation step failed.
            logger.info("ID-JAG rejected: %s", e)
            raise TokenError("invalid_grant", "Invalid identity assertion") from e

        subject = str(claims["sub"])

        # Granted scopes are authoritative from the signed assertion (or, when the
        # assertion omits them, from explicit server policy). The client-supplied
        # request `scope` (`params.scopes`) is NOT covered by the signed assertion,
        # so it may only NARROW the granted set — never widen it. A client cannot
        # obtain a scope the assertion did not grant by asking for it at the token
        # endpoint (e.g. an assertion granting `read` requesting `admin` gets nothing
        # extra).
        authoritative_scopes = _assertion_granted_scopes(claims)
        if not authoritative_scopes:
            authoritative_scopes = list(self._identity_assertion.required_scopes or [])

        # Configured mandatory scopes that the assertion actually grants must always
        # ride on the issued token — the client request may only narrow the
        # remaining, optional scopes. Otherwise a request like `scope=read` could
        # silently drop a required `admin` scope the assertion authorized.
        required = set(self._identity_assertion.required_scopes or [])

        if params.scopes:
            requested = set(params.scopes)
            granted_scopes = [
                s for s in authoritative_scopes if s in required or s in requested
            ]
        else:
            granted_scopes = list(authoritative_scopes)

        expires_in = self._identity_assertion.access_token_expiry_seconds
        access_jti = secrets.token_urlsafe(32)

        access_token = self.jwt_issuer.issue_access_token(
            client_id=client.client_id or "",
            scopes=granted_scopes,
            jti=access_jti,
            expires_in=expires_in,
            subject=subject,
            extra_claims={
                "fastmcp_grant": _ID_JAG_GRANT_MARKER,
                "assertion_iss": str(claims.get("iss")),
            },
        )

        logger.debug(
            "Issued ID-JAG access token for subject=%s client=%s jti=%s",
            subject,
            client.client_id,
            access_jti[:8],
        )

        # SEP-990: no refresh token — the IdP controls session lifetime.
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(granted_scopes),
        )

    # -------------------------------------------------------------------------
    # Refresh Token Flow
    # -------------------------------------------------------------------------

    def _prepare_scopes_for_token_exchange(self, scopes: list[str]) -> list[str]:
        """Prepare scopes for initial token exchange (auth code -> tokens).

        Override this method to provide scopes during the authorization
        code exchange. Some providers (like Azure) require scopes to be sent.

        Args:
            scopes: Scopes from the authorization request

        Returns:
            List of scopes to send, or empty list to omit scope parameter
        """
        return scopes

    def _translate_scopes_from_idp(self, scopes: list[str]) -> list[str]:
        """Translate IdP-returned scopes into the client-facing form.

        Some IdPs (notably Azure) require scopes on the wire in a form that
        differs from the form MCP clients use — e.g. Azure requires custom API
        scopes to be prefixed with the application's identifier URI
        (``api://{client_id}/read``) on outbound requests and echoes that same
        prefixed form back in the token response. MCP clients, however, request
        and recognize the short form (``read``) advertised via
        ``/.well-known/oauth-authorization-server``.

        This hook is the inverse of provider-specific outbound translation
        (e.g. :meth:`AzureProvider._prefix_scopes_for_azure`). The default
        implementation returns the scopes unchanged, which is correct for any
        provider where IdP-wire scopes already match the client-facing form.

        It is called once on each ``scope`` value parsed out of an IdP token
        response, before that value is stored, embedded in the FastMCP JWT
        scope claim, or echoed to the client. Applying the translation here
        keeps storage, the issued JWT, and the response body consistent.

        Args:
            scopes: Scopes parsed from the IdP token response's ``scope`` field.

        Returns:
            List of scopes in the form clients expect.
        """
        return scopes

    def _prepare_scopes_for_upstream_refresh(self, scopes: list[str]) -> list[str]:
        """Prepare scopes for upstream token refresh request.

        Override this method to transform scopes before sending to upstream provider.
        For example, Azure needs to prefix scopes and add additional Graph scopes.

        The scopes parameter represents what should be stored in the RefreshToken.
        This method returns what should be sent to the upstream provider.

        Args:
            scopes: Base scopes that will be stored in RefreshToken

        Returns:
            Scopes to send to upstream provider (may be transformed/augmented)
        """
        return scopes

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract upstream claims to embed in FastMCP JWT.

        Override this method to decode upstream tokens, call userinfo endpoints,
        or otherwise extract claims that should be embedded in the FastMCP JWT
        issued to MCP clients. This enables gateways to inspect upstream identity
        information by decoding the JWT without server-side storage lookups.

        Args:
            idp_tokens: Full token response from upstream provider. Contains
                access_token, and for OIDC providers may include id_token,
                refresh_token, and other response fields.

        Returns:
            Dict of claims to embed in JWT under the "upstream_claims" key,
            or None to not embed any upstream claims.

        Example:
            For Azure/Entra ID, you might decode the access_token JWT and
            extract claims like sub, oid, name, preferred_username, email,
            roles, and groups.
        """
        _ = idp_tokens
        return None

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load refresh token metadata from distributed storage.

        Looks up by token hash and reconstructs the RefreshToken object.
        Validates that the token belongs to the requesting client.
        """
        token_hash = _hash_token(refresh_token)
        metadata = await self._refresh_token_store.get(key=token_hash)
        if not metadata:
            logger.warning(
                "Refresh token not found for client=%s (token_hash=%s); it was "
                "already rotated, expired, or revoked. Rejecting with invalid_grant, "
                "which forces the client to re-authenticate.",
                client.client_id,
                token_hash[:8],
            )
            return None
        # Verify token belongs to this client (prevents cross-client token usage)
        if metadata.client_id != client.client_id:
            logger.warning(
                "Refresh token client_id mismatch: expected %s, got %s",
                client.client_id,
                metadata.client_id,
            )
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=metadata.client_id,
            scopes=metadata.scopes,
            expires_at=metadata.expires_at,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange FastMCP refresh token for new FastMCP access token.

        Implements two-tier refresh:
        1. Verify FastMCP refresh token
        2. Look up upstream token via JTI mapping
        3. Refresh upstream token with upstream provider
        4. Update stored upstream token
        5. Issue new FastMCP access token
        6. Keep same FastMCP refresh token (unless upstream rotates)
        """
        # Verify FastMCP refresh token
        try:
            refresh_payload = self.jwt_issuer.verify_token(
                refresh_token.token, expected_token_use="refresh"
            )
            refresh_jti = refresh_payload["jti"]
        except Exception as e:
            logger.debug("FastMCP refresh token validation failed: %s", e)
            raise TokenError("invalid_grant", "Invalid refresh token") from e

        # Look up upstream token via JTI mapping
        jti_mapping = await self._jti_mapping_store.get(key=refresh_jti)
        if not jti_mapping:
            logger.error("JTI mapping not found for refresh token: %s", refresh_jti[:8])
            raise TokenError("invalid_grant", "Refresh token mapping not found")

        upstream_token_set = await self._upstream_token_store.get(
            key=jti_mapping.upstream_token_id
        )
        if not upstream_token_set:
            logger.error(
                "Upstream token set not found: %s", jti_mapping.upstream_token_id[:8]
            )
            raise TokenError("invalid_grant", "Upstream token not found")

        # Decrypt upstream refresh token
        if not upstream_token_set.refresh_token:
            logger.error("No upstream refresh token available")
            raise TokenError("invalid_grant", "Refresh not supported for this token")

        # Allow child classes to transform scopes before sending to upstream
        # This enables provider-specific scope formatting (e.g., Azure prefixing)
        # while keeping original scopes in storage
        upstream_scopes = self._prepare_scopes_for_upstream_refresh(scopes)

        try:
            logger.debug("Refreshing upstream token (jti=%s)", refresh_jti[:8])
            async with self._upstream_oauth_client() as oauth_client:
                token_response: dict[str, Any] = await oauth_client.refresh_token(
                    url=self._upstream_token_endpoint,
                    refresh_token=upstream_token_set.refresh_token,
                    scope=" ".join(upstream_scopes) if upstream_scopes else None,
                    **self._extra_token_params,
                )
            logger.debug("Successfully refreshed upstream token")
        except Exception as e:
            logger.error("Upstream token refresh failed: %s", e)
            raise TokenError("invalid_grant", f"Upstream refresh failed: {e}") from e

        # Update stored upstream token
        # In refresh flow, we know there's a refresh token, so default to 1 hour
        # (user override still applies if set)
        if "expires_in" in token_response:
            new_expires_in = int(token_response["expires_in"])
            logger.debug(
                "Refreshed access token TTL: %d seconds (from IdP expires_in)",
                new_expires_in,
            )
        elif self._fallback_access_token_expiry_seconds is not None:
            new_expires_in = self._fallback_access_token_expiry_seconds
            logger.debug(
                "Refreshed access token TTL: %d seconds (using configured fallback)",
                new_expires_in,
            )
        else:
            new_expires_in = DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS
            logger.debug(
                "Refreshed access token TTL: %d seconds (default)", new_expires_in
            )
        upstream_token_set.access_token = token_response["access_token"]
        upstream_token_set.expires_at = time.time() + new_expires_in

        # See exchange_authorization_code: the FastMCP access token may outlive the
        # upstream one. `new_expires_in` stays the upstream lifetime (drives
        # upstream_token_set.expires_at and transparent refresh).
        fastmcp_access_expires_in = (
            self._fastmcp_access_token_expiry_seconds
            if self._fastmcp_access_token_expiry_seconds is not None
            else new_expires_in
        )

        # Prefer IdP-granted scopes from refresh response (RFC 6749 §5.1)
        refreshed_scopes: list[str] = (
            parse_scopes(token_response["scope"]) or []
            if "scope" in token_response
            else scopes
        )
        # Same translation as in exchange_authorization_code — keep storage,
        # the rotated FastMCP JWT, and the response body all in client-facing
        # scope form.
        refreshed_scopes = self._translate_scopes_from_idp(refreshed_scopes)
        upstream_token_set.scope = " ".join(refreshed_scopes)

        # Handle upstream refresh token rotation and expiry
        new_refresh_expires_in = None
        if new_upstream_refresh := token_response.get("refresh_token"):
            if new_upstream_refresh != upstream_token_set.refresh_token:
                upstream_token_set.refresh_token = new_upstream_refresh
                logger.debug("Upstream refresh token rotated")

            # Update refresh token expiry if provided
            if "refresh_expires_in" in token_response and int(
                token_response["refresh_expires_in"]
            ):
                new_refresh_expires_in = int(token_response["refresh_expires_in"])
                upstream_token_set.refresh_token_expires_at = (
                    time.time() + new_refresh_expires_in
                )
                logger.debug(
                    "Upstream refresh token expires in %d seconds",
                    new_refresh_expires_in,
                )
            elif upstream_token_set.refresh_token_expires_at:
                # Keep existing expiry if upstream doesn't provide new one
                new_refresh_expires_in = int(
                    upstream_token_set.refresh_token_expires_at - time.time()
                )
            else:
                # Upstream rotated the refresh token but gave no expiry; use fallback
                new_refresh_expires_in = self._fallback_refresh_token_expiry_seconds
                upstream_token_set.refresh_token_expires_at = (
                    time.time() + new_refresh_expires_in
                )

        upstream_token_set.raw_token_data = {
            **upstream_token_set.raw_token_data,
            **token_response,
        }
        # Calculate refresh TTL for storage
        refresh_ttl = new_refresh_expires_in or (
            int(upstream_token_set.refresh_token_expires_at - time.time())
            if upstream_token_set.refresh_token_expires_at
            else self._fallback_refresh_token_expiry_seconds
        )
        # Guard against past expiry (e.g. existing upstream refresh has already
        # ticked past its recorded expiry). Storage TTL must be > 0.
        refresh_ttl = max(refresh_ttl, 1)
        await self._upstream_token_store.put(
            key=upstream_token_set.upstream_token_id,
            value=upstream_token_set,
            ttl=max(
                refresh_ttl, new_expires_in, fastmcp_access_expires_in, 1
            ),  # Keep until longest-lived token expires (min 1s for safety)
        )

        # Re-extract upstream claims from refreshed token response
        upstream_claims = await self._extract_upstream_claims(
            upstream_token_set.raw_token_data
        )

        # Issue new minimal FastMCP access token (just a reference via JTI)
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        new_access_jti = secrets.token_urlsafe(32)
        new_fastmcp_access = self.jwt_issuer.issue_access_token(
            client_id=client.client_id,
            scopes=refreshed_scopes,
            jti=new_access_jti,
            expires_in=fastmcp_access_expires_in,
            upstream_claims=upstream_claims,
        )

        # Store new access token JTI mapping
        await self._jti_mapping_store.put(
            key=new_access_jti,
            value=JTIMapping(
                jti=new_access_jti,
                upstream_token_id=upstream_token_set.upstream_token_id,
                created_at=time.time(),
            ),
            ttl=fastmcp_access_expires_in,  # Auto-expire with FastMCP access token
        )

        # Issue NEW minimal FastMCP refresh token (rotation for security).
        # Use refresh_ttl so the JWT exp claim matches the storage TTL — otherwise
        # providers that don't rotate the refresh token (e.g. Cognito) would get
        # a JWT with a hardcoded short exp even when the upstream refresh is
        # still valid for much longer.
        new_refresh_jti = secrets.token_urlsafe(32)
        new_fastmcp_refresh = self.jwt_issuer.issue_refresh_token(
            client_id=client.client_id,
            scopes=refreshed_scopes,
            jti=new_refresh_jti,
            expires_in=refresh_ttl,
            upstream_claims=upstream_claims,
        )

        # Store new refresh token JTI mapping with aligned expiry
        # (reuse refresh_ttl calculated above for upstream token store)
        await self._jti_mapping_store.put(
            key=new_refresh_jti,
            value=JTIMapping(
                jti=new_refresh_jti,
                upstream_token_id=upstream_token_set.upstream_token_id,
                created_at=time.time(),
            ),
            ttl=refresh_ttl,  # Align with upstream refresh token expiry
        )

        # Invalidate old refresh token (refresh token rotation - enforces one-time use)
        await self._jti_mapping_store.delete(key=refresh_jti)
        logger.debug(
            "Rotated refresh token (old JTI invalidated - one-time use enforced)"
        )

        # Store new refresh token metadata (keyed by hash)
        await self._refresh_token_store.put(
            key=_hash_token(new_fastmcp_refresh),
            value=RefreshTokenMetadata(
                client_id=client.client_id,
                scopes=refreshed_scopes,
                expires_at=int(time.time()) + refresh_ttl,
                created_at=time.time(),
            ),
            ttl=refresh_ttl,
        )

        # Delete old refresh token (by hash)
        await self._refresh_token_store.delete(key=_hash_token(refresh_token.token))

        logger.info(
            "Issued new FastMCP tokens (rotated refresh) for client=%s (access_jti=%s, refresh_jti=%s)",
            client.client_id,
            new_access_jti[:8],
            new_refresh_jti[:8],
        )

        # Return new FastMCP tokens (both access AND refresh are new)
        return OAuthToken(
            access_token=new_fastmcp_access,
            token_type="Bearer",
            expires_in=fastmcp_access_expires_in,
            refresh_token=new_fastmcp_refresh,  # NEW refresh token (rotated)
            scope=" ".join(refreshed_scopes),
        )

    # -------------------------------------------------------------------------
    # Token Validation
    # -------------------------------------------------------------------------

    def _get_verification_token(
        self, upstream_token_set: UpstreamTokenSet
    ) -> str | None:
        """Get the token string to pass to the token verifier.

        Returns the upstream access token by default. Subclasses can override
        to verify a different token (e.g., the OIDC id_token for providers
        that issue opaque access tokens).
        """
        return upstream_token_set.access_token

    def _uses_alternate_verification(self) -> bool:
        """Whether this provider verifies a different token than the access token.

        When True, ``load_access_token`` patches the validated result with
        the upstream access token, scopes, and expiry so that the returned
        ``AccessToken`` reflects the access token rather than the
        verification token.

        The default implementation compares token values, but subclasses
        should override this to use an intent-based flag so the patch is
        applied even when the verification token and access token happen to
        carry the same value (e.g., some OIDC providers issue identical
        JWTs for both).
        """
        return False

    async def _try_transparent_refresh(
        self,
        upstream_token_set: UpstreamTokenSet,
    ) -> UpstreamTokenSet:
        """Refresh the upstream token transparently and update storage.

        Called during load_access_token when the upstream token has expired
        but a refresh token is available. This avoids returning a 401 that
        would force the client into a full re-authentication flow.

        Mutates and returns the upstream_token_set with refreshed token data.
        Raises on failure (caller should catch and fall through to None).
        """
        scopes = upstream_token_set.scope.split() if upstream_token_set.scope else []
        upstream_scopes = self._prepare_scopes_for_upstream_refresh(scopes)

        async with self._upstream_oauth_client() as oauth_client:
            token_response: dict[str, Any] = await oauth_client.refresh_token(
                url=self._upstream_token_endpoint,
                refresh_token=upstream_token_set.refresh_token,
                scope=" ".join(upstream_scopes) if upstream_scopes else None,
                **self._extra_token_params,
            )
        logger.debug(
            "Transparent upstream refresh succeeded (token_id=%s)",
            upstream_token_set.upstream_token_id[:8],
        )

        # Calculate new expiry
        if "expires_in" in token_response:
            new_expires_in = int(token_response["expires_in"])
        elif self._fallback_access_token_expiry_seconds is not None:
            new_expires_in = self._fallback_access_token_expiry_seconds
        else:
            new_expires_in = DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS

        upstream_token_set.access_token = token_response["access_token"]
        upstream_token_set.expires_at = time.time() + new_expires_in
        refreshed_scopes = (
            parse_scopes(token_response["scope"]) or []
            if "scope" in token_response
            else scopes
        )
        upstream_token_set.scope = " ".join(
            self._translate_scopes_from_idp(refreshed_scopes)
        )

        # Handle upstream refresh token rotation
        new_refresh_expires_in = None
        if new_upstream_refresh := token_response.get("refresh_token"):
            if new_upstream_refresh != upstream_token_set.refresh_token:
                upstream_token_set.refresh_token = new_upstream_refresh
            if "refresh_expires_in" in token_response and int(
                token_response["refresh_expires_in"]
            ):
                new_refresh_expires_in = int(token_response["refresh_expires_in"])
                upstream_token_set.refresh_token_expires_at = (
                    time.time() + new_refresh_expires_in
                )
            elif upstream_token_set.refresh_token_expires_at:
                new_refresh_expires_in = int(
                    upstream_token_set.refresh_token_expires_at - time.time()
                )
            else:
                new_refresh_expires_in = self._fallback_refresh_token_expiry_seconds
                upstream_token_set.refresh_token_expires_at = (
                    time.time() + new_refresh_expires_in
                )

        upstream_token_set.raw_token_data = {
            **upstream_token_set.raw_token_data,
            **token_response,
        }

        refresh_ttl = new_refresh_expires_in or (
            int(upstream_token_set.refresh_token_expires_at - time.time())
            if upstream_token_set.refresh_token_expires_at
            else self._fallback_refresh_token_expiry_seconds
        )
        await self._upstream_token_store.put(
            key=upstream_token_set.upstream_token_id,
            value=upstream_token_set,
            # Include the configured FastMCP lifetime so the upstream token never
            # expires before an extended access JTI mapping that still points at it.
            ttl=max(
                refresh_ttl,
                new_expires_in,
                self._fastmcp_access_token_expiry_seconds or 0,
                1,
            ),
        )

        return upstream_token_set

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]  # ty:ignore[invalid-method-override]
        """Validate FastMCP JWT by swapping for upstream token.

        This implements the token swap pattern:
        1. Verify FastMCP JWT signature (proves it's our token)
        2. Look up upstream token via JTI mapping
        3. Decrypt upstream token
        4. Validate upstream token with provider (GitHub API, JWT validation, etc.)
        5. If upstream validation fails, attempt transparent refresh
        6. Return upstream validation result

        The FastMCP JWT is a reference token - all authorization data comes
        from validating the upstream token via the TokenVerifier.
        """
        try:
            # 1. Verify FastMCP JWT signature and claims
            payload = self.jwt_issuer.verify_token(token)
            jti = payload["jti"]
            upstream_claims = payload.get("upstream_claims")

            # SEP-990: ID-JAG tokens are self-contained — the asserted subject
            # is carried in the token itself, and there is no upstream token to
            # swap for. Return directly from the verified claims, unless the
            # token was revoked (tracked by jti until natural expiry).
            if payload.get("fastmcp_grant") == _ID_JAG_GRANT_MARKER:
                if jti in self._revoked_id_jag_jtis:
                    logger.info("Rejected revoked ID-JAG access token jti=%s", jti[:16])
                    return None
                scope = payload.get("scope", "")
                return AccessToken(
                    token=token,
                    client_id=str(payload.get("client_id", "")),
                    scopes=scope.split() if scope else [],
                    expires_at=int(payload["exp"]) if payload.get("exp") else None,
                    subject=str(payload["sub"]) if payload.get("sub") else None,
                    claims=payload,
                )

            # 2. Look up upstream token via JTI mapping
            jti_mapping = await self._jti_mapping_store.get(key=jti)
            if not jti_mapping:
                logger.info(
                    "JTI mapping not found (token may have expired): jti=%s...",
                    jti[:16],
                )
                return None

            upstream_token_set = await self._upstream_token_store.get(
                key=jti_mapping.upstream_token_id
            )
            if not upstream_token_set:
                logger.debug(
                    "Upstream token not found: %s", jti_mapping.upstream_token_id
                )
                return None

            # 3. Validate with upstream provider (delegated to TokenVerifier)
            # This calls the real token validator (GitHub API, JWKS, etc.)
            verification_token = self._get_verification_token(upstream_token_set)
            if verification_token is None:
                logger.debug("No verification token available")
                return None
            validated = await self._token_validator.verify_token(verification_token)

            # 4. Determine if refresh is needed. Two cases:
            #    a) Validation failed and token is expired/within threshold
            #    b) Validation passed but token is within threshold (proactive)
            needs_refresh = upstream_token_set.refresh_token and (
                upstream_token_set.expires_at
                <= time.time() + self._token_expiry_threshold_seconds
            )
            should_refresh = needs_refresh and (
                not validated
                or (validated and self._token_expiry_threshold_seconds > 0)
            )

            if should_refresh:
                try:
                    token_id = upstream_token_set.upstream_token_id

                    # Advisory lock prevents concurrent requests from racing
                    # to refresh the same upstream token.
                    lock = self._get_refresh_lock(token_id)

                    async with lock:
                        # Re-read from storage — another task may have
                        # already refreshed while we waited for the lock.
                        upstream_token_set = (
                            await self._upstream_token_store.get(key=token_id)
                            or upstream_token_set
                        )

                        verification_token = self._get_verification_token(
                            upstream_token_set
                        )
                        if verification_token is not None:
                            validated = await self._token_validator.verify_token(
                                verification_token
                            )

                        # Only refresh if the (possibly reloaded) token is
                        # still within threshold — a freshly-refreshed token
                        # doesn't need another refresh.
                        if (
                            upstream_token_set.expires_at
                            <= time.time() + self._token_expiry_threshold_seconds
                        ):
                            upstream_token_set = await self._try_transparent_refresh(
                                upstream_token_set
                            )
                            verification_token = self._get_verification_token(
                                upstream_token_set
                            )
                            if verification_token is not None:
                                validated = await self._token_validator.verify_token(
                                    verification_token
                                )
                except Exception as e:
                    logger.debug("Transparent upstream refresh failed: %s", e)
                    # In a distributed deployment, another worker may have
                    # already refreshed and rotated the token, causing our
                    # stale refresh token to fail. Re-read and re-validate.
                    try:
                        reloaded = await self._upstream_token_store.get(
                            key=upstream_token_set.upstream_token_id
                        )
                        if reloaded:
                            verification_token = self._get_verification_token(reloaded)
                            if verification_token is not None:
                                validated = await self._token_validator.verify_token(
                                    verification_token
                                )
                                if validated:
                                    upstream_token_set = reloaded
                    except Exception:
                        pass

            if not validated:
                logger.debug("Upstream token validation failed")
                return None

            # When alternate verification is in use (e.g., id_token
            # verification in OIDCProxy), ensure the returned AccessToken
            # carries the upstream access token and its scopes, not the
            # verification token's values.  We use an intent-based check
            # rather than value equality because some IdPs issue identical
            # JWTs for both access_token and id_token, which would cause
            # the scope patch to be skipped even though it's needed.
            if self._uses_alternate_verification():
                validated = validated.model_copy(
                    update={
                        "token": upstream_token_set.access_token,
                        "scopes": upstream_token_set.scope.split()
                        if upstream_token_set.scope
                        else validated.scopes,
                        "expires_at": int(upstream_token_set.expires_at),
                    }
                )

            # Propagate upstream claims from the verified FastMCP JWT into the
            # final AccessToken object. This allows subclasses to access custom
            # identity data extracted during the initial authorization flow.
            # We perform a model copy to avoid mutating a potentially cached
            # reference shared across concurrent requests.
            if validated and upstream_claims:
                validated = validated.model_copy(deep=True)
                if validated.claims is None:
                    validated.claims = {}
                validated.claims["upstream_claims"] = upstream_claims

            logger.debug(
                "Token swap successful for JTI=%s (upstream validated)", jti[:8]
            )
            return validated

        except Exception as e:
            logger.debug("Token swap validation failed: %s", e)
            return None

    # -------------------------------------------------------------------------
    # Token Revocation
    # -------------------------------------------------------------------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke token locally and with upstream server if supported.

        For refresh tokens, removes from local storage by hash.
        For all tokens, attempts upstream revocation if endpoint is configured.
        Access token JTI mappings expire via TTL.
        """
        # For refresh tokens, delete from local storage by hash
        if isinstance(token, RefreshToken):
            await self._refresh_token_store.delete(key=_hash_token(token.token))

        # ID-JAG access tokens are self-contained and never known upstream, so
        # upstream revocation cannot invalidate them. Track the jti locally so
        # load_access_token() rejects the token for its remaining lifetime.
        try:
            payload = self.jwt_issuer.verify_token(token.token)
        except (JoseError, ValueError, KeyError):
            # Not a (valid) FastMCP-issued JWT — nothing to track locally.
            payload = None
        if payload is not None and payload.get("fastmcp_grant") == _ID_JAG_GRANT_MARKER:
            now = time.time()
            self._revoked_id_jag_jtis = {
                jti: exp for jti, exp in self._revoked_id_jag_jtis.items() if exp > now
            }
            exp = payload.get("exp")
            jti = payload.get("jti")
            if isinstance(jti, str) and jti:
                self._revoked_id_jag_jtis[jti] = (
                    float(exp) if isinstance(exp, (int, float)) else now + 3600
                )
                logger.debug("Revoked ID-JAG access token jti=%s", jti[:16])
            return

        # Attempt upstream revocation if endpoint is configured
        if self._upstream_revocation_endpoint:
            try:
                async with httpx2.AsyncClient(
                    timeout=HTTP_TIMEOUT_SECONDS
                ) as http_client:
                    revocation_data: dict[str, str] = {"token": token.token}
                    request_kwargs: dict[str, Any] = {"data": revocation_data}

                    # Use the factory method when available (supports alternative auth like
                    # client assertions for managed identity), falling back to basic auth
                    # or client_id-only for public clients per RFC 7009
                    async with self._upstream_oauth_client() as oauth_client:
                        if oauth_client.client_secret is not None:
                            # Client secret is available, use HTTP Basic auth
                            request_kwargs["auth"] = (
                                self._upstream_client_id,
                                oauth_client.client_secret,
                            )
                        else:
                            # No secret; public client must still identify itself per RFC 7009
                            revocation_data["client_id"] = self._upstream_client_id

                    await http_client.post(
                        self._upstream_revocation_endpoint,
                        **request_kwargs,
                    )
                    logger.debug("Successfully revoked token with upstream server")
            except Exception as e:
                logger.warning("Failed to revoke token with upstream server: %s", e)
        else:
            logger.debug("No upstream revocation endpoint configured")

        logger.debug("Token revoked successfully")

    def get_routes(
        self,
        mcp_path: str | None = None,
    ) -> list[Route]:
        """Get OAuth routes with custom handlers for better error UX.

        This method creates standard OAuth routes and replaces:
        - /authorize endpoint: Enhanced error responses for unregistered clients
        - /token endpoint: OAuth 2.1 compliant error codes

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp")
                This is used to advertise the resource URL in metadata.
        """
        # Get standard OAuth routes from parent class
        # Note: parent already replaces /token with TokenHandler for proper error codes
        routes = super().get_routes(mcp_path)
        custom_routes = []

        logger.debug(
            f"get_routes called - configuring OAuth routes in {len(routes)} routes"
        )

        for i, route in enumerate(routes):
            logger.debug(
                f"Route {i}: {route} - path: {getattr(route, 'path', 'N/A')}, methods: {getattr(route, 'methods', 'N/A')}"
            )

            # Replace the authorize endpoint with our enhanced handler for better error UX
            if (
                isinstance(route, Route)
                and route.path == "/authorize"
                and route.methods is not None
                and ("GET" in route.methods or "POST" in route.methods)
            ):
                # Replace with our enhanced authorization handler
                # Note: self.base_url is guaranteed to be set in parent __init__
                authorize_handler = AuthorizationHandler(
                    provider=self,
                    base_url=self.base_url,  # ty: ignore[invalid-argument-type]
                    server_name=None,  # Could be extended to pass server metadata
                    server_icon_url=None,
                )
                custom_routes.append(
                    Route(
                        path="/authorize",
                        endpoint=authorize_handler.handle,
                        methods=["GET", "POST"],
                    )
                )
            elif (
                (self._cimd_manager is not None or self._identity_assertion is not None)
                and isinstance(route, Route)
                and route.path == "/token"
                and route.methods is not None
                and "POST" in route.methods
            ):
                # Replace the token endpoint so it can (a) authenticate CIMD
                # private_key_jwt clients and (b) accept the SEP-990 jwt-bearer
                # grant when identity assertion is enabled.
                token_endpoint_url = f"{self.base_url}/token"
                if self._cimd_manager is not None:
                    authenticator: ClientAuthenticator = (
                        PrivateKeyJWTClientAuthenticator(
                            provider=self,
                            cimd_manager=self._cimd_manager,
                            token_endpoint_url=token_endpoint_url,
                        )
                    )
                else:
                    authenticator = ClientAuthenticator(self)
                token_handler = TokenHandler(
                    provider=self,
                    client_authenticator=authenticator,
                    identity_assertion_enabled=self._identity_assertion is not None,
                )
                custom_routes.append(
                    Route(
                        path="/token",
                        endpoint=cors_middleware(
                            token_handler.handle, ["POST", "OPTIONS"]
                        ),
                        methods=["POST", "OPTIONS"],
                    )
                )
            elif isinstance(route, Route) and route.path.startswith(
                "/.well-known/oauth-authorization-server"
            ):
                client_registration_options = (
                    self.client_registration_options or ClientRegistrationOptions()
                )
                revocation_options = self.revocation_options or RevocationOptions()
                metadata = build_metadata(
                    self.base_url,  # ty: ignore[invalid-argument-type]
                    self.service_documentation_url,
                    client_registration_options,
                    revocation_options,
                    supports_identity_assertion=self._identity_assertion is not None,
                )
                # RFC 9207: every authorization response we issue carries an
                # `iss` matching this issuer byte-for-byte, so this route must
                # always be overridden to advertise support — not just when
                # CIMD or identity assertion is also enabled.
                metadata.authorization_response_iss_parameter_supported = True
                if self._cimd_manager is not None:
                    metadata.client_id_metadata_document_supported = True
                    existing = metadata.token_endpoint_auth_methods_supported or []
                    metadata.token_endpoint_auth_methods_supported = [
                        *existing,
                        "private_key_jwt",
                        "none",
                    ]
                if self._identity_assertion is not None:
                    # DCR clients are public (`token_endpoint_auth_method="none"`),
                    # so a metadata consumer must see `none` advertised to use the
                    # jwt-bearer grant — even when CIMD (which also adds it) is off.
                    methods_supported = (
                        metadata.token_endpoint_auth_methods_supported or []
                    )
                    if "none" not in methods_supported:
                        metadata.token_endpoint_auth_methods_supported = [
                            *methods_supported,
                            "none",
                        ]
                handler = MetadataHandler(metadata)
                methods = route.methods or ["GET", "OPTIONS"]

                custom_routes.append(
                    Route(
                        path=route.path,
                        endpoint=cors_middleware(handler.handle, ["GET", "OPTIONS"]),
                        methods=methods,
                        name=route.name,
                        include_in_schema=route.include_in_schema,
                    )
                )
            else:
                # Keep all other standard OAuth routes unchanged
                custom_routes.append(route)

        # Add OAuth callback endpoint for forwarding to client callbacks
        custom_routes.append(
            Route(
                path=self._redirect_path,
                endpoint=self._handle_idp_callback,
                methods=["GET"],
            )
        )

        # Add consent endpoints
        # Handle both GET (show page) and POST (submit) at /consent
        custom_routes.append(
            Route(
                path="/consent", endpoint=self._handle_consent, methods=["GET", "POST"]
            )
        )

        return custom_routes

    # -------------------------------------------------------------------------
    # IdP Callback Forwarding
    # -------------------------------------------------------------------------

    async def _handle_idp_callback(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Handle callback from upstream IdP and forward to client.

        This implements the DCR-compliant callback forwarding:
        1. Receive IdP callback with code and txn_id as state
        2. Exchange IdP code for tokens (server-side)
        3. Generate our own client code bound to PKCE challenge
        4. Redirect to client's callback with client code and original state
        """
        try:
            idp_code = request.query_params.get("code")
            txn_id = request.query_params.get("state")
            error = request.query_params.get("error")

            if not idp_code and not error:
                logger.error("IdP callback missing code or transaction ID")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Missing authorization code or transaction ID from the identity provider.",
                )
                return HTMLResponse(content=html_content, status_code=400)

            # Look up transaction data
            transaction_model = (
                await self._transaction_store.get(key=txn_id) if txn_id else None
            )

            if error:
                error_description = request.query_params.get("error_description")
                logger.error(
                    "IdP callback error: %s - %s",
                    error,
                    error_description,
                )
                if transaction_model:
                    # Forward the error to the client's redirect_uri (RFC 6749 §4.1.2.1)
                    client_redirect_uri = transaction_model.client_redirect_uri
                    if not self._validate_client_redirect_uri(client_redirect_uri):
                        logger.warning(
                            "Blocked IdP callback error redirect to disallowed URI "
                            "for transaction %s",
                            txn_id,
                        )
                        html_content = create_error_html(
                            error_title="OAuth Error",
                            error_message="Invalid redirect URI",
                        )
                        return HTMLResponse(content=html_content, status_code=400)

                    error_params: dict[str, str] = {
                        "error": error,
                        "state": transaction_model.client_state,
                    }
                    if error_description:
                        error_params["error_description"] = error_description
                    return RedirectResponse(
                        url=build_client_redirect(
                            client_redirect_uri,
                            error_params,
                            iss=str(self.base_url),
                        ),
                        status_code=302,
                    )
                # No trusted redirect_uri available — show local error page
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Authentication failed: {error_description or 'Unknown error'}",
                    error_details={"Error Code": error},
                )
                return HTMLResponse(content=html_content, status_code=400)
            if not txn_id or not transaction_model:
                logger.error("IdP callback with invalid transaction ID: %s", txn_id)
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Invalid or expired authorization transaction. Please try authenticating again.",
                )
                return HTMLResponse(content=html_content, status_code=400)

            if not self._validate_client_redirect_uri(
                transaction_model.client_redirect_uri
            ):
                logger.warning(
                    "Blocked IdP callback redirect to disallowed URI for transaction %s",
                    txn_id,
                )
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Invalid redirect URI",
                )
                return HTMLResponse(content=html_content, status_code=400)

            # Verify consent binding cookie to prevent confused deputy attacks.
            # When consent is enabled, the browser that approved consent receives
            # a signed cookie. A different browser (e.g., a victim lured to the
            # IdP URL) won't have this cookie and will be rejected. "remember"
            # mode still issues consent tokens (on silent approval or HTML
            # approval), so both code paths need binding verification.
            if self._require_authorization_consent in (True, "remember"):
                consent_token = transaction_model.consent_token
                if not consent_token:
                    logger.error("Transaction %s missing consent_token", txn_id)
                    html_content = create_error_html(
                        error_title="Authorization Error",
                        error_message="Invalid authorization flow. Please try authenticating again.",
                    )
                    return HTMLResponse(content=html_content, status_code=403)

                if not self._verify_consent_binding_cookie(
                    request, txn_id, consent_token
                ):
                    logger.warning(
                        "Consent binding cookie missing or invalid for transaction %s "
                        "(possible confused deputy attack)",
                        txn_id,
                    )
                    html_content = create_error_html(
                        error_title="Authorization Error",
                        error_message=(
                            "Authorization session mismatch. This can happen if you "
                            "followed a link from another person or your session expired. "
                            "Please try authenticating again."
                        ),
                    )
                    return HTMLResponse(content=html_content, status_code=403)

            transaction = transaction_model.model_dump()

            try:
                idp_redirect_uri = (
                    f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
                )
                logger.debug(
                    f"Exchanging IdP code for tokens with redirect_uri: {idp_redirect_uri}"
                )

                # Build token exchange parameters
                token_params: dict[str, Any] = {
                    "code": idp_code,
                    "redirect_uri": idp_redirect_uri,
                }

                # Include proxy's code_verifier if we forwarded PKCE
                proxy_code_verifier = transaction.get("proxy_code_verifier")
                if proxy_code_verifier:
                    token_params["code_verifier"] = proxy_code_verifier
                    logger.debug(
                        "Including proxy code_verifier in token exchange for transaction %s",
                        txn_id,
                    )

                # Allow providers to specify scope for token exchange
                exchange_scopes = self._prepare_scopes_for_token_exchange(
                    transaction.get("scopes") or []
                )
                if exchange_scopes:
                    token_params["scope"] = " ".join(exchange_scopes)

                # Add any extra token parameters configured for this proxy
                if self._extra_token_params:
                    token_params.update(self._extra_token_params)
                    logger.debug(
                        "Adding extra token parameters for transaction %s: %s",
                        txn_id,
                        list(self._extra_token_params.keys()),
                    )

                # Exchange IdP code for tokens (server-side)
                async with self._upstream_oauth_client() as oauth_client:
                    # url is passed by keyword: the _create_upstream_oauth_client
                    # override point is duck-typed, and alternative clients may
                    # declare it keyword-only (the refresh_token sites already
                    # call by keyword).
                    idp_tokens: dict[str, Any] = await oauth_client.fetch_token(
                        url=self._upstream_token_endpoint, **token_params
                    )

                logger.debug(
                    f"Successfully exchanged IdP code for tokens (transaction: {txn_id}, PKCE: {bool(proxy_code_verifier)})"
                )
                logger.debug(
                    "IdP token response: expires_in=%s, has_refresh_token=%s",
                    idp_tokens.get("expires_in"),
                    "refresh_token" in idp_tokens,
                )

            except Exception as e:
                logger.error("IdP token exchange failed: %s", e)
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Token exchange with identity provider failed: {e}",
                )
                return HTMLResponse(content=html_content, status_code=500)

            # Generate our own authorization code for the client
            client_code = secrets.token_urlsafe(32)
            code_expires_at = int(time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS)

            # Store client code with PKCE challenge and IdP tokens
            await self._code_store.put(
                key=client_code,
                value=ClientCode(
                    code=client_code,
                    client_id=transaction["client_id"],
                    redirect_uri=transaction["client_redirect_uri"],
                    code_challenge=transaction["code_challenge"],
                    code_challenge_method=transaction["code_challenge_method"],
                    scopes=transaction["scopes"],
                    idp_tokens=idp_tokens,
                    expires_at=code_expires_at,
                    created_at=time.time(),
                ),
                ttl=DEFAULT_AUTH_CODE_EXPIRY_SECONDS,  # Auto-expire after 5 minutes
            )

            # Clean up transaction
            await self._transaction_store.delete(key=txn_id)

            # Build client callback URL with our code and original state
            client_redirect_uri = transaction["client_redirect_uri"]
            client_state = transaction["client_state"]

            callback_params = {
                "code": client_code,
                "state": client_state,
            }

            client_callback_url = build_client_redirect(
                client_redirect_uri, callback_params, iss=str(self.base_url)
            )

            logger.debug(f"Forwarding to client callback for transaction {txn_id}")

            response = RedirectResponse(url=client_callback_url, status_code=302)
            self._clear_consent_binding_cookie(request, response, txn_id)
            return response

        except Exception as e:
            logger.error("Error in IdP callback handler: %s", e, exc_info=True)
            html_content = create_error_html(
                error_title="OAuth Error",
                error_message="Internal server error during OAuth callback processing. Please try again.",
            )
            return HTMLResponse(content=html_content, status_code=500)
