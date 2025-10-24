"""Unit tests for Auth0 OAuth provider."""

import os
from unittest.mock import patch

import pytest

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0Provider, Auth0ProviderSettings
from fastmcp.server.auth.providers.jwt import JWTVerifier

TEST_CONFIG_URL = "https://example.com/.well-known/openid-configuration"
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_AUDIENCE = "test-audience"
TEST_BASE_URL = "https://example.com:8000/"
TEST_REDIRECT_PATH = "/test/callback"
TEST_REQUIRED_SCOPES = ["openid", "email"]


@pytest.fixture
def valid_oidc_configuration_dict():
    """Create a valid OIDC configuration dict for testing."""
    return {
        "issuer": "https://example.com",
        "authorization_endpoint": "https://example.com/authorize",
        "token_endpoint": "https://example.com/oauth/token",
        "jwks_uri": "https://example.com/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


class TestAuth0ProviderSettings:
    """Test settings for Auth0 OAuth provider."""

    def test_settings_from_env_vars(self):
        """Test that settings can be loaded from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AUTH0_CONFIG_URL": TEST_CONFIG_URL,
                "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_ID": TEST_CLIENT_ID,
                "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_SECRET": TEST_CLIENT_SECRET,
                "FASTMCP_SERVER_AUTH_AUTH0_AUDIENCE": TEST_AUDIENCE,
                "FASTMCP_SERVER_AUTH_AUTH0_BASE_URL": TEST_BASE_URL,
                "FASTMCP_SERVER_AUTH_AUTH0_REDIRECT_PATH": TEST_REDIRECT_PATH,
                "FASTMCP_SERVER_AUTH_AUTH0_REQUIRED_SCOPES": ",".join(
                    TEST_REQUIRED_SCOPES
                ),
                "FASTMCP_SERVER_AUTH_AUTH0_JWT_SIGNING_KEY": "test-secret",
            },
        ):
            settings = Auth0ProviderSettings()

            assert str(settings.config_url) == TEST_CONFIG_URL
            assert settings.client_id == TEST_CLIENT_ID
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == TEST_CLIENT_SECRET
            )
            assert settings.audience == TEST_AUDIENCE
            assert str(settings.base_url) == TEST_BASE_URL
            assert settings.redirect_path == TEST_REDIRECT_PATH
            assert settings.required_scopes == TEST_REQUIRED_SCOPES

    def test_settings_explicit_override_env(self):
        """Test that explicit settings override environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_ID": TEST_CLIENT_ID,
                "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_SECRET": TEST_CLIENT_SECRET,
            },
        ):
            settings = Auth0ProviderSettings.model_validate(
                {
                    "client_id": "explicit_client_id",
                    "client_secret": "explicit_secret",
                }
            )

            assert settings.client_id == "explicit_client_id"
            assert (
                settings.client_secret
                and settings.client_secret.get_secret_value() == "explicit_secret"
            )


