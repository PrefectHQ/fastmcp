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

import base64
import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64encode
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode, urlparse

import httpx
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import AsyncOAuth2Client
from cryptography.fernet import Fernet
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.disk import DiskStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.handlers.token import TokenErrorResponse, TokenSuccessResponse
from mcp.server.auth.handlers.token import TokenHandler as _SDKTokenHandler
from mcp.server.auth.json_response import PydanticJSONResponse
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.routes import cors_middleware
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, BaseModel, Field, SecretStr
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
from fastmcp.server.auth.redirect_validation import (
    validate_redirect_uri,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import (
    BUTTON_STYLES,
    DETAIL_BOX_STYLES,
    DETAILS_STYLES,
    INFO_BOX_STYLES,
    REDIRECT_SECTION_STYLES,
    TOOLTIP_STYLES,
    create_logo,
    create_page,
    create_secure_html_response,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

# Default token expiration times
DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS: Final[int] = 60 * 60  # 1 hour
DEFAULT_AUTH_CODE_EXPIRY_SECONDS: Final[int] = 5 * 60  # 5 minutes

# HTTP client timeout
HTTP_TIMEOUT_SECONDS: Final[int] = 30


# -------------------------------------------------------------------------
# Pydantic Models
# -------------------------------------------------------------------------


class OAuthTransaction(BaseModel):
    """OAuth transaction state for consent flow.

    Stored server-side to track active authorization flows with client context.
    Includes CSRF tokens for consent protection per MCP security best practices.
    """

    txn_id: str
    client_id: str
    client_redirect_uri: str
    client_state: str
    code_challenge: str | None
    code_challenge_method: str
    scopes: list[str]
    created_at: float
    resource: str | None = None
    proxy_code_verifier: str | None = None
    csrf_token: str | None = None
    csrf_expires_at: float | None = None


class ClientCode(BaseModel):
    """Client authorization code with PKCE and upstream tokens.

    Stored server-side after upstream IdP callback. Contains the upstream
    tokens bound to the client's PKCE challenge for secure token exchange.
    """

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    code_challenge_method: str
    scopes: list[str]
    idp_tokens: dict[str, Any]
    expires_at: float
    created_at: float


class UpstreamTokenSet(BaseModel):
    """Stored upstream OAuth tokens from identity provider.

    These tokens are obtained from the upstream provider (Google, GitHub, etc.)
    and stored in plaintext within this model. Encryption is handled transparently
    at the storage layer via FernetEncryptionWrapper. Tokens are never exposed to MCP clients.
    """

    upstream_token_id: str  # Unique ID for this token set
    access_token: str  # Upstream access token
    refresh_token: str | None  # Upstream refresh token
    refresh_token_expires_at: (
        float | None
    )  # Unix timestamp when refresh token expires (if known)
    expires_at: float  # Unix timestamp when access token expires
    token_type: str  # Usually "Bearer"
    scope: str  # Space-separated scopes
    client_id: str  # MCP client this is bound to
    created_at: float  # Unix timestamp
    raw_token_data: dict[str, Any] = Field(default_factory=dict)  # Full token response


class JTIMapping(BaseModel):
    """Maps FastMCP token JTI to upstream token ID.

    This allows stateless JWT validation while still being able to look up
    the corresponding upstream token when tools need to access upstream APIs.
    """

    jti: str  # JWT ID from FastMCP-issued token
    upstream_token_id: str  # References UpstreamTokenSet
    created_at: float  # Unix timestamp


class ProxyDCRClient(OAuthClientInformationFull):
    """Client for DCR proxy with configurable redirect URI validation.

    This special client class is critical for the OAuth proxy to work correctly
    with Dynamic Client Registration (DCR). Here's why it exists:

    Problem:
    --------
    When MCP clients use OAuth, they dynamically register with random localhost
    ports (e.g., http://localhost:55454/callback). The OAuth proxy needs to:
    1. Accept these dynamic redirect URIs from clients based on configured patterns
    2. Use its own fixed redirect URI with the upstream provider (Google, GitHub, etc.)
    3. Forward the authorization code back to the client's dynamic URI

    Solution:
    ---------
    This class validates redirect URIs against configurable patterns,
    while the proxy internally uses its own fixed redirect URI with the upstream
    provider. This allows the flow to work even when clients reconnect with
    different ports or when tokens are cached.

    Without proper validation, clients could get "Redirect URI not registered" errors
    when trying to authenticate with cached tokens, or security vulnerabilities could
    arise from accepting arbitrary redirect URIs.
    """

    allowed_redirect_uri_patterns: list[str] | None = Field(default=None)
    client_name: str | None = Field(default=None)

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        """Validate redirect URI against allowed patterns.

        Since we're acting as a proxy and clients register dynamically,
        we validate their redirect URIs against configurable patterns.
        This is essential for cached token scenarios where the client may
        reconnect with a different port.
        """
        if redirect_uri is not None:
            # Validate against allowed patterns
            if validate_redirect_uri(
                redirect_uri=redirect_uri,
                allowed_patterns=self.allowed_redirect_uri_patterns,
            ):
                return redirect_uri
            # Fall back to normal validation if not in allowed patterns
            return super().validate_redirect_uri(redirect_uri)
        # If no redirect_uri provided, use default behavior
        return super().validate_redirect_uri(redirect_uri)


# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------


def create_consent_html(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Application Access Request",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
) -> str:
    """Create a styled HTML consent page for OAuth authorization requests."""
    import html as html_module

    client_display = html_module.escape(client_name or client_id)
    server_name_escaped = html_module.escape(server_name or "FastMCP")

    # Make server name a hyperlink if website URL is available
    if server_website_url:
        website_url_escaped = html_module.escape(server_website_url)
        server_display = f'<a href="{website_url_escaped}" target="_blank" rel="noopener noreferrer" class="server-name-link">{server_name_escaped}</a>'
    else:
        server_display = server_name_escaped

    # Build intro box with call-to-action
    intro_box = f"""
        <div class="info-box">
            <p>The application <strong>{client_display}</strong> wants to access the MCP server <strong>{server_display}</strong>. Please ensure you recognize the callback address below.</p>
        </div>
    """

    # Build redirect URI section (yellow box, centered)
    redirect_uri_escaped = html_module.escape(redirect_uri)
    redirect_section = f"""
        <div class="redirect-section">
            <span class="label">Credentials will be sent to:</span>
            <div class="value">{redirect_uri_escaped}</div>
        </div>
    """

    # Build advanced details with collapsible section
    detail_rows = [
        ("Application Name", html_module.escape(client_name or client_id)),
        ("Application Website", html_module.escape(client_website_url or "N/A")),
        ("Application ID", client_id),
        ("Redirect URI", redirect_uri_escaped),
        (
            "Requested Scopes",
            ", ".join(html_module.escape(s) for s in scopes) if scopes else "None",
        ),
    ]

    detail_rows_html = "\n".join(
        [
            f"""
        <div class="detail-row">
            <div class="detail-label">{label}:</div>
            <div class="detail-value">{value}</div>
        </div>
        """
            for label, value in detail_rows
        ]
    )

    advanced_details = f"""
        <details>
            <summary>Advanced Details</summary>
            <div class="detail-box">
                {detail_rows_html}
            </div>
        </details>
    """

    # Build form with buttons
    form = f"""
        <form id="consentForm" method="POST" action="/consent/submit">
            <input type="hidden" name="txn_id" value="{txn_id}" />
            <input type="hidden" name="csrf_token" value="{csrf_token}" />
            <div class="button-group">
                <button type="submit" name="action" value="approve" class="btn-approve">Allow Access</button>
                <button type="submit" name="action" value="deny" class="btn-deny">Deny</button>
            </div>
        </form>
    """

    # Build help link with tooltip (identical to current implementation)
    help_link = """
        <div class="help-link-container">
            <span class="help-link">
                Why am I seeing this?
                <span class="tooltip">
                    This FastMCP server requires your consent to allow a new client
                    to connect. This protects you from <a
                    href="https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices#confused-deputy-problem"
                    target="_blank" class="tooltip-link">confused deputy
                    attacks</a>, where malicious clients could impersonate you
                    and steal access.<br><br>
                    <a
                    href="https://gofastmcp.com/servers/auth/oauth-proxy#confused-deputy-attacks"
                    target="_blank" class="tooltip-link">Learn more about
                    FastMCP security →</a>
                </span>
            </span>
        </div>
    """

    # Build the page content
    content = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=server_name or "FastMCP")}
            <h1>Application Access Request</h1>
            {intro_box}
            {redirect_section}
            {advanced_details}
            {form}
        </div>
        {help_link}
    """

    # Additional styles needed for this page
    additional_styles = (
        INFO_BOX_STYLES
        + REDIRECT_SECTION_STYLES
        + DETAILS_STYLES
        + DETAIL_BOX_STYLES
        + BUTTON_STYLES
        + TOOLTIP_STYLES
    )

    # Need to allow form-action for form submission
    csp_policy = "default-src 'none'; style-src 'unsafe-inline'; img-src https:; base-uri 'none'; form-action *"

    return create_page(
        content=content,
        title=title,
        additional_styles=additional_styles,
        csp_policy=csp_policy,
    )


# -------------------------------------------------------------------------
# Handler Classes
# -------------------------------------------------------------------------


class TokenHandler(_SDKTokenHandler):
    """TokenHandler that returns OAuth 2.1 compliant error responses.

    The MCP SDK always returns HTTP 400 for all client authentication issues.
    However, OAuth 2.1 Section 5.3 and the MCP specification require that
    invalid or expired tokens MUST receive a HTTP 401 response.

    This handler extends the base MCP SDK TokenHandler to transform client
    authentication failures into OAuth 2.1 compliant responses:
    - Changes 'unauthorized_client' to 'invalid_client' error code
    - Returns HTTP 401 status code instead of 400 for client auth failures

    Per OAuth 2.1 Section 5.3: "The authorization server MAY return an HTTP 401
    (Unauthorized) status code to indicate which HTTP authentication schemes
    are supported."

    Per MCP spec: "Invalid or expired tokens MUST receive a HTTP 401 response."
    """

    def response(self, obj: TokenSuccessResponse | TokenErrorResponse):
        """Override response method to provide OAuth 2.1 compliant error handling."""
        # Check if this is a client authentication failure (not just unauthorized for grant type)
        # unauthorized_client can mean two things:
        # 1. Client authentication failed (client_id not found or wrong credentials) -> invalid_client 401
        # 2. Client not authorized for this grant type -> unauthorized_client 400 (correct per spec)
        if (
            isinstance(obj, TokenErrorResponse)
            and obj.error == "unauthorized_client"
            and obj.error_description
            and "Invalid client_id" in obj.error_description
        ):
            # Transform client auth failure to OAuth 2.1 compliant response
            return PydanticJSONResponse(
                content=TokenErrorResponse(
                    error="invalid_client",
                    error_description=obj.error_description,
                    error_uri=obj.error_uri,
                ),
                status_code=401,
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                },
            )

        # Otherwise use default behavior from parent class
        return super().response(obj)


class OAuthProxy(OAuthProvider):
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
    The proxy maintains minimal but crucial state:
    - _oauth_transactions: Active authorization flows with client context
    - _client_codes: Authorization codes with PKCE challenges and upstream tokens
    - _access_tokens, _refresh_tokens: Token storage for revocation
    - Token relationship mappings for cleanup and rotation

    Security Considerations
    ----------------------
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
                If None (default), only localhost redirect URIs are allowed.
                If empty list, all redirect URIs are allowed (not recommended for production).
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
        if not require_authorization_consent:
            logger.warning(
                "Authorization consent screen disabled - only use for local development or testing. "
                + "In production, this screen protects against confused deputy attacks."
            )

        # Extra parameters for authorization and token endpoints
        self._extra_authorize_params: dict[str, str] = extra_authorize_params or {}
        self._extra_token_params: dict[str, str] = extra_token_params or {}

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

        self._jwt_issuer: JWTIssuer = JWTIssuer(
            issuer=str(self.base_url),
            audience=f"{str(self.base_url).rstrip('/')}/mcp",
            signing_key=jwt_signing_key,
        )

        # If the user does not provide a store, we will provide an encrypted disk store
        if client_storage is None:
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

        # Local state for token bookkeeping only (no client caching)
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

        # Token relation mappings for cleanup
        self._access_to_refresh: dict[str, str] = {}
        self._refresh_to_access: dict[str, str] = {}

        # Use the provided token validator
        self._token_validator: TokenVerifier = token_verifier

        logger.debug(
            "Initialized OAuth proxy provider with upstream server %s",
            self._upstream_authorization_endpoint,
        )

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
        proxy_client: ProxyDCRClient = ProxyDCRClient(
            client_id=client_info.client_id,
            client_secret=client_info.client_secret,
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
            len(proxy_client.redirect_uris),
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
        1. Store transaction with client details and PKCE (if forwarding)
        2. Return local /consent URL; browser visits consent first
        3. Consent handler redirects to upstream IdP if approved/already approved

        If consent is disabled (require_authorization_consent=False), skip the consent screen
        and redirect directly to the upstream IdP.
        """
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
        expires_in = int(
            idp_tokens.get("expires_in", DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)
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
            client_id=client.client_id,
            created_at=time.time(),
            raw_token_data=idp_tokens,
        )
        await self._upstream_token_store.put(
            key=upstream_token_id,
            value=upstream_token_set,
            ttl=expires_in,  # Auto-expire when access token expires
        )
        logger.debug("Stored encrypted upstream tokens (jti=%s)", access_jti[:8])

        # Issue minimal FastMCP access token (just a reference via JTI)
        fastmcp_access_token = self._jwt_issuer.issue_access_token(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            jti=access_jti,
            expires_in=expires_in,
        )

        # Issue minimal FastMCP refresh token if upstream provided one
        # Use upstream refresh token expiry to align lifetimes
        fastmcp_refresh_token = None
        if refresh_jti and refresh_expires_in:
            fastmcp_refresh_token = self._jwt_issuer.issue_refresh_token(
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

        # Store FastMCP access token for MCP framework validation
        self._access_tokens[fastmcp_access_token] = AccessToken(
            token=fastmcp_access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time() + expires_in),
        )

        # Store FastMCP refresh token if provided
        if fastmcp_refresh_token:
            self._refresh_tokens[fastmcp_refresh_token] = RefreshToken(
                token=fastmcp_refresh_token,
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                expires_at=None,
            )
            # Maintain token relationships for cleanup
            self._access_to_refresh[fastmcp_access_token] = fastmcp_refresh_token
            self._refresh_to_access[fastmcp_refresh_token] = fastmcp_access_token

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

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load refresh token from local storage."""
        return self._refresh_tokens.get(refresh_token)

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
            refresh_payload = self._jwt_issuer.verify_token(refresh_token.token)
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

        try:
            logger.debug("Refreshing upstream token (jti=%s)", refresh_jti[:8])
            token_response: dict[str, Any] = await oauth_client.refresh_token(  # type: ignore[misc]
                url=self._upstream_token_endpoint,
                refresh_token=upstream_token_set.refresh_token,
                scope=" ".join(scopes) if scopes else None,
            )
            logger.debug("Successfully refreshed upstream token")
        except Exception as e:
            logger.error("Upstream token refresh failed: %s", e)
            raise TokenError("invalid_grant", f"Upstream refresh failed: {e}") from e

        # Update stored upstream token
        new_expires_in = int(
            token_response.get("expires_in", DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)
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
        await self._upstream_token_store.put(
            key=upstream_token_set.upstream_token_id,
            value=upstream_token_set,
            ttl=new_expires_in,  # Auto-expire when refreshed access token expires
        )

        # Issue new minimal FastMCP access token (just a reference via JTI)
        new_access_jti = secrets.token_urlsafe(32)
        new_fastmcp_access = self._jwt_issuer.issue_access_token(
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
        new_fastmcp_refresh = self._jwt_issuer.issue_refresh_token(
            client_id=client.client_id,
            scopes=scopes,
            jti=new_refresh_jti,
            expires_in=new_refresh_expires_in
            or 60 * 60 * 24 * 30,  # Fallback to 30 days
        )

        # Store new refresh token JTI mapping with aligned expiry
        refresh_ttl = new_refresh_expires_in or 60 * 60 * 24 * 30
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

        # Update local token tracking
        self._access_tokens[new_fastmcp_access] = AccessToken(
            token=new_fastmcp_access,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time() + new_expires_in),
        )
        self._refresh_tokens[new_fastmcp_refresh] = RefreshToken(
            token=new_fastmcp_refresh,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=None,
        )

        # Update token relationship mappings
        self._access_to_refresh[new_fastmcp_access] = new_fastmcp_refresh
        self._refresh_to_access[new_fastmcp_refresh] = new_fastmcp_access

        # Clean up old token from in-memory tracking
        self._refresh_tokens.pop(refresh_token.token, None)
        old_access = self._refresh_to_access.pop(refresh_token.token, None)
        if old_access:
            self._access_tokens.pop(old_access, None)
            self._access_to_refresh.pop(old_access, None)

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

    async def load_access_token(self, token: str) -> AccessToken | None:
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
            payload = self._jwt_issuer.verify_token(token)
            jti = payload["jti"]

            # 2. Look up upstream token via JTI mapping
            jti_mapping = await self._jti_mapping_store.get(key=jti)
            if not jti_mapping:
                logger.debug("JTI mapping not found: %s", jti)
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

        Removes tokens from local storage and attempts to revoke them with
        the upstream server if a revocation endpoint is configured.
        """
        # Clean up local token storage
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            # Also remove associated refresh token
            paired_refresh = self._access_to_refresh.pop(token.token, None)
            if paired_refresh:
                self._refresh_tokens.pop(paired_refresh, None)
                self._refresh_to_access.pop(paired_refresh, None)
        else:  # RefreshToken
            self._refresh_tokens.pop(token.token, None)
            # Also remove associated access token
            paired_access = self._refresh_to_access.pop(token.token, None)
            if paired_access:
                self._access_tokens.pop(paired_access, None)
                self._access_to_refresh.pop(paired_access, None)

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
        routes = super().get_routes(mcp_path)
        custom_routes = []
        token_route_found = False
        authorize_route_found = False

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
                authorize_route_found = True
                # Replace with our enhanced authorization handler
                authorize_handler = AuthorizationHandler(
                    provider=self,
                    base_url=self.base_url,
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
            # Replace the token endpoint with our custom handler that returns proper OAuth 2.1 error codes
            elif (
                isinstance(route, Route)
                and route.path == "/token"
                and route.methods is not None
                and "POST" in route.methods
            ):
                token_route_found = True
                # Replace with our OAuth 2.1 compliant token handler
                token_handler = TokenHandler(
                    provider=self, client_authenticator=ClientAuthenticator(self)
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
        custom_routes.append(
            Route(path="/consent", endpoint=self._show_consent_page, methods=["GET"])
        )
        custom_routes.append(
            Route(
                path="/consent/submit", endpoint=self._submit_consent, methods=["POST"]
            )
        )

        logger.debug(
            f"✅ OAuth routes configured: authorize_endpoint={authorize_route_found}, token_endpoint={token_route_found}, total routes={len(custom_routes)} (includes OAuth callback + consent)"
        )
        return custom_routes

    # -------------------------------------------------------------------------
    # IdP Callback Forwarding
    # -------------------------------------------------------------------------

    async def _handle_idp_callback(self, request: Request) -> RedirectResponse:
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
                logger.error(
                    "IdP callback error: %s - %s",
                    error,
                    request.query_params.get("error_description"),
                )
                # TODO: Forward error to client callback
                return RedirectResponse(
                    url=f"data:text/html,<h1>OAuth Error</h1><p>{error}: {request.query_params.get('error_description', 'Unknown error')}</p>",
                    status_code=302,
                )

            if not idp_code or not txn_id:
                logger.error("IdP callback missing code or transaction ID")
                return RedirectResponse(
                    url="data:text/html,<h1>OAuth Error</h1><p>Missing authorization code or transaction ID</p>",
                    status_code=302,
                )

            # Look up transaction data
            transaction_model = await self._transaction_store.get(key=txn_id)
            if not transaction_model:
                logger.error("IdP callback with invalid transaction ID: %s", txn_id)
                return RedirectResponse(
                    url="data:text/html,<h1>OAuth Error</h1><p>Invalid or expired transaction</p>",
                    status_code=302,
                )
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
                )  # type: ignore[misc]

                logger.debug(
                    f"Successfully exchanged IdP code for tokens (transaction: {txn_id}, PKCE: {bool(proxy_code_verifier)})"
                )

            except Exception as e:
                logger.error("IdP token exchange failed: %s", e)
                # TODO: Forward error to client callback
                return RedirectResponse(
                    url=f"data:text/html,<h1>OAuth Error</h1><p>Token exchange failed: {e}</p>",
                    status_code=302,
                )

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
            return RedirectResponse(
                url="data:text/html,<h1>OAuth Error</h1><p>Internal server error during IdP callback</p>",
                status_code=302,
            )

    # -------------------------------------------------------------------------
    # Consent Interstitial
    # -------------------------------------------------------------------------

    def _normalize_uri(self, uri: str) -> str:
        """Normalize a URI to a canonical form for consent tracking."""
        parsed = urlparse(uri)
        path = parsed.path or ""
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
        if normalized.endswith("/") and len(path) > 1:
            normalized = normalized[:-1]
        return normalized

    def _make_client_key(self, client_id: str, redirect_uri: str | AnyUrl) -> str:
        """Create a stable key for consent tracking from client_id and redirect_uri."""
        normalized = self._normalize_uri(str(redirect_uri))
        return f"{client_id}:{normalized}"

    def _cookie_name(self, base_name: str) -> str:
        """Return secure cookie name for HTTPS, fallback for HTTP development."""
        if self._is_https:
            return f"__Host-{base_name}"
        return f"__{base_name}"

    def _sign_cookie(self, payload: str) -> str:
        """Sign a cookie payload with HMAC-SHA256.

        Returns: base64(payload).base64(signature)
        """
        # Use upstream client secret as signing key
        key = self._upstream_client_secret.get_secret_value().encode()
        signature = hmac.new(key, payload.encode(), hashlib.sha256).digest()
        signature_b64 = base64.b64encode(signature).decode()
        return f"{payload}.{signature_b64}"

    def _verify_cookie(self, signed_value: str) -> str | None:
        """Verify and extract payload from signed cookie.

        Returns: payload if signature valid, None otherwise
        """
        try:
            if "." not in signed_value:
                return None
            payload, signature_b64 = signed_value.rsplit(".", 1)

            # Verify signature
            key = self._upstream_client_secret.get_secret_value().encode()
            expected_sig = hmac.new(key, payload.encode(), hashlib.sha256).digest()
            provided_sig = base64.b64decode(signature_b64.encode())

            # Constant-time comparison
            if not hmac.compare_digest(expected_sig, provided_sig):
                return None

            return payload
        except Exception:
            return None

    def _decode_list_cookie(self, request: Request, base_name: str) -> list[str]:
        """Decode and verify a signed base64-encoded JSON list from cookie. Returns [] if missing/invalid."""
        # Prefer secure name, but also check non-secure variant for dev
        secure_name = self._cookie_name(base_name)
        raw = request.cookies.get(secure_name) or request.cookies.get(f"__{base_name}")
        if not raw:
            return []
        try:
            # Verify signature
            payload = self._verify_cookie(raw)
            if not payload:
                logger.debug("Cookie signature verification failed for %s", secure_name)
                return []

            # Decode payload
            data = base64.b64decode(payload.encode())
            value = json.loads(data.decode())
            if isinstance(value, list):
                return [str(x) for x in value]
        except Exception:
            logger.debug("Failed to decode cookie %s; treating as empty", secure_name)
        return []

    def _encode_list_cookie(self, values: list[str]) -> str:
        """Encode values to base64 and sign with HMAC.

        Returns: signed cookie value (payload.signature)
        """
        payload = json.dumps(values, separators=(",", ":")).encode()
        payload_b64 = base64.b64encode(payload).decode()
        return self._sign_cookie(payload_b64)

    def _set_list_cookie(
        self,
        response: HTMLResponse | RedirectResponse,
        base_name: str,
        value_b64: str,
        max_age: int,
    ) -> None:
        name = self._cookie_name(base_name)
        response.set_cookie(
            name,
            value_b64,
            max_age=max_age,
            secure=self._is_https,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        """Construct the upstream IdP authorization URL using stored transaction data."""
        query_params: dict[str, Any] = {
            "response_type": "code",
            "client_id": self._upstream_client_id,
            "redirect_uri": f"{str(self.base_url).rstrip('/')}{self._redirect_path}",
            "state": txn_id,
        }

        scopes_to_use = transaction.get("scopes") or self.required_scopes or []
        if scopes_to_use:
            query_params["scope"] = " ".join(scopes_to_use)

        # If PKCE forwarding was enabled, include the proxy challenge
        proxy_code_verifier = transaction.get("proxy_code_verifier")
        if proxy_code_verifier:
            challenge_bytes = hashlib.sha256(proxy_code_verifier.encode()).digest()
            proxy_code_challenge = (
                urlsafe_b64encode(challenge_bytes).decode().rstrip("=")
            )
            query_params["code_challenge"] = proxy_code_challenge
            query_params["code_challenge_method"] = "S256"

        # Forward resource indicator if present in transaction
        if resource := transaction.get("resource"):
            query_params["resource"] = resource

        # Extra configured parameters
        if self._extra_authorize_params:
            query_params.update(self._extra_authorize_params)

        separator = "&" if "?" in self._upstream_authorization_endpoint else "?"
        return f"{self._upstream_authorization_endpoint}{separator}{urlencode(query_params)}"

    async def _show_consent_page(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Display consent page or auto-approve/deny based on cookies."""
        from fastmcp.server.server import FastMCP

        txn_id = request.query_params.get("txn_id")
        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn = txn_model.model_dump()
        client_key = self._make_client_key(txn["client_id"], txn["client_redirect_uri"])

        approved = set(self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS"))
        denied = set(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))

        if client_key in approved:
            upstream_url = self._build_upstream_authorize_url(txn_id, txn)
            return RedirectResponse(url=upstream_url, status_code=302)

        if client_key in denied:
            callback_params = {
                "error": "access_denied",
                "state": txn.get("client_state") or "",
            }
            sep = "&" if "?" in txn["client_redirect_uri"] else "?"
            return RedirectResponse(
                url=f"{txn['client_redirect_uri']}{sep}{urlencode(callback_params)}",
                status_code=302,
            )

        # Need consent: issue CSRF token and show HTML
        csrf_token = secrets.token_urlsafe(32)
        csrf_expires_at = time.time() + 15 * 60

        # Update transaction with CSRF token
        txn_model.csrf_token = csrf_token
        txn_model.csrf_expires_at = csrf_expires_at
        await self._transaction_store.put(
            key=txn_id, value=txn_model, ttl=15 * 60
        )  # Auto-expire after 15 minutes

        # Update dict for use in HTML generation
        txn["csrf_token"] = csrf_token
        txn["csrf_expires_at"] = csrf_expires_at

        # Load client to get client_name if available
        client = await self.get_client(txn["client_id"])
        client_name = getattr(client, "client_name", None) if client else None

        # Extract server metadata from app state
        fastmcp = getattr(request.app.state, "fastmcp_server", None)

        if isinstance(fastmcp, FastMCP):
            server_name = fastmcp.name
            icons = fastmcp.icons
            server_icon_url = icons[0].src if icons else None
            server_website_url = fastmcp.website_url
        else:
            server_name = None
            server_icon_url = None
            server_website_url = None

        html = create_consent_html(
            client_id=txn["client_id"],
            redirect_uri=txn["client_redirect_uri"],
            scopes=txn.get("scopes") or [],
            txn_id=txn_id,
            csrf_token=csrf_token,
            client_name=client_name,
            server_name=server_name,
            server_icon_url=server_icon_url,
            server_website_url=server_website_url,
        )
        response = create_secure_html_response(html)
        # Store CSRF in cookie with short lifetime
        self._set_list_cookie(
            response,
            "MCP_CONSENT_STATE",
            self._encode_list_cookie([csrf_token]),
            max_age=15 * 60,
        )
        return response

    async def _submit_consent(
        self, request: Request
    ) -> RedirectResponse | HTMLResponse:
        """Handle consent approval/denial, set cookies, and redirect appropriately."""
        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        action = str(form.get("action", ""))
        csrf_token = str(form.get("csrf_token", ""))

        if not txn_id:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn_model = await self._transaction_store.get(key=txn_id)
        if not txn_model:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired transaction</p>", status_code=400
            )

        txn = txn_model.model_dump()
        expected_csrf = txn.get("csrf_token")
        expires_at = float(txn.get("csrf_expires_at") or 0)

        if not expected_csrf or csrf_token != expected_csrf or time.time() > expires_at:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid or expired consent token</p>", status_code=400
            )

        client_key = self._make_client_key(txn["client_id"], txn["client_redirect_uri"])

        if action == "approve":
            approved = set(self._decode_list_cookie(request, "MCP_APPROVED_CLIENTS"))
            if client_key not in approved:
                approved.add(client_key)
            approved_b64 = self._encode_list_cookie(sorted(approved))

            upstream_url = self._build_upstream_authorize_url(txn_id, txn)
            response = RedirectResponse(url=upstream_url, status_code=302)
            self._set_list_cookie(
                response, "MCP_APPROVED_CLIENTS", approved_b64, max_age=365 * 24 * 3600
            )
            # Clear CSRF cookie by setting empty short-lived value
            self._set_list_cookie(
                response, "MCP_CONSENT_STATE", self._encode_list_cookie([]), max_age=60
            )
            return response

        elif action == "deny":
            denied = set(self._decode_list_cookie(request, "MCP_DENIED_CLIENTS"))
            if client_key not in denied:
                denied.add(client_key)
            denied_b64 = self._encode_list_cookie(sorted(denied))

            callback_params = {
                "error": "access_denied",
                "state": txn.get("client_state") or "",
            }
            sep = "&" if "?" in txn["client_redirect_uri"] else "?"
            client_callback_url = (
                f"{txn['client_redirect_uri']}{sep}{urlencode(callback_params)}"
            )
            response = RedirectResponse(url=client_callback_url, status_code=302)
            self._set_list_cookie(
                response, "MCP_DENIED_CLIENTS", denied_b64, max_age=365 * 24 * 3600
            )
            self._set_list_cookie(
                response, "MCP_CONSENT_STATE", self._encode_list_cookie([]), max_age=60
            )
            return response

        else:
            return create_secure_html_response(
                "<h1>Error</h1><p>Invalid action</p>", status_code=400
            )
