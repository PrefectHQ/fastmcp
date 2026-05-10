"""AWS Cognito auth plugin."""

from __future__ import annotations

from typing import Any, ClassVar

from key_value.aio.protocols import AsyncKeyValue

from fastmcp.server.auth import AuthProvider
from fastmcp.server.plugins.auth._base import AuthPlugin, OAuthProxyConfig
from fastmcp.server.plugins.auth.aws.provider import AWSCognitoProvider
from fastmcp.server.plugins.base import PluginMeta


class AWSCognitoAuthConfig(OAuthProxyConfig):
    """Config model for the AWS Cognito auth plugin."""

    user_pool_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    aws_region: str = "eu-central-1"
    redirect_path: str | None = "/auth/callback"


class AWSCognitoAuth(AuthPlugin[AWSCognitoAuthConfig]):
    """Contribute an `AWSCognitoProvider` as the server's auth provider."""

    Config: ClassVar[type[AWSCognitoAuthConfig]] = AWSCognitoAuthConfig

    meta = PluginMeta(name="aws-cognito-auth")

    def __init__(
        self,
        config: AWSCognitoAuthConfig | dict[str, Any] | None = None,
        *,
        client_storage: AsyncKeyValue | None = None,
    ) -> None:
        super().__init__(config)
        self._client_storage = client_storage

    def auth(self) -> AuthProvider | None:
        self._require("user_pool_id", "client_id", "client_secret", "base_url")
        return AWSCognitoProvider(
            **self._kwargs(
                "user_pool_id",
                "client_id",
                "client_secret",
                "base_url",
                "resource_base_url",
                "aws_region",
                "issuer_url",
                "redirect_path",
                "required_scopes",
                "allowed_client_redirect_uris",
                "jwt_signing_key",
                "require_authorization_consent",
                "consent_csp_policy",
                "forward_resource",
            ),
            client_storage=self._client_storage,
        )
