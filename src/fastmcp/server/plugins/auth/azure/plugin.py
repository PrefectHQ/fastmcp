"""Azure auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProviderConfig
from fastmcp.server.plugins.auth.azure.provider import AzureProvider
from fastmcp.server.plugins.base import PluginMeta


class AzureAuthConfig(OAuthProviderConfig):
    """Config model for the Azure auth plugin."""

    tenant_id: str | None = None
    required_scopes: list[str] | None = None
    identifier_uri: str | None = None
    additional_authorize_scopes: list[str] | None = None
    base_authority: str = "login.microsoftonline.com"


class AzureAuth(AuthPlugin[AzureAuthConfig]):
    """Contribute an `AzureProvider` as the server's auth provider."""

    Config: ClassVar[type[AzureAuthConfig]] = AzureAuthConfig

    meta = PluginMeta(name="azure-auth")

    def __init__(
        self,
        config: AzureAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage
        self._http_client = http_client

    def auth(self) -> AuthProvider | None:
        self._require("client_id", "tenant_id", "required_scopes", "base_url")
        self._require_one("client_secret", "jwt_signing_key")
        return AzureProvider(
            **self._kwargs(
                "client_id",
                "client_secret",
                "tenant_id",
                "required_scopes",
                "base_url",
                "resource_base_url",
                "identifier_uri",
                "issuer_url",
                "redirect_path",
                "additional_authorize_scopes",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
                "base_authority",
                "enable_cimd",
            ),
            client_storage=self._client_storage,
            http_client=self._http_client,
        )
