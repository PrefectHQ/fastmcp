"""WorkOS OAuth authentication provider for FastMCP."""

from __future__ import annotations

import contextlib
from typing import Literal

import httpx
from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class WorkOSTokenVerifier(TokenVerifier):
    """Token verifier for WorkOS OAuth tokens.

    WorkOS AuthKit tokens are opaque, so we verify them by calling
    the /oauth2/userinfo endpoint to check validity and get user info.
    """

    def __init__(
        self,
        *,
        authkit_domain: str,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        http_client: httpx.AsyncClient | None = None,
    ):
        """Initialize the WorkOS token verifier.

        Args:
            authkit_domain: WorkOS AuthKit domain (e.g., "https://your-app.authkit.app")
            required_scopes: Required OAuth scopes
            timeout_seconds: HTTP request timeout
            http_client: Optional httpx.AsyncClient for connection pooling. When provided,
                the client is reused across calls and the caller is responsible for its
                lifecycle. When None (default), a fresh client is created per call.
        """
        super().__init__(required_scopes=required_scopes)
        self.authkit_domain = authkit_domain.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify WorkOS OAuth token by calling userinfo endpoint."""
        try:
            async with (
                contextlib.nullcontext(self._http_client)
                if self._http_client is not None
                else httpx.AsyncClient(timeout=self.timeout_seconds)
            ) as client:
                # Use WorkOS AuthKit userinfo endpoint to validate token
                response = await client.get(
                    f"{self.authkit_domain}/oauth2/userinfo",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FastMCP-WorkOS-OAuth",
                    },
                )

                if response.status_code != 200:
                    logger.debug(
                        "WorkOS token verification failed: %d - %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None

                user_data = response.json()
                token_scopes = (
                    parse_scopes(user_data.get("scope") or user_data.get("scopes"))
                    or []
                )

                if self.required_scopes and not all(
                    scope in token_scopes for scope in self.required_scopes
                ):
                    logger.debug(
                        "WorkOS token missing required scopes. required=%s actual=%s",
                        self.required_scopes,
                        token_scopes,
                    )
                    return None

                # Create AccessToken with WorkOS user info
                return AccessToken(
                    token=token,
                    client_id=str(user_data.get("sub", "unknown")),
                    scopes=token_scopes,
                    expires_at=None,  # Will be set from token introspection if needed
                    claims={
                        "sub": user_data.get("sub"),
                        "email": user_data.get("email"),
                        "email_verified": user_data.get("email_verified"),
                        "name": user_data.get("name"),
                        "given_name": user_data.get("given_name"),
                        "family_name": user_data.get("family_name"),
                    },
                )

        except httpx.RequestError as e:
            logger.debug("Failed to verify WorkOS token: %s", e)
            return None
        except Exception as e:
            logger.debug("WorkOS token verification error: %s", e)
            return None


class WorkOSProvider(OAuthProxy):
    """Complete WorkOS OAuth provider for FastMCP.

    This provider implements WorkOS AuthKit OAuth using the OAuth Proxy pattern.
    It provides OAuth2 authentication for users through WorkOS Connect applications.

    Features:
    - Transparent OAuth proxy to WorkOS AuthKit
    - Automatic token validation via userinfo endpoint
    - User information extraction from ID tokens
    - Support for standard OAuth scopes (openid, profile, email)

    Setup Requirements:
    1. Create a WorkOS Connect application in your dashboard
    2. Note your AuthKit domain (e.g., "https://your-app.authkit.app")
    3. Configure redirect URI as: http://localhost:8000/auth/callback
    4. Note your Client ID and Client Secret

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.auth.workos.provider import WorkOSProvider

        auth = WorkOSProvider(
            client_id="client_123",
            client_secret="sk_test_456",
            authkit_domain="https://your-app.authkit.app",
            base_url="http://localhost:8000"
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authkit_domain: str,
        base_url: AnyHttpUrl | str,
        resource_base_url: AnyHttpUrl | str | None = None,
        issuer_url: AnyHttpUrl | str | None = None,
        redirect_path: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        allowed_client_redirect_uris: list[str] | None = None,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | None = None,
        require_authorization_consent: bool | Literal["remember", "external"] = True,
        consent_csp_policy: str | None = None,
        forward_resource: bool = True,
        http_client: httpx.AsyncClient | None = None,
        enable_cimd: bool = True,
    ):
        """Initialize WorkOS OAuth provider.

        Args:
            client_id: WorkOS client ID
            client_secret: WorkOS client secret
            authkit_domain: Your WorkOS AuthKit domain (e.g., "https://your-app.authkit.app")
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            resource_base_url: Optional public base URL for the protected resource metadata
                and token audience. Defaults to ``base_url``.
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            redirect_path: Redirect path configured in WorkOS (defaults to "/auth/callback")
            required_scopes: Required OAuth scopes (no default)
            timeout_seconds: HTTP request timeout for WorkOS API calls (defaults to 10)
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, an encrypted file store will be created in the data directory
                (derived from `platformdirs`).
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to WorkOS.
                When False, authorization proceeds directly without user confirmation.
                When "external", the built-in consent screen is skipped but no warning is
                logged, indicating that consent is handled externally (e.g. by the upstream IdP).
                SECURITY WARNING: Only set to False for local development or testing environments.
            http_client: Optional httpx.AsyncClient for connection pooling in token verification.
                When provided, the client is reused across verify_token calls and the caller
                is responsible for its lifecycle. When None (default), a fresh client is created per call.
            enable_cimd: Enable CIMD (Client ID Metadata Document) support for URL-based
                client IDs (default True). Set to False to disable.
        """
        # Apply defaults and ensure authkit_domain is a full URL
        authkit_domain_str = authkit_domain
        if not authkit_domain_str.startswith(("http://", "https://")):
            authkit_domain_str = f"https://{authkit_domain_str}"
        authkit_domain_final = authkit_domain_str.rstrip("/")
        scopes_final = (
            parse_scopes(required_scopes) if required_scopes is not None else []
        )

        # Create WorkOS token verifier
        token_verifier = WorkOSTokenVerifier(
            authkit_domain=authkit_domain_final,
            required_scopes=scopes_final,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )

        # Initialize OAuth proxy with WorkOS AuthKit endpoints
        super().__init__(
            upstream_authorization_endpoint=f"{authkit_domain_final}/oauth2/authorize",
            upstream_token_endpoint=f"{authkit_domain_final}/oauth2/token",
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=token_verifier,
            base_url=base_url,
            resource_base_url=resource_base_url,
            redirect_path=redirect_path,
            issuer_url=issuer_url or base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            consent_csp_policy=consent_csp_policy,
            forward_resource=forward_resource,
            enable_cimd=enable_cimd,
        )

        logger.debug(
            "Initialized WorkOS OAuth provider for client %s with AuthKit domain %s",
            client_id,
            authkit_domain_final,
        )
