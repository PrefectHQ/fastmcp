"""Auth0 OAuth provider for FastMCP.

This module provides a complete Auth0 integration that's ready to use with
just the configuration URL, client ID, client secret, audience, and base URL.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.auth0 import Auth0Provider

    # Simple Auth0 OAuth protection
    auth = Auth0Provider(
        config_url="https://auth0.config.url",
        client_id="your-auth0-client-id",
        client_secret="your-auth0-client-secret",
        audience="your-auth0-api-audience",
        base_url="http://localhost:8000",
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

logger = get_logger(__name__)


class Auth0ProviderSettings(BaseSettings):
    """Settings for Auth0 OIDC provider."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_SERVER_AUTH_AUTH0_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    config_url: AnyHttpUrl | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    audience: str | None = None
    base_url: AnyHttpUrl | None = None
    issuer_url: AnyHttpUrl | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    allowed_client_redirect_uris: list[str] | None = None
    jwt_signing_key: str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class Auth0Provider(OIDCProxy):
    """An Auth0 provider implementation for FastMCP.

    This provider is a complete Auth0 integration that's ready to use with
    just the configuration URL, client ID, client secret, audience, and base URL.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.auth0 import Auth0Provider

        # Simple Auth0 OAuth protection
        auth = Auth0Provider(
            config_url="https://auth0.config.url",
            client_id="your-auth0-client-id",
            client_secret="your-auth0-client-secret",
            audience="your-auth0-api-audience",
            base_url="http://localhost:8000",
        )

        mcp = FastMCP("My Protected Server", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        config_url: AnyHttpUrl | str | NotSetT = NotSet,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        audience: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        issuer_url: AnyHttpUrl | str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
    ) -> None:
        """Initialize Auth0 OAuth provider.

        Args:
            config_url: Auth0 config URL
            client_id: Auth0 application client id
            client_secret: Auth0 application client secret
            audience: Auth0 API audience
            base_url: Public URL where OAuth endpoints will be accessible (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url). Use root-level URL
                to avoid 404s during discovery when mounting under a path.
            required_scopes: Required Auth0 scopes (defaults to ["openid"])
            redirect_path: Redirect path configured in Auth0 application
            allowed_client_redirect_uris: List of allowed redirect URI patterns for MCP clients.
                If None (default), all URIs are allowed. If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state (client registrations, encrypted tokens).
                If None, a DiskStore will be created in the data directory (derived from `platformdirs`). The
                disk store will be encrypted using a key derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens (any string or bytes). If bytes are provided,
                they will be used as is. If a string is provided, it will be derived into a 32-byte key. If not
                provided, the upstream client secret will be used to derive a 32-byte key using PBKDF2.
            require_authorization_consent: Whether to require user consent before authorizing clients (default True).
                When True, users see a consent screen before being redirected to Auth0.
                When False, authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or testing environments.
        """
        settings = Auth0ProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "config_url": config_url,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": audience,
                    "base_url": base_url,
                    "issuer_url": issuer_url,
                    "required_scopes": required_scopes,
                    "redirect_path": redirect_path,
                    "allowed_client_redirect_uris": allowed_client_redirect_uris,
                    "jwt_signing_key": jwt_signing_key,
                }.items()
                if v is not NotSet
            }
        )

        if not settings.config_url:
            raise ValueError(
                "config_url is required - set via parameter or FASTMCP_SERVER_AUTH_AUTH0_CONFIG_URL"
            )

        if not settings.client_id:
            raise ValueError(
                "client_id is required - set via parameter or FASTMCP_SERVER_AUTH_AUTH0_CLIENT_ID"
            )

        if not settings.client_secret:
            raise ValueError(
                "client_secret is required - set via parameter or FASTMCP_SERVER_AUTH_AUTH0_CLIENT_SECRET"
            )

        if not settings.audience:
            raise ValueError(
                "audience is required - set via parameter or FASTMCP_SERVER_AUTH_AUTH0_AUDIENCE"
            )

        if not settings.base_url:
            raise ValueError(
                "base_url is required - set via parameter or FASTMCP_SERVER_AUTH_AUTH0_BASE_URL"
            )

        auth0_required_scopes = settings.required_scopes or ["openid"]

        super().__init__(
            config_url=settings.config_url,
            client_id=settings.client_id,
            client_secret=settings.client_secret.get_secret_value(),
            audience=settings.audience,
            base_url=settings.base_url,
            issuer_url=settings.issuer_url,
            redirect_path=settings.redirect_path,
            required_scopes=auth0_required_scopes,
            allowed_client_redirect_uris=settings.allowed_client_redirect_uris,
            client_storage=client_storage,
            jwt_signing_key=settings.jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
        )

        logger.debug(
            "Initialized Auth0 OAuth provider for client %s with scopes: %s",
            settings.client_id,
            auth0_required_scopes,
        )
