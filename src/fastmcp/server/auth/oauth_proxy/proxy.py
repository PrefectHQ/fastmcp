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
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import AsyncOAuth2Client
from cryptography.fernet import Fernet
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
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
from fastmcp.server.auth.auth import OAuthProvider, TokenVerifier
from fastmcp.server.auth.handlers.authorize import AuthorizationHandler
from fastmcp.server.auth.jwt_issuer import (
    JWTIssuer,
    derive_jwt_key,
)
from fastmcp.server.auth.oauth_proxy.consent import ConsentMixin
from fastmcp.server.auth.oauth_proxy.models import (
    DEFAULT_ACCESS_TOKEN_EXPIRY_NO_REFRESH_SECONDS,
    DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
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
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


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
       - Forward refresh requests to upstream using authlib
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
        upstream_client_secret: str,
        upstream_revocation_endpoint: str | None = None,
        # Token validation
        token_verifier: TokenVerifier,
        # FastMCP server configuration
        base_url: AnyHttpUrl | str,
        redirect_path: str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        # Client redirect URI validation
        allowed_client_redirect_uris: list[str] | None = None,
        valid_scopes: list[str] | None = None,
        # PKCE configuration
        forward_pkce: bool = True,
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
        require_authorization_consent: bool = True,
        consent_csp_policy: str | None = None,
        # Token expiry fallback
        fallback_access_token_expiry_seconds: int | None = None,
    ):
        """Initialize the OAuth proxy provider.

        Args:
            upstream_authorization_endpoint: URL of upstream authorization endpoint
            upstream_token_endpoint: URL of upstream token endpoint
            upstream_client_id: Client ID registered with upstream server
            upstream_client_secret: Client secret for upstream server
            upstream_revocation_endpoint: Optional upstream revocation endpoint
            token_verifier: Token verifier for validating access tokens
            base_url: Public URL of the server that exposes this FastMCP server; redirect path is
                relative to this URL
            redirect_path: Redirect path configured in upstream OAuth app (defaults to "/auth/callback")
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url)
            service_documentation_url: Optional service documentation URL
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                Patterns support wildcards (e.g., "http://localhost:*", "https://*.example.com/*").
                If None (default), all redirect URIs are allowed (for DCR compatibility).
                If empty list, no redirect URIs are allowed.
                These are for MCP clients performing loopback redirects, NOT for the upstream OAuth app.
            valid_scopes: List of all the possible valid scopes for a client.
                These are advertised to clients through the `/.well-known` endpoints. Defaults to `required_scopes` if not provided.
            forward_pkce: Whether to forward PKCE to upstream server (default True).
                Enable for providers that support/require PKCE (Google, Azure, AWS, etc.).
                Disable only if upstream provider doesn't support PKCE.
            token_endpoint_auth_method: Token endpoint authentication method for upstream server.
                Common values: "client_secret_basic", "client_secret_post", "none".
                If None, authlib will use its default (typically "client_secret_basic").
            extra_authorize_params: Additional parameters to forward to the upstream authorization endpoint.
                Useful for provider-specific parameters like Auth0's "audience".
                Example: {"audience": "https://api.example.com"}
            extra_token_params: Additional parameters to forward to the upstream token endpoint.
                Useful for provider-specific parameters during token exchange.
            client_storage: Storage backend for OAuth state (client registrations, tokens).
                If None, an encrypted DiskStore will be created in the data directory.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes).
                If bytes are provided, they will be used as-is.
                If a string is provided, it will be derived into a 32-byte key using PBKDF2 (1.2M iterations).
                If not provided, it will be derived from the upstream client secret using HKDF.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to the upstream IdP.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
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
        """

        # Always enable DCR since we implement it locally for MCP clients
        client_registration_options = ClientRegistrationOptions(
            enabled=True,
            valid_scopes=valid_scopes or token_verifier.required_scopes,
        )

        # Enable revocation only if upstream endpoint provided
        revocation_options = (
            RevocationOptions(enabled=True) if upstream_revocation_endpoint else None
        )

        super().__init__(
            base_url=base_url,
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
        self._upstream_client_secret: SecretStr = SecretStr(
            secret_value=upstream_client_secret
        )
        self._upstream_revocation_endpoint: str | None = upstream_revocation_endpoint
        self._default_scope_str: str = " ".join(self.required_scopes or [])

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
                + "This will block all OAuth clients."
            )
        self._allowed_client_redirect_uris: list[str] | None = (
            allowed_client_redirect_uris
        )

        # PKCE configuration
        self._forward_pkce: bool = forward_pkce

        # Token endpoint authentication
        self._token_endpoint_auth_method: str | None = token_endpoint_auth_method

        # Consent screen configuration
        self._require_authorization_consent: bool = require_authorization_consent
        self._consent_csp_policy: str | None = consent_csp_policy
        if not require_authorization_consent:
            logger.warning(
                "Authorization consent screen disabled - only use for local development or testing. "
                + "In production, this screen protects against confused deputy attacks."
            )

        # Extra parameters for authorization and token endpoints
        self._extra_authorize_params: dict[str, str] = extra_authorize_params or {}
        self._extra_token_params: dict[str, str] = extra_token_params or {}

        # Token expiry fallback (None means use smart default based on refresh token)
        self._fallback_access_token_expiry_seconds: int | None = (
            fallback_access_token_expiry_seconds
        )

        if jwt_signing_key is None:
            jwt_signing_key = derive_jwt_key(
                high_entropy_material=upstream_client_secret,
                salt="fastmcp-jwt-signing-key",
            )

        if isinstance(jwt_signing_key, str):
            if len(jwt_signing_key) < 12:
                logger.warning(
                    "jwt_signing_key is less than 12 characters; it is recommended to use a longer. "
                    + "string for the key derivation."
                )
            jwt_signing_key = derive_jwt_key(
                low_entropy_material=jwt_signing_key,
                salt="fastmcp-jwt-signing-key",
            )

        # Store JWT signing key for deferred JWTIssuer creation in set_mcp_path()
        self._jwt_signing_key: bytes = jwt_signing_key
        # JWTIssuer will be created in set_mcp_path() with correct audience
        self._jwt_issuer: JWTIssuer | None = None

        # If the user does not provide a store, we will provide an encrypted disk store
        if client_storage is None:
            # Import lazily to avoid sqlite3 dependency when not using OAuthProxy
            from key_value.aio.stores.disk import DiskStore

            storage_encryption_key = derive_jwt_key(
                high_entropy_material=jwt_signing_key.decode(),
                salt="fastmcp-storage-encryption-key",
            )
            client_storage = FernetEncryptionWrapper(
                key_value=DiskStore(directory=settings.home / "oauth-proxy"),
                fernet=Fernet(key=storage_encryption_key),
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

        logger.debug(
            "Initialized OAuth proxy provider with upstream server %s",
            self._upstream_authorization_endpoint,
        )

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
        """
        # Load from storage
        if not (client := await self._client_store.get(key=client_id)):
            return None

        if client.allowed_redirect_uri_patterns is None:
            client.allowed_redirect_uri_patterns = self._allowed_client_redirect_uris

        return client

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
        # We use token_endpoint_auth_method="none" because the proxy handles
        # all upstream authentication. The client_secret must also be None
        # because the SDK requires secrets to be provided if they're set,
        # regardless of auth method.
        proxy_client: ProxyDCRClient = ProxyDCRClient(
            client_id=client_info.client_id,
            client_secret=None,
            redirect_uris=client_info.redirect_uris or [AnyUrl("http://localhost")],
            grant_types=client_info.grant_types
            or ["authorization_code", "refresh_token"],
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

        If consent is disabled (require_authorization_consent=False), skip the consent screen
        and redirect directly to the upstream IdP.
        """
        # Security check: validate client's requested resource matches this server
        # This prevents tokens intended for one server from being used on another
        client_resource = getattr(params, "resource", None)
        if client_resource and self._resource_url:
            if str(client_resource) != str(self._resource_url):
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
                error="invalid_client",  # type: ignore[arg-type]  # "invalid_client" is valid OAuth error but not in Literal type
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

        # If consent is disabled, skip consent screen and go directly to upstream IdP
        if not self._require_authorization_consent:
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
                error="invalid_client",  # type: ignore[arg-type]  # "invalid_client" is valid OAuth error but not in Literal type
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

        # Calculate refresh token expiry if provided by upstream
        # Some providers include refresh_expires_in, some don't
        refresh_expires_in = None
        refresh_token_expires_at = None
        if idp_tokens.get("refresh_token"):
            if "refresh_expires_in" in idp_tokens:
                refresh_expires_in = int(idp_tokens["refresh_expires_in"])
                refresh_token_expires_at = time.time() + refresh_expires_in
                logger.debug(
                    "Upstream refresh token expires in %d seconds", refresh_expires_in
                )
            else:
                # Default to 30 days if upstream doesn't specify
                # This is conservative - most providers use longer expiry
                refresh_expires_in = 60 * 60 * 24 * 30  # 30 days
                refresh_token_expires_at = time.time() + refresh_expires_in
                logger.debug(
                    "Upstream refresh token expiry unknown, using 30-day default"
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
            scope=" ".join(authorization_code.scopes),
            client_id=client.client_id or "",
            created_at=time.time(),
            raw_token_data=idp_tokens,
        )
        await self._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=max(
                refresh_expires_in or 0, expires_in, 1
            ),  # Keep until longest-lived token expires (min 1s for safety)
        )
        logger.debug("Stored encrypted upstream tokens (jti=%s)", access_jti[:8])

        # Issue minimal FastMCP access token (just a reference via JTI)
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        fastmcp_access_token = self.jwt_issuer.issue_access_token(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            jti=access_jti,
            expires_in=expires_in,
        )

        # Issue minimal FastMCP refresh token if upstream provided one
        # Use upstream refresh token expiry to align lifetimes
        fastmcp_refresh_token = None
        if refresh_jti and refresh_expires_in:
            fastmcp_refresh_token = self.jwt_issuer.issue_refresh_token(
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                jti=refresh_jti,
                expires_in=refresh_expires_in,
            )

        # Store JTI mappings
        await self._jti_mapping_store.put(
            key=access_jti,
            value=JTIMapping(
                jti=access_jti,
                upstream_token_id=upstream_token_id,
                created_at=time.time(),
            ),
            ttl=expires_in,  # Auto-expire with access token
        )
        if refresh_jti:
            await self._jti_mapping_store.put(
                key=refresh_jti,
                value=JTIMapping(
                    jti=refresh_jti,
                    upstream_token_id=upstream_token_id,
                    created_at=time.time(),
                ),
                ttl=60 * 60 * 24 * 30,  # Auto-expire with refresh token (30 days)
            )

        # Store refresh token metadata (keyed by hash for security)
        if fastmcp_refresh_token and refresh_expires_in:
            await self._refresh_token_store.put(
                key=_hash_token(fastmcp_refresh_token),
                value=RefreshTokenMetadata(
                    client_id=client.client_id,
                    scopes=authorization_code.scopes,
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
            expires_in=expires_in,
            refresh_token=fastmcp_refresh_token,
            scope=" ".join(authorization_code.scopes),
        )

    # -------------------------------------------------------------------------
    # Refresh Token Flow
    # -------------------------------------------------------------------------

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
            refresh_payload = self.jwt_issuer.verify_token(refresh_token.token)
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

        # Refresh upstream token using authlib
        oauth_client = AsyncOAuth2Client(
            client_id=self._upstream_client_id,
            client_secret=self._upstream_client_secret.get_secret_value(),
            token_endpoint_auth_method=self._token_endpoint_auth_method,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        # Allow child classes to transform scopes before sending to upstream
        # This enables provider-specific scope formatting (e.g., Azure prefixing)
        # while keeping original scopes in storage
        upstream_scopes = self._prepare_scopes_for_upstream_refresh(scopes)

        try:
            logger.debug("Refreshing upstream token (jti=%s)", refresh_jti[:8])
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

        # Handle upstream refresh token rotation and expiry
        new_refresh_expires_in = None
        if new_upstream_refresh := token_response.get("refresh_token"):
            if new_upstream_refresh != upstream_token_set.refresh_token:
                upstream_token_set.refresh_token = new_upstream_refresh
                logger.debug("Upstream refresh token rotated")

            # Update refresh token expiry if provided
            if "refresh_expires_in" in token_response:
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
                # Default to 30 days if unknown
                new_refresh_expires_in = 60 * 60 * 24 * 30
                upstream_token_set.refresh_token_expires_at = (
                    time.time() + new_refresh_expires_in
                )

        upstream_token_set.raw_token_data = token_response
        # Calculate refresh TTL for storage
        refresh_ttl = new_refresh_expires_in or (
            int(upstream_token_set.refresh_token_expires_at - time.time())
            if upstream_token_set.refresh_token_expires_at
            else 60 * 60 * 24 * 30  # Default to 30 days if unknown
        )
        await self._upstream_token_store.put(
            key=upstream_token_set.upstream_token_id,
            value=upstream_token_set,
            ttl=max(
                refresh_ttl, new_expires_in, 1
            ),  # Keep until longest-lived token expires (min 1s for safety)
        )

        # Issue new minimal FastMCP access token (just a reference via JTI)
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        new_access_jti = secrets.token_urlsafe(32)
        new_fastmcp_access = self.jwt_issuer.issue_access_token(
            client_id=client.client_id,
            scopes=scopes,
            jti=new_access_jti,
            expires_in=new_expires_in,
        )

        # Store new access token JTI mapping
        await self._jti_mapping_store.put(
            key=new_access_jti,
            value=JTIMapping(
                jti=new_access_jti,
                upstream_token_id=upstream_token_set.upstream_token_id,
                created_at=time.time(),
            ),
            ttl=new_expires_in,  # Auto-expire with refreshed access token
        )

        # Issue NEW minimal FastMCP refresh token (rotation for security)
        # Use upstream refresh token expiry to align lifetimes
        new_refresh_jti = secrets.token_urlsafe(32)
        new_fastmcp_refresh = self.jwt_issuer.issue_refresh_token(
            client_id=client.client_id,
            scopes=scopes,
            jti=new_refresh_jti,
            expires_in=new_refresh_expires_in
            or 60 * 60 * 24 * 30,  # Fallback to 30 days
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
                scopes=scopes,
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
            expires_in=new_expires_in,
            refresh_token=new_fastmcp_refresh,  # NEW refresh token (rotated)
            scope=" ".join(scopes),
        )

    # -------------------------------------------------------------------------
    # Token Validation
    # -------------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        """Validate FastMCP JWT by swapping for upstream token.

        This implements the token swap pattern:
        1. Verify FastMCP JWT signature (proves it's our token)
        2. Look up upstream token via JTI mapping
        3. Decrypt upstream token
        4. Validate upstream token with provider (GitHub API, JWT validation, etc.)
        5. Return upstream validation result

        The FastMCP JWT is a reference token - all authorization data comes
        from validating the upstream token via the TokenVerifier.
        """
        try:
            # 1. Verify FastMCP JWT signature and claims
            payload = self.jwt_issuer.verify_token(token)
            jti = payload["jti"]

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
            validated = await self._token_validator.verify_token(
                upstream_token_set.access_token
            )

            if not validated:
                logger.debug("Upstream token validation failed")
                return None

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

        # Attempt upstream revocation if endpoint is configured
        if self._upstream_revocation_endpoint:
            try:
                async with httpx.AsyncClient(
                    timeout=HTTP_TIMEOUT_SECONDS
                ) as http_client:
                    await http_client.post(
                        self._upstream_revocation_endpoint,
                        data={"token": token.token},
                        auth=(
                            self._upstream_client_id,
                            self._upstream_client_secret.get_secret_value(),
                        ),
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

            if error:
                error_description = request.query_params.get("error_description")
                logger.error(
                    "IdP callback error: %s - %s",
                    error,
                    error_description,
                )
                # Show error page to user
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Authentication failed: {error_description or 'Unknown error'}",
                    error_details={"Error Code": error} if error else None,
                )
                return HTMLResponse(content=html_content, status_code=400)

            if not idp_code or not txn_id:
                logger.error("IdP callback missing code or transaction ID")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Missing authorization code or transaction ID from the identity provider.",
                )
                return HTMLResponse(content=html_content, status_code=400)

            # Look up transaction data
            transaction_model = await self._transaction_store.get(key=txn_id)
            if not transaction_model:
                logger.error("IdP callback with invalid transaction ID: %s", txn_id)
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Invalid or expired authorization transaction. Please try authenticating again.",
                )
                return HTMLResponse(content=html_content, status_code=400)
            transaction = transaction_model.model_dump()

            # Exchange IdP code for tokens (server-side)
            oauth_client = AsyncOAuth2Client(
                client_id=self._upstream_client_id,
                client_secret=self._upstream_client_secret.get_secret_value(),
                token_endpoint_auth_method=self._token_endpoint_auth_method,
                timeout=HTTP_TIMEOUT_SECONDS,
            )

            try:
                idp_redirect_uri = (
                    f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
                )
                logger.debug(
                    f"Exchanging IdP code for tokens with redirect_uri: {idp_redirect_uri}"
                )

                # Build token exchange parameters
                token_params = {
                    "url": self._upstream_token_endpoint,
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

                # Add any extra token parameters configured for this proxy
                if self._extra_token_params:
                    token_params.update(self._extra_token_params)
                    logger.debug(
                        "Adding extra token parameters for transaction %s: %s",
                        txn_id,
                        list(self._extra_token_params.keys()),
                    )

                idp_tokens: dict[str, Any] = await oauth_client.fetch_token(
                    **token_params
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

            # Add query parameters to client redirect URI
            separator = "&" if "?" in client_redirect_uri else "?"
            client_callback_url = (
                f"{client_redirect_uri}{separator}{urlencode(callback_params)}"
            )

            logger.debug(f"Forwarding to client callback for transaction {txn_id}")

            return RedirectResponse(url=client_callback_url, status_code=302)

        except Exception as e:
            logger.error("Error in IdP callback handler: %s", e, exc_info=True)
            html_content = create_error_html(
                error_title="OAuth Error",
                error_message="Internal server error during OAuth callback processing. Please try again.",
            )
            return HTMLResponse(content=html_content, status_code=500)
