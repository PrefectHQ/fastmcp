"""PropelAuth auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, RemoteAuthConfig
from fastmcp.server.plugins.auth.propelauth.provider import (
    PropelAuthProvider,
    PropelAuthTokenIntrospectionOverrides,
)
from fastmcp.server.plugins.base import PluginMeta


class PropelAuthConfig(RemoteAuthConfig):
    """Config model for the PropelAuth auth plugin."""

    auth_url: AnyHttpUrl | str | None = None
    introspection_client_id: str | None = None
    introspection_client_secret: str | None = None
    resource: AnyHttpUrl | str | None = None
    introspection_timeout_seconds: int | None = None
    introspection_cache_ttl_seconds: int | None = None
    introspection_max_cache_size: int | None = None


class PropelAuth(AuthPlugin[PropelAuthConfig]):
    """Contribute a `PropelAuthProvider` as the server's auth provider."""

    Config: ClassVar[type[PropelAuthConfig]] = PropelAuthConfig

    meta = PluginMeta(name="propelauth-auth")

    def __init__(
        self,
        config: PropelAuthConfig | dict[str, Any] | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._http_client = http_client

    def auth(self) -> AuthProvider | None:
        self._require(
            "auth_url",
            "introspection_client_id",
            "introspection_client_secret",
            "base_url",
        )
        overrides: PropelAuthTokenIntrospectionOverrides = {}
        if self.config.introspection_timeout_seconds is not None:
            overrides["timeout_seconds"] = self.config.introspection_timeout_seconds
        if self.config.introspection_cache_ttl_seconds is not None:
            overrides["cache_ttl_seconds"] = self.config.introspection_cache_ttl_seconds
        if self.config.introspection_max_cache_size is not None:
            overrides["max_cache_size"] = self.config.introspection_max_cache_size
        if self._http_client is not None:
            overrides["http_client"] = self._http_client

        return PropelAuthProvider(
            **self._kwargs(
                "auth_url",
                "introspection_client_id",
                "introspection_client_secret",
                "base_url",
                "required_scopes",
                "scopes_supported",
                "resource_name",
                "resource_documentation",
                "resource",
            ),
            token_introspection_overrides=overrides or None,
        )
