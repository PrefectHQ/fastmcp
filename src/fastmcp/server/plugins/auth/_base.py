"""Shared primitives for first-party auth plugins."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import AnyHttpUrl, BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin

ConsentMode = bool | Literal["remember", "external"]
Algorithm = Literal["RS256", "ES256"]
ConfigT = TypeVar("ConfigT", bound=BaseModel)


class AuthPlugin(Plugin[ConfigT], Generic[ConfigT]):
    def _require(self, *fields: str) -> None:
        missing = [field for field in fields if getattr(self.config, field) is None]
        if missing:
            names = ", ".join(f"`{field}`" for field in missing)
            raise ValueError(f"{type(self).__name__} requires {names}.")

    def _require_one(self, *fields: str) -> None:
        if not any(getattr(self.config, field) is not None for field in fields):
            names = " or ".join(f"`{field}`" for field in fields)
            raise ValueError(f"{type(self).__name__} requires {names}.")

    def _kwargs(self, *fields: str) -> dict[str, Any]:
        return {
            field: getattr(self.config, field)
            for field in fields
            if getattr(self.config, field) is not None
        }


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthProxyConfig(PluginConfig):
    base_url: AnyHttpUrl | str | None = None
    resource_base_url: AnyHttpUrl | str | None = None
    issuer_url: AnyHttpUrl | str | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    allowed_client_redirect_uris: list[str] | None = None
    jwt_signing_key: str | None = None
    require_authorization_consent: ConsentMode = True
    consent_csp_policy: str | None = None
    forward_resource: bool = True


class OAuthProviderConfig(OAuthProxyConfig):
    client_id: str | None = None
    client_secret: str | None = None
    timeout_seconds: int = 10
    enable_cimd: bool = True


class RemoteAuthConfig(PluginConfig):
    base_url: AnyHttpUrl | str | None = None
    required_scopes: list[str] | None = None
    scopes_supported: list[str] | None = None
    resource_name: str | None = None
    resource_documentation: AnyHttpUrl | None = None
