"""Clerk OAuth provider for FastMCP.

This module provides a complete Clerk OAuth integration that's ready to use
with a Clerk domain, client ID, and client secret. It handles all the complexity
of Clerk's OAuth/OIDC flow, token validation, and user management.

Clerk uses standard OIDC endpoints derived from the instance domain
(e.g., ``https://<instance>.clerk.accounts.dev``). Token verification is
performed via the introspection endpoint (RFC 7662) for security-critical
checks (active status, audience, scopes), followed by the userinfo endpoint
for profile enrichment. Userinfo failure is non-fatal.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.clerk import ClerkProvider

    auth = ClerkProvider(
        domain="saving-primate-16.clerk.accounts.dev",
        client_id="your-clerk-client-id",
        client_secret="your-clerk-client-secret",
        base_url="https://my-server.com",
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import contextlib
from typing import Literal

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class ClerkTokenVerifier(TokenVerifier):
    """Token verifier for Clerk OAuth tokens.

    Clerk issues standard OIDC tokens. Verification uses the introspection
    endpoint (RFC 7662) as the primary security gate — it confirms the token
    is active and provides metadata (scopes, expiry, audience). The userinfo
    endpoint is called second for profile enrichment (name, email, picture)
    and its failure is non-fatal.

    When a ``client_id`` is configured, the audience from introspection is
    validated against it. When ``required_scopes`` are configured,
    introspection must return the token's scopes — the verifier will not
    assume scopes when introspection is unavailable.
    """

    def __init__(
        self,
        *,
        domain: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        http_client: httpx.AsyncClient | None = None,
    ):
        """Initialize the Clerk token verifier.

        Args:
            domain: Clerk instance domain (e.g., "saving-primate-16.clerk.accounts.dev")
            client_id: Clerk OAuth client ID, used for introspection endpoint authentication
            client_secret: Clerk OAuth client secret, used for introspection endpoint authentication
            required_scopes: Required OAuth scopes (e.g., ["openid", "email", "profile"])
            timeout_seconds: HTTP request timeout
            http_client: Optional httpx.AsyncClient for connection pooling. When provided,
                the client is reused across calls and the caller is responsible for its
                lifecycle. When None (default), a fresh client is created per call.
        """
        super().__init__(required_scopes=required_scopes)
        self.domain = domain.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

        self._userinfo_url = f"https://{self.domain}/oauth/userinfo"
        self._introspection_url = f"https://{self.domain}/oauth/token_info"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a Clerk OAuth token via introspection and userinfo.

        Calls the introspection endpoint first to validate the token and
        retrieve auth metadata (active status, scopes, expiry, audience).
        If the token passes security checks, the userinfo endpoint is called
        for profile enrichment. Userinfo failure is non-fatal.

        When a ``client_id`` is configured, the token's audience must match it.
        When ``required_scopes`` are configured, introspection must confirm
        them; tokens are rejected if scope information is unavailable.
        """
        try:
            async with (
                contextlib.nullcontext(self._http_client)
                if self._http_client is not None
                else httpx.AsyncClient(timeout=self.timeout_seconds)
            ) as client:
                # Step 1: Validate token via introspection (RFC 7662).
                # Security-critical checks (active, audience, scopes) come first.
                introspect_data_payload: dict = {"token": token}
                introspect_kwargs: dict = {
                    "data": introspect_data_payload,
                    "headers": {"User-Agent": "FastMCP-Clerk-OAuth"},
                }

                if self._client_id and self._client_secret:
                    introspect_kwargs["auth"] = (
                        self._client_id,
                        self._client_secret,
                    )
                elif self._client_id:
                    introspect_data_payload["client_id"] = self._client_id

                introspect_response = await client.post(
                    self._introspection_url,
                    **introspect_kwargs,
                )

                if introspect_response.status_code != 200:
                    logger.debug(
                        "Clerk introspection failed: %d",
                        introspect_response.status_code,
                    )
                    return None

                introspect_data = introspect_response.json()

                # RFC 7662 requires the 'active' field in the response.
                # A missing field indicates a malformed response — reject.
                if "active" not in introspect_data or not introspect_data["active"]:
                    logger.debug(
                        "Clerk introspection: token inactive or missing 'active' field"
                    )
                    return None

                scope_str = introspect_data.get("scope", "")
                token_scopes = scope_str.split() if scope_str else []

                aud = introspect_data.get("aud") or introspect_data.get("client_id")

                expires_at: int | None = None
                exp = introspect_data.get("exp")
                if exp is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        expires_at = int(exp)

                if self._client_id and aud != self._client_id:
                    logger.debug(
                        "Clerk token audience mismatch: got %s, expected %s",
                        aud,
                        self._client_id,
                    )
                    return None

                if self.required_scopes:
                    if not token_scopes:
                        logger.debug(
                            "Clerk token missing scope information; "
                            "cannot verify required scopes %s",
                            self.required_scopes,
                        )
                        return None
                    token_scopes_set = set(token_scopes)
                    required_scopes_set = set(self.required_scopes)
                    if not required_scopes_set.issubset(token_scopes_set):
                        logger.debug(
                            "Clerk token missing required scopes. Has %s, needs %s",
                            token_scopes_set,
                            required_scopes_set,
                        )
                        return None

                # Step 2: Fetch user profile via userinfo.
                # Enriches the token with profile data (name, email, picture).
                sub = introspect_data.get("sub")
                user_data: dict = {}
                try:
                    userinfo_response = await client.get(
                        self._userinfo_url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "User-Agent": "FastMCP-Clerk-OAuth",
                        },
                    )
                    if userinfo_response.status_code == 200:
                        user_data = userinfo_response.json()
                        if not sub:
                            sub = user_data.get("sub")
                except Exception as e:
                    logger.debug("Clerk userinfo call failed: %s", e)

                if not sub:
                    logger.debug("Clerk token missing 'sub' claim")
                    return None

                access_token = AccessToken(
                    token=token,
                    client_id=aud or sub,
                    scopes=token_scopes,
                    expires_at=expires_at,
                    claims={
                        "sub": sub,
                        "aud": aud,
                        "email": user_data.get("email"),
                        "email_verified": user_data.get("email_verified"),
                        "name": user_data.get("name"),
                        "picture": user_data.get("picture"),
                        "given_name": user_data.get("given_name"),
                        "family_name": user_data.get("family_name"),
                        "preferred_username": user_data.get("preferred_username"),
                        "iss": user_data.get("iss"),
                        "clerk_user_data": user_data or None,
                    },
                )
                logger.debug("Clerk token verified successfully for sub=%s", sub)
                return access_token

        except httpx.RequestError as e:
            logger.debug("Failed to verify Clerk token: %s", e)
            return None
        except Exception as e:
            logger.debug("Clerk token verification error: %s", e)
            return None


