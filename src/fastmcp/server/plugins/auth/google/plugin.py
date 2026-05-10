"""Google auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProviderConfig
from fastmcp.server.plugins.auth.google.provider import GoogleProvider
from fastmcp.server.plugins.base import PluginMeta


class GoogleAuthConfig(OAuthProviderConfig):
    """Config model for the Google auth plugin."""

    valid_scopes: list[str] | None = None
    extra_authorize_params: dict[str, str] | None = None


class GoogleAuth(AuthPlugin[GoogleAuthConfig]):
    """Contribute a `GoogleProvider` as the server's auth provider."""

    Config: ClassVar[type[GoogleAuthConfig]] = GoogleAuthConfig

    meta = PluginMeta(name="google-auth")

    def __init__(
        self,
        config: GoogleAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage
        self._http_client = http_client

    def auth(self) -> AuthProvider | None:
        self._require("client_id", "base_url")
        self._require_one("client_secret", "jwt_signing_key")
        return GoogleProvider(
            **self._kwargs(
                "client_id",
                "client_secret",
                "base_url",
                "resource_base_url",
                "issuer_url",
                "redirect_path",
                "required_scopes",
                "valid_scopes",
                "timeout_seconds",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
                "extra_authorize_params",
                "enable_cimd",
            ),
            client_storage=self._client_storage,
            http_client=self._http_client,
        )
