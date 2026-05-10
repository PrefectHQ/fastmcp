"""Scalekit auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.plugins.auth._base import AuthPlugin, RemoteAuthConfig
from fastmcp.server.plugins.auth.scalekit.provider import ScalekitProvider
from fastmcp.server.plugins.base import PluginMeta


class ScalekitAuthConfig(RemoteAuthConfig):
    """Config model for the Scalekit auth plugin."""

    environment_url: AnyHttpUrl | str | None = None
    resource_id: str | None = None
    mcp_url: AnyHttpUrl | str | None = None
    client_id: str | None = None


class ScalekitAuth(AuthPlugin[ScalekitAuthConfig]):
    """Contribute a `ScalekitProvider` as the server's auth provider."""

    Config: ClassVar[type[ScalekitAuthConfig]] = ScalekitAuthConfig

    meta = PluginMeta(name="scalekit-auth")

    def __init__(
        self,
        config: ScalekitAuthConfig | dict[str, Any] | None = None,
        *,
        token_verifier: TokenVerifier | None = None,
    ) -> None:
        super().__init__(config)
        self._token_verifier = token_verifier

    def auth(self) -> AuthProvider | None:
        self._require("environment_url", "resource_id")
        self._require_one("base_url", "mcp_url")
        return ScalekitProvider(
            **self._kwargs(
                "environment_url",
                "resource_id",
                "base_url",
                "mcp_url",
                "client_id",
                "required_scopes",
                "scopes_supported",
                "resource_name",
                "resource_documentation",
            ),
            token_verifier=self._token_verifier,
        )
