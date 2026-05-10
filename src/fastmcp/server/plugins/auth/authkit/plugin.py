"""WorkOS AuthKit auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.plugins.auth._base import AuthPlugin, RemoteAuthConfig
from fastmcp.server.plugins.auth.authkit.provider import AuthKitProvider
from fastmcp.server.plugins.base import PluginMeta


class AuthKitAuthConfig(RemoteAuthConfig):
    """Config model for the WorkOS AuthKit auth plugin."""

    authkit_domain: AnyHttpUrl | str | None = None
    resource_base_url: AnyHttpUrl | str | None = None


class AuthKitAuth(AuthPlugin[AuthKitAuthConfig]):
    """Contribute an `AuthKitProvider` as the server's auth provider."""

    Config: ClassVar[type[AuthKitAuthConfig]] = AuthKitAuthConfig

    meta = PluginMeta(name="authkit-auth")

    def __init__(
        self,
        config: AuthKitAuthConfig | dict[str, Any] | None = None,
        *,
        token_verifier: TokenVerifier | None = None,
    ) -> None:
        super().__init__(config)
        self._token_verifier = token_verifier

    def auth(self) -> AuthProvider | None:
        self._require("authkit_domain", "base_url")
        return AuthKitProvider(
            **self._kwargs(
                "authkit_domain",
                "base_url",
                "resource_base_url",
                "required_scopes",
                "scopes_supported",
                "resource_name",
                "resource_documentation",
            ),
            token_verifier=self._token_verifier,
        )
