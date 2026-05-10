"""Auth0 auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProxyConfig
from fastmcp.server.plugins.auth.auth0.provider import Auth0Provider
from fastmcp.server.plugins.base import PluginMeta


class Auth0AuthConfig(OAuthProxyConfig):
    """Config model for the Auth0 auth plugin."""

    config_url: AnyHttpUrl | str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    audience: str | None = None


class Auth0Auth(AuthPlugin[Auth0AuthConfig]):
    """Contribute an `Auth0Provider` as the server's auth provider."""

    Config: ClassVar[type[Auth0AuthConfig]] = Auth0AuthConfig

    meta = PluginMeta(name="auth0-auth")

    def __init__(
        self,
        config: Auth0AuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage

    def auth(self) -> AuthProvider | None:
        self._require(
            "config_url", "client_id", "client_secret", "audience", "base_url"
        )
        return Auth0Provider(
            **self._kwargs(
                "config_url",
                "client_id",
                "client_secret",
                "audience",
                "base_url",
                "resource_base_url",
                "issuer_url",
                "required_scopes",
                "redirect_path",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
            ),
            client_storage=self._client_storage,
        )
