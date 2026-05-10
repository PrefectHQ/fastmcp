"""Descope auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.plugins.auth._base import AuthPlugin, RemoteAuthConfig
from fastmcp.server.plugins.auth.descope.provider import DescopeProvider
from fastmcp.server.plugins.base import PluginMeta


class DescopeAuthConfig(RemoteAuthConfig):
    """Config model for the Descope auth plugin."""

    config_url: AnyHttpUrl | str | None = None
    project_id: str | None = None
    descope_base_url: AnyHttpUrl | str | None = None


class DescopeAuth(AuthPlugin[DescopeAuthConfig]):
    """Contribute a `DescopeProvider` as the server's auth provider."""

    Config: ClassVar[type[DescopeAuthConfig]] = DescopeAuthConfig

    meta = PluginMeta(name="descope-auth")

    def __init__(
        self,
        config: DescopeAuthConfig | dict[str, Any] | None = None,
        *,
        token_verifier: TokenVerifier | None = None,
    ) -> None:
        super().__init__(config)
        self._token_verifier = token_verifier

    def auth(self) -> AuthProvider | None:
        self._require("base_url")
        if self.config.config_url is None:
            self._require("project_id", "descope_base_url")
        return DescopeProvider(
            **self._kwargs(
                "base_url",
                "config_url",
                "project_id",
                "descope_base_url",
                "required_scopes",
                "scopes_supported",
                "resource_name",
                "resource_documentation",
            ),
            token_verifier=self._token_verifier,
        )
