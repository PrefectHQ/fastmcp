"""Unit tests for OCI OAuth provider."""

from unittest.mock import patch

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.oci import OCIProvider

TEST_DOMAIN = "idcs-test.identity.oraclecloud.com"
TEST_CONFIG_URL = f"https://{TEST_DOMAIN}/.well-known/openid-configuration"
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_AUDIENCE = "test-audience"
TEST_BASE_URL = "https://example.com:8000/"
TEST_REDIRECT_PATH = "/test/callback"
TEST_REQUIRED_SCOPES = ["openid", "profile", "email"]


@pytest.fixture
def valid_oidc_configuration_dict():
    """Create a valid OCI OIDC configuration dict for testing."""
    return {
        "issuer": "https://identity.oraclecloud.com/",
        "authorization_endpoint": f"https://{TEST_DOMAIN}/oauth2/v1/authorize",
        "token_endpoint": f"https://{TEST_DOMAIN}/oauth2/v1/token",
        "jwks_uri": f"https://{TEST_DOMAIN}/admin/v1/SigningCert/jwk",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


class TestOCIProvider:
    """Test OCIProvider initialization."""

    def test_init_with_explicit_params(self, valid_oidc_configuration_dict):
        """Test initialization with explicit parameters."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            provider = OCIProvider(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                audience=TEST_AUDIENCE,
                base_url=TEST_BASE_URL,
                redirect_path=TEST_REDIRECT_PATH,
                required_scopes=TEST_REQUIRED_SCOPES,
                client_storage=MemoryStore(),
                jwt_signing_key="test-secret-key",
            )

            mock_get.assert_called_once()

            call_args = mock_get.call_args
            assert str(call_args[0][0]) == TEST_CONFIG_URL

            assert provider._upstream_client_id == TEST_CLIENT_ID
            assert provider._upstream_client_secret is not None
            assert (
                provider._upstream_client_secret.get_secret_value()
                == TEST_CLIENT_SECRET
            )

            assert (
                provider._upstream_authorization_endpoint
                == f"https://{TEST_DOMAIN}/oauth2/v1/authorize"
            )
            assert (
                provider._upstream_token_endpoint
                == f"https://{TEST_DOMAIN}/oauth2/v1/token"
            )

            assert isinstance(provider._token_validator, JWTVerifier)
            assert provider._token_validator.audience == TEST_AUDIENCE

            assert str(provider.base_url) == TEST_BASE_URL
            assert provider._redirect_path == TEST_REDIRECT_PATH
            assert provider._token_validator.required_scopes == TEST_REQUIRED_SCOPES
