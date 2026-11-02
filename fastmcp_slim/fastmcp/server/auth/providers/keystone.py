"""OpenStack Keystone token verification for FastMCP.

Keystone issues opaque Fernet tokens and has no RFC 7662 introspection
endpoint. Its equivalent is the Identity v3 token validation API
(`GET /v3/auth/tokens`), which this verifier calls through an authenticated
keystoneauth1 session. The service account needs permission to validate other
users' tokens, which Keystone's default policy grants to the `service` role.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.keystone import KeystoneTokenVerifier

    verifier = KeystoneTokenVerifier(
        auth_url="https://keystone.example.com:5000/v3",
        username="mcp-service",
        password="service-password",
        project_name="service",
        allowed_project_ids=["f3a1c6b2d4e54c60a7c9e8b1d2f30456"],
    )

    mcp = FastMCP("My Protected Server", auth=verifier)
    ```
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import anyio.to_thread
from pydantic import AnyHttpUrl, SecretStr

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.token_cache import TokenCache

try:
    from keystoneauth1 import access, adapter, session
    from keystoneauth1.identity import v3
except ImportError as e:
    raise ImportError(
        "KeystoneTokenVerifier requires the `keystone` extra. "
        "Install with: pip install 'fastmcp[keystone]'"
    ) from e

logger = get_logger(__name__)


def _secret_value(secret: str | SecretStr) -> str:
    return secret.get_secret_value() if isinstance(secret, SecretStr) else secret


class KeystoneTokenVerifier(TokenVerifier):
    """Verify OpenStack Keystone tokens with the Identity v3 validation API.

    Clients send their Keystone token as a standard `Authorization: Bearer`
    header. The token's Keystone role names become its FastMCP scopes, so
    `required_scopes` and `require_scopes` operate on roles.

    Authenticate the verifier with either a service user (`username` and
    `password`) or an application credential (`application_credential_id` and
    `application_credential_secret`).

    Keystone authenticates every user in a cloud, so a valid token alone only
    proves that its holder has an account there. On a shared cloud, set
    `allowed_project_ids` to limit access to specific projects.

    Tokens from application credentials with access rules are rejected. Those
    rules name the OpenStack service and API paths a token may reach, and an
    MCP server is not one of the services they can describe.

    Caching is disabled by default so that revoked tokens stop working
    immediately. Set `cache_ttl_seconds` to reduce load on Keystone.
    """

    def __init__(
        self,
        *,
        auth_url: str,
        username: str | None = None,
        password: str | SecretStr | None = None,
        user_domain_name: str = "Default",
        project_name: str | None = None,
        project_domain_name: str = "Default",
        application_credential_id: str | None = None,
        application_credential_secret: str | SecretStr | None = None,
        interface: str = "public",
        region_name: str | None = None,
        verify: bool | str = True,
        timeout_seconds: int = 10,
        required_scopes: list[str] | None = None,
        allowed_project_ids: list[str] | None = None,
        base_url: AnyHttpUrl | str | None = None,
        cache_ttl_seconds: int | None = None,
        max_cache_size: int | None = None,
    ):
        """Initialize the Keystone token verifier.

        Args:
            auth_url: Keystone URL, with or without the `/v3` suffix.
            username: Service user that validates tokens.
            password: Password for the service user.
            user_domain_name: Domain of the service user.
            project_name: Project to scope the service user's token to.
            project_domain_name: Domain of `project_name`.
            application_credential_id: Application credential to authenticate
                with instead of a service user.
            application_credential_secret: Secret for the application credential.
            interface: Service catalog interface used to reach Keystone's
                validation endpoint: `public`, `internal`, or `admin`.
            region_name: Service catalog region of the identity endpoint.
            verify: Whether to verify TLS certificates, or a path to a CA bundle.
            timeout_seconds: Timeout for each request to Keystone.
            required_scopes: Keystone role names that every token must hold.
            allowed_project_ids: Project IDs a token must be scoped to. None
                accepts any token that Keystone considers valid. When set,
                domain-scoped, system-scoped, and unscoped tokens are rejected.
            base_url: Base URL for the TokenVerifier protocol.
            cache_ttl_seconds: How long to cache successful validations. None
                disables caching so that revocation takes effect immediately.
            max_cache_size: Maximum number of cached tokens. Default: 10000.
        """
        super().__init__(
            base_url=base_url,
            required_scopes=(
                parse_scopes(required_scopes) if required_scopes is not None else None
            ),
        )

        has_user = username is not None or password is not None
        has_app_cred = (
            application_credential_id is not None
            or application_credential_secret is not None
        )
        if has_user == has_app_cred:
            raise ValueError(
                "Provide either username and password or "
                "application_credential_id and application_credential_secret."
            )

        base_auth_url = auth_url.rstrip("/").removesuffix("/v3")
        identity_url = f"{base_auth_url}/v3"

        auth: v3.Auth
        if has_app_cred:
            if (
                application_credential_id is None
                or application_credential_secret is None
            ):
                raise ValueError(
                    "application_credential_id and application_credential_secret "
                    "must be provided together."
                )
            auth = v3.ApplicationCredential(
                auth_url=identity_url,
                application_credential_id=application_credential_id,
                application_credential_secret=_secret_value(
                    application_credential_secret
                ),
            )
        else:
            if username is None or password is None:
                raise ValueError("username and password must be provided together.")
            auth = v3.Password(
                auth_url=identity_url,
                username=username,
                password=_secret_value(password),
                user_domain_name=user_domain_name,
                project_name=project_name,
                project_domain_name=project_domain_name if project_name else None,
            )

        self._identity = adapter.Adapter(
            session.Session(auth=auth, verify=verify, timeout=timeout_seconds),
            service_type="identity",
            interface=interface,
            region_name=region_name,
            version="3.0",
        )
        self.allowed_project_ids = (
            None if allowed_project_ids is None else set(allowed_project_ids)
        )
        self._cache = TokenCache(
            ttl_seconds=cache_ttl_seconds,
            max_size=max_cache_size,
        )

    def _fetch_token_data(self, token: str) -> dict[str, Any] | None:
        """Ask Keystone to validate a token, returning its data if valid."""
        response = self._identity.get(
            "/auth/tokens?nocatalog",
            headers={"X-Subject-Token": token},
            raise_exc=False,
            log=False,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            logger.debug("Keystone does not recognize this token")
            return None
        # Anything else is about this verifier rather than the caller's token:
        # 401 and 403 mean Keystone refused the service credentials or their
        # permission to validate tokens. Without this warning, every caller
        # would simply see 401 and the cause would be invisible.
        logger.warning(
            "Keystone token validation failed: HTTP %d", response.status_code
        )
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a token against Keystone's token validation API.

        Args:
            token: The Keystone token to validate.

        Returns:
            AccessToken if Keystone accepts the token and it satisfies this
            verifier's restrictions, otherwise None.
        """
        if not token or not token.strip():
            return None

        is_cached, cached_result = self._cache.get(token)
        if is_cached:
            return cached_result

        try:
            # keystoneauth1 is synchronous and may need to fetch a service
            # token, discover endpoints, and validate, all over blocking HTTP.
            data = await anyio.to_thread.run_sync(self._fetch_token_data, token)
            if data is None:
                return None
            result = self._build_access_token(token, data)
        except Exception as e:
            # Fail closed on outages, bad service credentials, and malformed
            # responses. Log above debug because operators need to see these.
            logger.warning("Keystone token verification error: %s", e)
            return None

        if result is not None:
            self._cache.set(token, result)
        return result

    def _build_access_token(
        self, token: str, data: dict[str, Any]
    ) -> AccessToken | None:
        """Apply this verifier's restrictions and build an AccessToken."""
        auth_ref = access.create(body=data, auth_token=token)

        expires = auth_ref.expires
        if expires is None or expires <= datetime.now(timezone.utc):
            logger.debug("Keystone token has expired")
            return None

        application_credential = data["token"].get("application_credential") or {}
        if application_credential.get("access_rules") is not None:
            logger.debug("Rejecting Keystone token restricted by access rules")
            return None

        project_id = auth_ref.project_id
        if (
            self.allowed_project_ids is not None
            and project_id not in self.allowed_project_ids
        ):
            logger.debug("Keystone token is not scoped to an allowed project")
            return None

        roles = auth_ref.role_names or []
        if self.required_scopes and not set(self.required_scopes).issubset(roles):
            logger.debug("Keystone token is missing required roles")
            return None

        user_id = auth_ref.user_id
        if not user_id:
            logger.debug("Keystone token has no user ID")
            return None

        return AccessToken(
            token=token,
            client_id=user_id,
            scopes=roles,
            expires_at=int(expires.timestamp()),
            subject=user_id,
            claims={
                "sub": user_id,
                "username": auth_ref.username,
                "user_domain_id": auth_ref.user_domain_id,
                "user_domain_name": auth_ref.user_domain_name,
                "project_id": project_id,
                "project_name": auth_ref.project_name,
                "project_domain_id": auth_ref.project_domain_id,
                "project_domain_name": auth_ref.project_domain_name,
                "domain_id": auth_ref.domain_id,
                "domain_name": auth_ref.domain_name,
                "system_scoped": auth_ref.system_scoped,
                "roles": roles,
                "application_credential_id": application_credential.get("id"),
                "audit_id": auth_ref.audit_id,
            },
        )
