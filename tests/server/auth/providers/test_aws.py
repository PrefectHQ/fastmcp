"""Unit tests for AWS Cognito OAuth provider."""

from contextlib import contextmanager
from unittest.mock import patch

from fastmcp.server.auth.providers.aws import (
    AWSCognitoProvider,
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
