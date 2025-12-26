"""Unit tests for Auth0 OAuth provider."""

from unittest.mock import patch

import pytest

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0Provider
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
