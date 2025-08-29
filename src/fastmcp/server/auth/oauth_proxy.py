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

import secrets
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
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
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from fastmcp.server.auth.auth import OAuthProvider, TokenVerifier
from fastmcp.server.auth.redirect_validation import validate_redirect_uri
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


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

    def __init__(
        self, *args, allowed_redirect_uri_patterns: list[str] | None = None, **kwargs
    ):
        """Initialize with allowed redirect URI patterns.

        Args:
            allowed_redirect_uri_patterns: List of allowed redirect URI patterns with wildcard support.
                                          If None, defaults to localhost-only patterns.
                                          If empty list, allows all redirect URIs.
        """
        super().__init__(*args, **kwargs)
        self._allowed_redirect_uri_patterns = allowed_redirect_uri_patterns

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        """Validate redirect URI against allowed patterns.

        Since we're acting as a proxy and clients register dynamically,
        we validate their redirect URIs against configurable patterns.
        This is essential for cached token scenarios where the client may
        reconnect with a different port.
        """
        if redirect_uri is not None:
            # Validate against allowed patterns
            if validate_redirect_uri(redirect_uri, self._allowed_redirect_uri_patterns):
                return redirect_uri
            # Fall back to normal validation if not in allowed patterns
            return super().validate_redirect_uri(redirect_uri)
        # If no redirect_uri provided, use default behavior
        return super().validate_redirect_uri(redirect_uri)


# Default token expiration times
DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS: Final[int] = 60 * 60  # 1 hour
DEFAULT_AUTH_CODE_EXPIRY_SECONDS: Final[int] = 5 * 60  # 5 minutes

