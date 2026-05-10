"""WorkOS auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProviderConfig
from fastmcp.server.plugins.auth.workos.provider import WorkOSProvider
from fastmcp.server.plugins.base import PluginMeta


class WorkOSAuthConfig(OAuthProviderConfig):
    """Config model for the WorkOS auth plugin."""

    authkit_domain: str | None = None


class WorkOSAuth(AuthPlugin[WorkOSAuthConfig]):
    """Contribute a `WorkOSProvider` as the server's auth provider."""

    Config: ClassVar[type[WorkOSAuthConfig]] = WorkOSAuthConfig

    meta = PluginMeta(name="workos-auth")

    def __init__(
        self,
        config: WorkOSAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage
        self._http_client = http_client

    def auth(self) -> AuthProvider | None:
        self._require("client_id", "client_secret", "authkit_domain", "base_url")
        return WorkOSProvider(
            **self._kwargs(
                "client_id",
                "client_secret",
                "authkit_domain",
                "base_url",
                "resource_base_url",
                "issuer_url",
                "redirect_path",
                "required_scopes",
                "timeout_seconds",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
                "enable_cimd",
            ),
            client_storage=self._client_storage,
            http_client=self._http_client,
        )
