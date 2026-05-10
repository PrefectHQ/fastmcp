"""Keycloak auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.plugins.auth._base import AuthPlugin, PluginConfig
from fastmcp.server.plugins.auth.keycloak.provider import KeycloakAuthProvider
from fastmcp.server.plugins.base import PluginMeta


class KeycloakAuthConfig(PluginConfig):
    """Config model for the Keycloak auth plugin."""

    realm_url: AnyHttpUrl | str | None = None
    base_url: AnyHttpUrl | str | None = None
    required_scopes: list[str] | str | None = None
    audience: str | list[str] | None = None


class KeycloakAuth(AuthPlugin[KeycloakAuthConfig]):
    """Contribute a `KeycloakAuthProvider` as the server's auth provider."""

    Config: ClassVar[type[KeycloakAuthConfig]] = KeycloakAuthConfig

    meta = PluginMeta(name="keycloak-auth")

    def __init__(
        self,
        config: KeycloakAuthConfig | dict[str, Any] | None = None,
        *,
        token_verifier: TokenVerifier | None = None,
    ) -> None:
        super().__init__(config)
        self._token_verifier = token_verifier

    def auth(self) -> AuthProvider | None:
        self._require("realm_url", "base_url")
        return KeycloakAuthProvider(
            **self._kwargs("realm_url", "base_url", "required_scopes", "audience"),
            token_verifier=self._token_verifier,
        )