class ClerkProvider(OAuthProxy):
    """Complete Clerk OAuth provider for FastMCP.

    This provider makes it trivial to add Clerk OAuth protection to any
    FastMCP server. Provide your Clerk instance domain, OAuth app credentials,
    and a base URL, and you're ready to go.

    Clerk uses standard OIDC endpoints derived from the instance domain.
    All endpoint URLs are constructed automatically from the domain parameter.

    Features:
    - Transparent OAuth proxy to Clerk
    - Automatic token validation via Clerk's userinfo & introspection APIs
    - User information extraction from Clerk's OIDC claims
    - PKCE support (S256)
    - Minimal configuration required

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.clerk import ClerkProvider

        auth = ClerkProvider(
            domain="saving-primate-16.clerk.accounts.dev",
            client_id="your-clerk-client-id",
            client_secret="your-clerk-client-secret",
            base_url="https://my-server.com",
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str | None = None,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        required_scopes: list[str] | None = None,
        valid_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        extra_authorize_params: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        enable_cimd: bool = True,
    ):
        """Initialize Clerk OAuth provider.

        Args:
            domain: Clerk instance domain (e.g., "saving-primate-16.clerk.accounts.dev").
                This is used to derive all OAuth/OIDC endpoint URLs.
            client_id: Clerk OAuth application client ID
            client_secret: Clerk OAuth application client secret.
                Optional for PKCE public clients. When omitted, jwt_signing_key must be provided.
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            resource_base_url: Optional public base URL for the protected resource metadata
                and token audience. Defaults to ``base_url``.
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in Clerk OAuth app (defaults to "/auth/callback")
            required_scopes: Required Clerk scopes (defaults to ["openid", "email", "profile"]).
                Clerk supports: "openid", "email", "profile", "public_metadata",
                "private_metadata", "offline_access".
            valid_scopes: All scopes that clients are allowed to request, advertised through
                well-known endpoints. Defaults to required_scopes if not provided.
            timeout_seconds: HTTP request timeout for Clerk API calls (defaults to 10)
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, an encrypted file store will be created in the data directory
                (derived from ``platformdirs``).
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes
                are provided, they will be used as is. If a string is provided, it will be derived
                into a 32-byte key. If not provided, the upstream client secret will be used to
                derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing
                clients (default True). When "external", the built-in consent screen is skipped
                but no warning is logged, indicating that consent is handled externally by Clerk.
            consent_csp_policy: Custom CSP policy for the consent page.
            extra_authorize_params: Additional parameters to forward to Clerk's authorization
                endpoint. Example: {"prompt": "login"} to force re-authentication.
            http_client: Optional httpx.AsyncClient for connection pooling in token verification.
                When provided, the client is reused across verify_token calls and the caller
                is responsible for its lifecycle. When None (default), a fresh client is created
                per call.
            enable_cimd: Enable CIMD (Client ID Metadata Document) support for URL-based
                client IDs (default True). Set to False to disable.
        """
        domain = domain.rstrip("/")

        required_scopes_final = (
            parse_scopes(required_scopes)
            if required_scopes is not None
            else ["openid", "email", "profile"]
        )

        parsed_valid_scopes = (
            parse_scopes(valid_scopes) if valid_scopes is not None else None
        )

        token_verifier = ClerkTokenVerifier(
            domain=domain,
            client_id=client_id,
            client_secret=client_secret,
            required_scopes=required_scopes_final,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )

        extra_authorize_params_final = (
            dict(extra_authorize_params) if extra_authorize_params else {}
        )

        super().__init__(
            upstream_authorization_endpoint=f"https://{domain}/oauth/authorize",
            upstream_token_endpoint=f"https://{domain}/oauth/token",
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            resource_base_url=resource_base_url,
            redirect_path=redirect_path,
            issuer_url=issuer_url or base_url,
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            extra_authorize_params=extra_authorize_params_final or None,
            valid_scopes=parsed_valid_scopes,
            enable_cimd=enable_cimd,
        )

        logger.debug(
            "Initialized Clerk OAuth provider for domain %s with scopes: %s",
            domain,
            required_scopes_final,
        )
