"""Supabase auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.plugins.auth._base import Algorithm, AuthPlugin, RemoteAuthConfig
from fastmcp.server.plugins.auth.supabase.provider import SupabaseProvider
from fastmcp.server.plugins.base import PluginMeta


class SupabaseAuthConfig(RemoteAuthConfig):
    """Config model for the Supabase auth plugin."""

    project_url: AnyHttpUrl | str | None = None
    auth_route: str = "/auth/v1"
    algorithm: Algorithm = "ES256"


class SupabaseAuth(AuthPlugin[SupabaseAuthConfig]):
    """Contribute a `SupabaseProvider` as the server's auth provider."""

    Config: ClassVar[type[SupabaseAuthConfig]] = SupabaseAuthConfig

    meta = PluginMeta(name="supabase-auth")

    def __init__(
        self,
        config: SupabaseAuthConfig | dict[str, Any] | None = None,
        *,
        token_verifier: TokenVerifier | None = None,
    ) -> None:
        super().__init__(config)
        self._token_verifier = token_verifier

    def auth(self) -> AuthProvider | None:
        self._require("project_url", "base_url")
        return SupabaseProvider(
            **self._kwargs(
                "project_url",
                "base_url",
                "auth_route",
                "algorithm",
                "required_scopes",
                "scopes_supported",
                "resource_name",
                "resource_documentation",
            ),
            token_verifier=self._token_verifier,
        )
