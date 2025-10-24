"""Unit tests for AWS Cognito OAuth provider."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from fastmcp.server.auth.providers.aws import (
    AWSCognitoProvider,
    AWSCognitoProviderSettings,
)


@contextmanager
def mock_cognito_oidc_discovery():
    """Context manager to mock AWS Cognito OIDC discovery endpoint."""
    mock_oidc_config = {
        "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX",
        "authorization_endpoint": "https://test.auth.us-east-1.amazoncognito.com/oauth2/authorize",
        "token_endpoint": "https://test.auth.us-east-1.amazoncognito.com/oauth2/token",
        "jwks_uri": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX/.well-known/jwks.json",
        "userinfo_endpoint": "https://test.auth.us-east-1.amazoncognito.com/oauth2/userInfo",
        "response_types_supported": ["code", "token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "phone", "profile"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
    }

    with patch("httpx.get") as mock_get:
        mock_response = mock_get.return_value
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_oidc_config
        yield


class TestAWSCognitoProviderSettings:
    """Test settings for AWS Cognito OAuth provider."""

    def test_settings_from_env_vars(self):
        """Test that settings can be loaded from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID": "us-east-1_XXXXXXXXX",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION": "us-east-1",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_BASE_URL": "https://example.com",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_REDIRECT_PATH": "/custom/callback",
            },
        ):
            settings = AWSCognitoProviderSettings()

            assert settings.user_pool_id == "us-east-1_XXXXXXXXX"
            assert settings.aws_region == "us-east-1"
            assert settings.client_id == "env_client_id"
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == "env_secret"
            )
            assert settings.base_url == "https://example.com"
            assert settings.redirect_path == "/custom/callback"

    def test_settings_explicit_override_env(self):
        """Test that explicit settings override environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID": "env_pool_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET": "env_secret",
            },
        ):
            settings = AWSCognitoProviderSettings.model_validate(
                {
                    "user_pool_id": "explicit_pool_id",
                    "client_id": "explicit_client_id",
                    "client_secret": "explicit_secret",
                }
            )

            assert settings.user_pool_id == "explicit_pool_id"
            assert settings.client_id == "explicit_client_id"
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == "explicit_secret"
            )


class TestAWSCognitoProvider:
    """Test AWSCognitoProvider initialization."""

    def test_init_with_explicit_params(self):
        """Test initialization with explicit parameters."""
        with mock_cognito_oidc_discovery():
            provider = AWSCognitoProvider(
                user_pool_id="us-east-1_XXXXXXXXX",
                aws_region="us-east-1",
                client_id="test_client",
                client_secret="test_secret",
                base_url="https://example.com",
                redirect_path="/custom/callback",
                required_scopes=["openid", "email"],
                jwt_signing_key="test-secret",
            )

            # Check that the provider was initialized correctly
            assert provider._upstream_client_id == "test_client"
            assert provider._upstream_client_secret.get_secret_value() == "test_secret"
            assert (
                str(provider.base_url) == "https://example.com/"
            )  # URLs get normalized with trailing slash
            assert provider._redirect_path == "/custom/callback"
            # OIDC provider should have discovered the endpoints automatically
            assert (
                provider._upstream_authorization_endpoint
                == "https://test.auth.us-east-1.amazoncognito.com/oauth2/authorize"
            )
            assert (
                provider._upstream_token_endpoint
                == "https://test.auth.us-east-1.amazoncognito.com/oauth2/token"
            )

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "openid,email",
            '["openid", "email"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test initialization with environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID": "us-east-1_XXXXXXXXX",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION": "us-east-1",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_BASE_URL": "https://env-example.com",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_REQUIRED_SCOPES": scopes_env,
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            with mock_cognito_oidc_discovery():
                provider = AWSCognitoProvider()

                assert provider._upstream_client_id == "env_client_id"
                assert (
                    provider._upstream_client_secret.get_secret_value() == "env_secret"
                )
                assert str(provider.base_url) == "https://env-example.com/"
                assert provider._token_validator.required_scopes == ["openid", "email"]

    def test_init_explicit_overrides_env(self):
        """Test that explicit parameters override environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID": "env_pool_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID": "env_client_id",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET": "env_secret",
                "FASTMCP_SERVER_AUTH_AWS_COGNITO_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            with mock_cognito_oidc_discovery():
                provider = AWSCognitoProvider(
                    user_pool_id="explicit_pool_id",
                    client_id="explicit_client",
                    client_secret="explicit_secret",
                    base_url="https://example.com",
                    jwt_signing_key="test-secret",
                )

                assert provider._upstream_client_id == "explicit_client"
                assert (
                    provider._upstream_client_secret.get_secret_value()
                    == "explicit_secret"
                )
                # OIDC discovery should have configured the endpoints automatically
                assert provider._upstream_authorization_endpoint is not None

    def test_init_missing_user_pool_id_raises_error(self):
        """Test that missing user_pool_id raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="user_pool_id is required"):
                AWSCognitoProvider(
                    client_id="test_client",
                    client_secret="test_secret",
                )

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_id is required"):
                AWSCognitoProvider(
                    user_pool_id="us-east-1_XXXXXXXXX",
                    client_secret="test_secret",
                )

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_secret is required"):
                AWSCognitoProvider(
                    user_pool_id="us-east-1_XXXXXXXXX",
                    client_id="test_client",
                )

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        with mock_cognito_oidc_discovery():
            provider = AWSCognitoProvider(
                user_pool_id="us-east-1_XXXXXXXXX",
                client_id="test_client",
                client_secret="test_secret",
                base_url="https://example.com",
                jwt_signing_key="test-secret",
            )

            # Check defaults
            assert str(provider.base_url) == "https://example.com/"
            assert provider._redirect_path == "/auth/callback"
            assert provider._token_validator.required_scopes == ["openid"]
            assert provider.aws_region == "eu-central-1"

    def test_oidc_discovery_integration(self):
        """Test that OIDC discovery endpoints are used correctly."""
        with mock_cognito_oidc_discovery():
            provider = AWSCognitoProvider(
                user_pool_id="us-west-2_YYYYYYYY",
                aws_region="us-west-2",
                client_id="test_client",
                client_secret="test_secret",
                base_url="https://example.com",
                jwt_signing_key="test-secret",
            )

            # OIDC discovery should have configured the endpoints automatically
            assert provider._upstream_authorization_endpoint is not None
            assert provider._upstream_token_endpoint is not None
            assert "amazoncognito.com" in provider._upstream_authorization_endpoint


# Token verification functionality is now tested as part of the OIDC provider integration
# The CognitoTokenVerifier class is an internal implementation detail