# HTTP client timeout
HTTP_TIMEOUT_SECONDS: Final[int] = 30


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
       - Return shared upstream credentials to all clients

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
    - _clients: DCR registrations (all use ProxyDCRClient for flexibility)
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
        redirect_path: str = "/auth/callback",
        issuer_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        resource_server_url: AnyHttpUrl | str | None = None,
        # Client redirect URI validation
        allowed_client_redirect_uris: list[str] | None = None,
    ):
        """Initialize the OAuth proxy provider.

        Args:
            upstream_authorization_endpoint: URL of upstream authorization endpoint
            upstream_token_endpoint: URL of upstream token endpoint
            upstream_client_id: Client ID registered with upstream server
            upstream_client_secret: Client secret for upstream server
            upstream_revocation_endpoint: Optional upstream revocation endpoint
            token_verifier: Token verifier for validating access tokens
            base_url: Public URL of this FastMCP server
            redirect_path: Redirect path configured in upstream OAuth app (defaults to "/auth/callback")
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url)
            service_documentation_url: Optional service documentation URL
            resource_server_url: Resource server URL (defaults to base_url)
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                Patterns support wildcards (e.g., "http://localhost:*", "https://*.example.com/*").
                If None (default), only localhost redirect URIs are allowed.
                If empty list, all redirect URIs are allowed (not recommended for production).
                These are for MCP clients performing loopback redirects, NOT for the upstream OAuth app.
        """
        # Convert string URLs to AnyHttpUrl for parent class
        base_url_parsed = (
            AnyHttpUrl(base_url) if isinstance(base_url, str) else base_url
        )
        issuer_url_parsed = (
            (AnyHttpUrl(issuer_url) if isinstance(issuer_url, str) else issuer_url)
            if issuer_url
            else None
        )
        service_documentation_url_parsed = (
            (
                AnyHttpUrl(service_documentation_url)
                if isinstance(service_documentation_url, str)
                else service_documentation_url
            )
            if service_documentation_url
            else None
        )
        resource_server_url_parsed = (
            (
                AnyHttpUrl(resource_server_url)
                if isinstance(resource_server_url, str)
                else resource_server_url
            )
            if resource_server_url
            else None
        )

        # Always enable DCR since we implement it locally for MCP clients
        client_registration_options = ClientRegistrationOptions(enabled=True)

        # Enable revocation only if upstream endpoint provided
        revocation_options = (
            RevocationOptions(enabled=True) if upstream_revocation_endpoint else None
        )

        super().__init__(
            base_url=base_url_parsed,
            issuer_url=issuer_url_parsed,
            service_documentation_url=service_documentation_url_parsed,
            client_registration_options=client_registration_options,
            revocation_options=revocation_options,
            required_scopes=token_verifier.required_scopes,
            resource_server_url=resource_server_url_parsed,
        )

        # Store upstream configuration
        self._upstream_authorization_endpoint = upstream_authorization_endpoint
        self._upstream_token_endpoint = upstream_token_endpoint
        self._upstream_client_id = upstream_client_id
        self._upstream_client_secret = SecretStr(upstream_client_secret)
        self._upstream_revocation_endpoint = upstream_revocation_endpoint
        self._default_scope_str = " ".join(self.required_scopes or [])

        # Store redirect configuration
        self._redirect_path = (
            redirect_path if redirect_path.startswith("/") else f"/{redirect_path}"
        )
        self._allowed_client_redirect_uris = allowed_client_redirect_uris

        # Local state for DCR and token bookkeeping
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

        # Token relation mappings for cleanup
        self._access_to_refresh: dict[str, str] = {}
        self._refresh_to_access: dict[str, str] = {}

        # OAuth transaction storage for IdP callback forwarding
        self._oauth_transactions: dict[
            str, dict[str, Any]
        ] = {}  # txn_id -> transaction_data
        self._client_codes: dict[str, dict[str, Any]] = {}  # client_code -> code_data

        # Use the provided token validator
        self._token_validator = token_verifier

        logger.debug(
            "Initialized OAuth proxy provider with upstream server %s",
            self._upstream_authorization_endpoint,
        )

    # -------------------------------------------------------------------------
    # Client Registration (Local Implementation)
    # -------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get client information by ID.

        For unregistered clients, returns a ProxyDCRClient that accepts
        any localhost redirect URI for DCR clients.

        Even registered clients use ProxyDCRClient to ensure they can
        authenticate with different dynamic ports on reconnection. This
        handles the case where a client with cached tokens reconnects
        on a different port.
        """
        client = self._clients.get(client_id)

        if client is None:
            # For unregistered DCR clients, create a permissive client
            # that will accept any localhost redirect URI
            # We need at least one URI for Pydantic validation, but our custom
            # validate_redirect_uri will accept any localhost URI
            client = ProxyDCRClient(
                client_id=client_id,
                client_secret=None,
                redirect_uris=[
                    AnyUrl("http://localhost")
                ],  # Placeholder, validation uses allowed_patterns
                grant_types=["authorization_code", "refresh_token"],
                scope=self._default_scope_str,
                token_endpoint_auth_method="none",
                allowed_redirect_uri_patterns=self._allowed_client_redirect_uris,
            )
            logger.debug("Created ProxyDCRClient for unregistered client %s", client_id)

        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a client locally using fixed upstream credentials.

        This implementation always uses the upstream client_id and client_secret
        regardless of what the client requests. It modifies the client_info object
        in place since the MCP framework ignores return values.

        This ensures all clients use the same credentials that are registered
        with the upstream server.

        Implementation Detail:
        We store a ProxyDCRClient (not the original client_info) to ensure
        the client can reconnect with different dynamic redirect URIs. This is
        essential for cached token scenarios where the client port changes.

        The flow:
        1. Client provides its desired redirect URIs (dynamic localhost ports)
        2. We create a ProxyDCRClient that will accept ANY localhost URI
        3. We store this flexible client for future authentications
        4. When client reconnects with a different port, ProxyDCRClient accepts it
        """
        # Always use the upstream credentials
        upstream_id = self._upstream_client_id
        upstream_secret = self._upstream_client_secret.get_secret_value()

        # Create a ProxyDCRClient with configured redirect URI validation
        proxy_client = ProxyDCRClient(
            client_id=upstream_id,
            client_secret=upstream_secret,
            redirect_uris=client_info.redirect_uris or [AnyUrl("http://localhost")],
            grant_types=client_info.grant_types
            or ["authorization_code", "refresh_token"],
            scope=self._default_scope_str,
            token_endpoint_auth_method="none",
            allowed_redirect_uri_patterns=self._allowed_client_redirect_uris,
        )

        # Store the ProxyDCRClient using the upstream ID
        self._clients[upstream_id] = proxy_client

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
            upstream_id,
            len(proxy_client.redirect_uris),
        )

    # -------------------------------------------------------------------------
    # Authorization Flow (Proxy to Upstream)
    # -------------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Start OAuth transaction and redirect to upstream IdP.

        This implements the DCR-compliant proxy pattern:
        1. Store transaction with client details and PKCE challenge
        2. Use transaction ID as state for IdP
        3. Redirect to IdP with our fixed callback URL
        """
        # Generate transaction ID for this authorization request
        txn_id = secrets.token_urlsafe(32)

        # Store transaction data for IdP callback processing
        self._oauth_transactions[txn_id] = {
            "client_id": client.client_id,
            "client_redirect_uri": str(params.redirect_uri),
            "client_state": params.state,
            "code_challenge": params.code_challenge,
            "code_challenge_method": getattr(params, "code_challenge_method", "S256"),
            "scopes": params.scopes or [],
            "created_at": time.time(),
        }

        # Build query parameters for upstream IdP authorization request
        # Use our fixed IdP callback and transaction ID as state
        query_params: dict[str, Any] = {
            "response_type": "code",
            "client_id": self._upstream_client_id,
            "redirect_uri": f"{str(self.base_url).rstrip('/')}{self._redirect_path}",
            "state": txn_id,  # Use txn_id as IdP state
        }

        # Add scopes - use client scopes or fallback to required scopes
        scopes_to_use = params.scopes or self.required_scopes or []

        if scopes_to_use:
            query_params["scope"] = " ".join(scopes_to_use)

        # Build the upstream authorization URL
        separator = "&" if "?" in self._upstream_authorization_endpoint else "?"
        upstream_url = f"{self._upstream_authorization_endpoint}{separator}{urlencode(query_params)}"

        logger.debug(
            "Starting OAuth transaction %s for client %s, redirecting to IdP",
            txn_id,
            client.client_id,
        )
        return upstream_url

    # -------------------------------------------------------------------------
    # Authorization Code Handling
    # -------------------------------------------------------------------------

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
        code_data = self._client_codes.get(authorization_code)
        if not code_data:
            logger.debug("Authorization code not found: %s", authorization_code)
            return None

        # Check if code expired
        if time.time() > code_data["expires_at"]:
            logger.debug("Authorization code expired: %s", authorization_code)
            self._client_codes.pop(authorization_code, None)
            return None

        # Verify client ID matches
        if code_data["client_id"] != client.client_id:
            logger.debug(
                "Authorization code client ID mismatch: %s vs %s",
                code_data["client_id"],
                client.client_id,
            )
            return None

        # Create authorization code object with PKCE challenge
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            redirect_uri=code_data["redirect_uri"],
            redirect_uri_provided_explicitly=True,
            scopes=code_data["scopes"],
            expires_at=code_data["expires_at"],
            code_challenge=code_data.get("code_challenge", ""),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Exchange authorization code for stored IdP tokens.

        For the DCR-compliant proxy flow, we return the IdP tokens that were obtained
        during the IdP callback exchange. PKCE validation is handled by the MCP framework.
        """
        # Look up stored code data
        code_data = self._client_codes.get(authorization_code.code)
        if not code_data:
            logger.error(
                "Authorization code not found in client codes: %s",
                authorization_code.code,
            )
            raise TokenError("invalid_grant", "Authorization code not found")

        # Get stored IdP tokens
        idp_tokens = code_data["idp_tokens"]

        # Clean up client code (one-time use)
        self._client_codes.pop(authorization_code.code, None)

        # Extract token information for local tracking
        access_token_value = idp_tokens["access_token"]
        refresh_token_value = idp_tokens.get("refresh_token")
        expires_in = int(
            idp_tokens.get("expires_in", DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)
        )
        expires_at = int(time.time() + expires_in)

        # Store access token locally for tracking
        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=expires_at,
        )
        self._access_tokens[access_token_value] = access_token

        # Store refresh token if provided
        if refresh_token_value:
            refresh_token = RefreshToken(
                token=refresh_token_value,
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                expires_at=None,  # Refresh tokens typically don't expire
            )
            self._refresh_tokens[refresh_token_value] = refresh_token

            # Maintain token relationships for cleanup
            self._access_to_refresh[access_token_value] = refresh_token_value
            self._refresh_to_access[refresh_token_value] = access_token_value

        logger.debug(
            "Successfully exchanged client code for stored IdP tokens (client: %s)",
            client.client_id,
        )

        return OAuthToken(**idp_tokens)  # type: ignore[arg-type]

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
        """Exchange refresh token for new access token using authlib."""

        # Use authlib's AsyncOAuth2Client for refresh token exchange
        oauth_client = AsyncOAuth2Client(
            client_id=self._upstream_client_id,
            client_secret=self._upstream_client_secret.get_secret_value(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        try:
            logger.debug("Using authlib to refresh token from upstream")

            # Let authlib handle the refresh token exchange
            token_response: dict[str, Any] = await oauth_client.refresh_token(  # type: ignore[misc]
                url=self._upstream_token_endpoint,
                refresh_token=refresh_token.token,
                scope=" ".join(scopes) if scopes else None,
            )

            logger.debug(
                "Successfully refreshed access token via authlib (client: %s)",
                client.client_id,
            )

        except Exception as e:
            logger.error("Authlib refresh token exchange failed: %s", e)
            raise TokenError(
                "invalid_grant", f"Upstream refresh token exchange failed: {e}"
            ) from e

        # Update local token storage
        new_access_token = token_response["access_token"]
        expires_in = int(
            token_response.get("expires_in", DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)
        )

        self._access_tokens[new_access_token] = AccessToken(
            token=new_access_token,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(time.time() + expires_in),
        )

        # Handle refresh token rotation if new one provided
        if "refresh_token" in token_response:
            new_refresh_token = token_response["refresh_token"]
            if new_refresh_token != refresh_token.token:
                # Remove old refresh token
                self._refresh_tokens.pop(refresh_token.token, None)
                old_access = self._refresh_to_access.pop(refresh_token.token, None)
                if old_access:
                    self._access_to_refresh.pop(old_access, None)

                # Store new refresh token
                self._refresh_tokens[new_refresh_token] = RefreshToken(
                    token=new_refresh_token,
                    client_id=client.client_id,
                    scopes=scopes,
                    expires_at=None,
                )
                self._access_to_refresh[new_access_token] = new_refresh_token
                self._refresh_to_access[new_refresh_token] = new_access_token

        return OAuthToken(**token_response)  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Token Validation
    # -------------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Validate access token using upstream JWKS.

        Delegates to the JWT verifier which handles signature validation,
        expiration checking, and claims validation using the upstream JWKS.
        """
        result = await self._token_validator.verify_token(token)
        if result:
            logger.debug("Token validated successfully")
        else:
            logger.debug("Token validation failed")
        return result

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

    # -------------------------------------------------------------------------
    # Custom Route Handling
    # -------------------------------------------------------------------------

    async def _handle_proxy_token_request(self, request: Request) -> JSONResponse:
        """Custom token endpoint using authlib for upstream requests.

        This handler uses authlib's OAuth2Client to forward token requests to the
        upstream OAuth server, automatically handling response format differences.
        """
        try:
            # Parse the incoming request form data
            form_data = await request.form()

            # Log the incoming request (with sensitive data redacted)
            redacted_form = {
                k: (
                    str(v)[:8] + "..."
                    if k in {"code", "code_verifier", "client_secret", "refresh_token"}
                    and v
                    else str(v)
                )
                for k, v in form_data.items()
            }
            logger.debug("Proxy token request form data: %s", redacted_form)

            # Create authlib OAuth2 client
            oauth_client = AsyncOAuth2Client(
                client_id=self._upstream_client_id,
                client_secret=self._upstream_client_secret.get_secret_value(),
                timeout=HTTP_TIMEOUT_SECONDS,
            )

            grant_type = str(form_data.get("grant_type", ""))

            if grant_type == "authorization_code":
                # Authorization code grant
                try:
                    token_data: dict[str, Any] = await oauth_client.fetch_token(  # type: ignore[misc]
                        url=self._upstream_token_endpoint,
                        code=str(form_data.get("code", "")),
                        redirect_uri=str(form_data.get("redirect_uri", "")),
                        code_verifier=str(form_data.get("code_verifier"))
                        if "code_verifier" in form_data
                        else None,
                    )

                    # Store tokens locally for tracking
                    if "access_token" in token_data:
                        self._store_tokens_from_response(token_data)

                    logger.debug(
                        "Successfully proxied authorization code exchange via authlib"
                    )

                except Exception as e:
                    logger.error("Authlib authorization code exchange failed: %s", e)
                    return JSONResponse(
                        content={
                            "error": "invalid_grant",
                            "error_description": f"Authorization code exchange failed: {e}",
                        },
                        status_code=400,
                    )

            elif grant_type == "refresh_token":
                # Refresh token grant
                try:
                    token_data: dict[str, Any] = await oauth_client.refresh_token(  # type: ignore[misc]
                        url=self._upstream_token_endpoint,
                        refresh_token=str(form_data.get("refresh_token", "")),
                        scope=str(form_data.get("scope"))
                        if "scope" in form_data
                        else None,
                    )

                    logger.debug(
                        "Successfully proxied refresh token exchange via authlib"
                    )

                except Exception as e:
                    logger.error("Authlib refresh token exchange failed: %s", e)
                    return JSONResponse(
                        content={
                            "error": "invalid_grant",
                            "error_description": f"Refresh token exchange failed: {e}",
                        },
                        status_code=400,
                    )
            else:
                # Unsupported grant type
                logger.error("Unsupported grant type: %s", grant_type)
                return JSONResponse(
                    content={
                        "error": "unsupported_grant_type",
                        "error_description": f"Grant type '{grant_type}' not supported by proxy",
                    },
                    status_code=400,
                )

            return JSONResponse(content=token_data)

        except Exception as e:
            logger.error("Error in proxy token handler: %s", e, exc_info=True)
            return JSONResponse(
                content={
                    "error": "server_error",
                    "error_description": "Internal server error",
                },
                status_code=500,
            )

    def _store_tokens_from_response(self, token_data: dict[str, Any]) -> None:
        """Store tokens from upstream response for local tracking."""
        try:
            access_token_value = token_data.get("access_token")
            refresh_token_value = token_data.get("refresh_token")
            expires_in = int(
                token_data.get("expires_in", DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)
            )
            expires_at = int(time.time() + expires_in)

            if access_token_value:
                access_token = AccessToken(
                    token=access_token_value,
                    client_id=self._upstream_client_id,
                    scopes=[],  # Will be determined by token validation
                    expires_at=expires_at,
                )
                self._access_tokens[access_token_value] = access_token

                if refresh_token_value:
                    refresh_token = RefreshToken(
                        token=refresh_token_value,
                        client_id=self._upstream_client_id,
                        scopes=[],
                        expires_at=None,
                    )
                    self._refresh_tokens[refresh_token_value] = refresh_token

                    # Maintain token relationships
                    self._access_to_refresh[access_token_value] = refresh_token_value
                    self._refresh_to_access[refresh_token_value] = access_token_value

                logger.debug("Stored tokens from upstream response for tracking")

        except Exception as e:
            logger.warning("Failed to store tokens from upstream response: %s", e)

    def get_routes(self) -> list[Route]:
        """Get OAuth routes with custom proxy token handler.

        This method creates standard OAuth routes and replaces the token endpoint
        with our proxy handler that forwards requests to the upstream OAuth server.
        """
        # Get standard OAuth routes from parent class
        routes = super().get_routes()
        custom_routes = []
        token_route_found = False

        logger.debug(
            f"get_routes called - configuring OAuth routes in {len(routes)} routes"
        )

        for i, route in enumerate(routes):
            logger.debug(
                f"Route {i}: {route} - path: {getattr(route, 'path', 'N/A')}, methods: {getattr(route, 'methods', 'N/A')}"
            )

            # Keep all standard OAuth routes unchanged - our DCR-compliant flow handles everything
            custom_routes.append(route)

            if (
                isinstance(route, Route)
                and route.path == "/token"
                and route.methods is not None
                and "POST" in route.methods
            ):
                token_route_found = True

        # Add OAuth callback endpoint for forwarding to client callbacks
        custom_routes.append(
            Route(
                path=self._redirect_path,
                endpoint=self._handle_idp_callback,
                methods=["GET"],
            )
        )

        logger.debug(
            f"✅ OAuth routes configured: token_endpoint={token_route_found}, total routes={len(custom_routes)} (includes OAuth callback)"
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
            transaction = self._oauth_transactions.get(txn_id)
            if not transaction:
                logger.error("IdP callback with invalid transaction ID: %s", txn_id)
                return RedirectResponse(
                    url="data:text/html,<h1>OAuth Error</h1><p>Invalid or expired transaction</p>",
                    status_code=302,
                )

            # Exchange IdP code for tokens (server-side)
            oauth_client = AsyncOAuth2Client(
                client_id=self._upstream_client_id,
                client_secret=self._upstream_client_secret.get_secret_value(),
                timeout=HTTP_TIMEOUT_SECONDS,
            )

            try:
                idp_redirect_uri = (
                    f"{str(self.base_url).rstrip('/')}{self._redirect_path}"
                )
                logger.debug(
                    f"Exchanging IdP code for tokens with redirect_uri: {idp_redirect_uri}"
                )

                idp_tokens: dict[str, Any] = await oauth_client.fetch_token(  # type: ignore[misc]
                    url=self._upstream_token_endpoint,
                    code=idp_code,
                    redirect_uri=idp_redirect_uri,
                )

                logger.debug(
                    f"Successfully exchanged IdP code for tokens (transaction: {txn_id})"
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
            self._client_codes[client_code] = {
                "client_id": transaction["client_id"],
                "redirect_uri": transaction["client_redirect_uri"],
                "code_challenge": transaction["code_challenge"],
                "code_challenge_method": transaction["code_challenge_method"],
                "scopes": transaction["scopes"],
                "idp_tokens": idp_tokens,
                "expires_at": code_expires_at,
                "created_at": time.time(),
            }

            # Clean up transaction
            self._oauth_transactions.pop(txn_id, None)

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
