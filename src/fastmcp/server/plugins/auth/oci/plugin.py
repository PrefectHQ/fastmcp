"""OCI auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from key_value.aio.protocols import AsyncKeyValue
from pydantic import AnyHttpUrl

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProxyConfig
from fastmcp.server.plugins.auth.oci.provider import OCIProvider
from fastmcp.server.plugins.base import PluginMeta


class OCIAuthConfig(OAuthProxyConfig):
    """Config model for the OCI auth plugin."""

    config_url: AnyHttpUrl | str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    audience: str | None = None


class OCIAuth(AuthPlugin[OCIAuthConfig]):
    """Contribute an `OCIProvider` as the server's auth provider."""

    Config: ClassVar[type[OCIAuthConfig]] = OCIAuthConfig

    meta = PluginMeta(name="oci-auth")

    def __init__(
        self,
        config: OCIAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage

    def auth(self) -> AuthProvider | None:
        self._require("config_url", "client_id", "client_secret", "base_url")
        return OCIProvider(
            **self._kwargs(
                "config_url",
                "client_id",
                "client_secret",
                "base_url",
                "resource_base_url",
                "audience",
                "issuer_url",
                "required_scopes",
                "redirect_path",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
            ),
            client_storage=self._client_storage,
        )