class TestAuth0Provider:
    """Test Auth0Provider initialization."""

    def test_init_with_explicit_params(self, valid_oidc_configuration_dict):
        """Test initialization with explicit parameters."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            provider = Auth0Provider(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                audience=TEST_AUDIENCE,
                base_url=TEST_BASE_URL,
                redirect_path=TEST_REDIRECT_PATH,
                required_scopes=TEST_REQUIRED_SCOPES,
                jwt_signing_key="test-secret",
            )

            mock_get.assert_called_once()

            call_args = mock_get.call_args
            assert str(call_args[0][0]) == TEST_CONFIG_URL

            assert provider._upstream_client_id == TEST_CLIENT_ID
            assert (
                provider._upstream_client_secret.get_secret_value()
                == TEST_CLIENT_SECRET
            )

            assert isinstance(provider._token_validator, JWTVerifier)
            assert provider._token_validator.audience == TEST_AUDIENCE

            assert str(provider.base_url) == TEST_BASE_URL
            assert provider._redirect_path == TEST_REDIRECT_PATH
            assert provider._token_validator.required_scopes == TEST_REQUIRED_SCOPES

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "openid,email",
            '["openid", "email"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env, valid_oidc_configuration_dict):
        """Test initialization with environment variables."""
        with (
            patch.dict(
                os.environ,
                {
                    "FASTMCP_SERVER_AUTH_AUTH0_CONFIG_URL": TEST_CONFIG_URL,
                    "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_ID": TEST_CLIENT_ID,
                    "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_SECRET": TEST_CLIENT_SECRET,
                    "FASTMCP_SERVER_AUTH_AUTH0_AUDIENCE": TEST_AUDIENCE,
                    "FASTMCP_SERVER_AUTH_AUTH0_BASE_URL": TEST_BASE_URL,
                    "FASTMCP_SERVER_AUTH_AUTH0_REQUIRED_SCOPES": scopes_env,
                    "FASTMCP_SERVER_AUTH_AUTH0_JWT_SIGNING_KEY": "test-secret",
                },
            ),
            patch(
                "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
            ) as mock_get,
        ):
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            provider = Auth0Provider()

            mock_get.assert_called_once()

            call_args = mock_get.call_args
            assert str(call_args[0][0]) == TEST_CONFIG_URL

            assert provider._upstream_client_id == TEST_CLIENT_ID
            assert (
                provider._upstream_client_secret.get_secret_value()
                == TEST_CLIENT_SECRET
            )

            assert isinstance(provider._token_validator, JWTVerifier)
            assert provider._token_validator.audience == TEST_AUDIENCE

            assert str(provider.base_url) == TEST_BASE_URL
            assert provider._token_validator.required_scopes == TEST_REQUIRED_SCOPES

    def test_init_explicit_overrides_env(self, valid_oidc_configuration_dict):
        """Test that explicit parameters override environment variables."""
        with (
            patch.dict(
                os.environ,
                {
                    "FASTMCP_SERVER_AUTH_AUTH0_CONFIG_URL": TEST_CONFIG_URL,
                    "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_ID": TEST_CLIENT_ID,
                    "FASTMCP_SERVER_AUTH_AUTH0_CLIENT_SECRET": TEST_CLIENT_SECRET,
                    "FASTMCP_SERVER_AUTH_AUTH0_AUDIENCE": TEST_AUDIENCE,
                    "FASTMCP_SERVER_AUTH_AUTH0_BASE_URL": TEST_BASE_URL,
                    "FASTMCP_SERVER_AUTH_AUTH0_JWT_SIGNING_KEY": "test-secret",
                },
            ),
            patch(
                "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
            ) as mock_get,
        ):
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            provider = Auth0Provider(
                client_id="explicit_client",
                client_secret="explicit_secret",
            )

            assert provider._upstream_client_id == "explicit_client"
            assert (
                provider._upstream_client_secret.get_secret_value() == "explicit_secret"
            )

    def test_init_missing_config_url_raises_error(self):
        """Test that missing config_url raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="config_url is required"):
                Auth0Provider()

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_id is required"):
                Auth0Provider(config_url=TEST_CONFIG_URL)

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="client_secret is required"):
                Auth0Provider(config_url=TEST_CONFIG_URL, client_id=TEST_CLIENT_ID)

    def test_init_missing_audience_raises_error(self):
        """Test that missing audience raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="audience is required"):
                Auth0Provider(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    jwt_signing_key="test-secret",
                )

    def test_init_missing_base_url_raises_error(self):
        """Test that missing base_url raises ValueError."""
        # Clear environment variables to test proper error handling
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="base_url is required"):
                Auth0Provider(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    audience=TEST_AUDIENCE,
                    jwt_signing_key="test-secret",
                )

    def test_init_defaults(self, valid_oidc_configuration_dict):
        """Test that default values are applied correctly."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            provider = Auth0Provider(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                audience=TEST_AUDIENCE,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
            )

            # Check defaults
            assert str(provider.base_url) == TEST_BASE_URL
            assert provider._redirect_path == "/auth/callback"
            assert provider._token_validator.required_scopes == ["openid"]
