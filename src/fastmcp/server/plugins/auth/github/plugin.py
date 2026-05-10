"""GitHub auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProviderConfig
from fastmcp.server.plugins.auth.github.provider import GitHubProvider
from fastmcp.server.plugins.base import PluginMeta


class GitHubAuthConfig(OAuthProviderConfig):
    """Config model for the GitHub auth plugin."""

    cache_ttl_seconds: int | None = None
    max_cache_size: int | None = None


class GitHubAuth(AuthPlugin[GitHubAuthConfig]):
    """Contribute a `GitHubProvider` as the server's auth provider."""

    Config: ClassVar[type[GitHubAuthConfig]] = GitHubAuthConfig

    meta = PluginMeta(name="github-auth")

    def __init__(
        self,
        config: GitHubAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage
        self._http_client = http_client

    def auth(self) -> AuthProvider | None:
        self._require("client_id", "client_secret", "base_url")
        return GitHubProvider(
            **self._kwargs(
                "client_id",
                "client_secret",
                "base_url",
                "resource_base_url",
                "issuer_url",
                "redirect_path",
                "required_scopes",
                "timeout_seconds",
                "cache_ttl_seconds",
                "max_cache_size",
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
