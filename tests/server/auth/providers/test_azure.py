"""Tests for Azure (Microsoft Entra) OAuth provider."""

import os
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from fastmcp.server.auth.providers.azure import AzureProvider


class TestAzureProvider:
    """Test Azure OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test AzureProvider initialization with explicit parameters."""
        provider = AzureProvider(
            client_id="12345678-1234-1234-1234-123456789012",
            client_secret="azure_secret_123",
            tenant_id="87654321-4321-4321-4321-210987654321",
            base_url="https://myserver.com",
            required_scopes=["User.Read", "Mail.Read"],
        )

        assert provider._upstream_client_id == "12345678-1234-1234-1234-123456789012"
        assert provider._upstream_client_secret.get_secret_value() == "azure_secret_123"
        assert str(provider.base_url) == "https://myserver.com/"
        # Check tenant is in the endpoints
        parsed_auth = urlparse(provider._upstream_authorization_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_auth.path
        parsed_token = urlparse(provider._upstream_token_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_token.path

    @pytest.mark.parametrize(
        "scopes_env",
        [
            "User.Read,Calendar.Read",
            '["User.Read", "Calendar.Read"]',
        ],
    )
    def test_init_with_env_vars(self, scopes_env):
        """Test AzureProvider initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "FASTMCP_SERVER_AUTH_AZURE_CLIENT_ID": "env-client-id",
                "FASTMCP_SERVER_AUTH_AZURE_CLIENT_SECRET": "env-secret",
                "FASTMCP_SERVER_AUTH_AZURE_TENANT_ID": "env-tenant-id",
                "FASTMCP_SERVER_AUTH_AZURE_BASE_URL": "https://envserver.com",
                "FASTMCP_SERVER_AUTH_AZURE_REQUIRED_SCOPES": scopes_env,
            },
        ):
            provider = AzureProvider()

            assert provider._upstream_client_id == "env-client-id"
            assert provider._upstream_client_secret.get_secret_value() == "env-secret"
            assert str(provider.base_url) == "https://envserver.com/"
            assert provider._token_validator.required_scopes == [
                "User.Read",
                "Calendar.Read",
            ]
            # Check tenant is in the endpoints
            parsed_auth = urlparse(provider._upstream_authorization_endpoint)
            assert "env-tenant-id" in parsed_auth.path
            parsed_token = urlparse(provider._upstream_token_endpoint)
            assert "env-tenant-id" in parsed_token.path

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        with pytest.raises(ValueError, match="client_id is required"):
            AzureProvider(
                client_secret="test_secret",
                tenant_id="test-tenant",
            )

    def test_init_missing_client_secret_raises_error(self):
        """Test that missing client_secret raises ValueError."""
        with pytest.raises(ValueError, match="client_secret is required"):
            AzureProvider(
                client_id="test_client",
                tenant_id="test-tenant",
            )

    def test_init_missing_tenant_id_raises_error(self):
        """Test that missing tenant_id raises ValueError."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            AzureProvider(
                client_id="test_client",
                client_secret="test_secret",
            )

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
        )

        # Check defaults
        assert provider.base_url is None
        assert provider._redirect_path == "/auth/callback"
        # Azure provider defaults are set but we can't easily verify them without accessing internals

    def test_oauth_endpoints_configured_correctly(self):
        """Test that OAuth endpoints are configured correctly."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant-id",
            base_url="https://myserver.com",
        )

        # Check that endpoints use the correct Azure OAuth2 v2.0 endpoints with tenant
        assert (
            provider._upstream_authorization_endpoint
            == "https://login.microsoftonline.com/my-tenant-id/oauth2/v2.0/authorize"
        )
        assert (
            provider._upstream_token_endpoint
            == "https://login.microsoftonline.com/my-tenant-id/oauth2/v2.0/token"
        )
        assert (
            provider._upstream_revocation_endpoint is None
        )  # Azure doesn't support revocation

    def test_special_tenant_values(self):
        """Test that special tenant values are accepted."""
        # Test with "organizations"
        provider1 = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="organizations",
        )
        parsed = urlparse(provider1._upstream_authorization_endpoint)
        assert "/organizations/" in parsed.path

        # Test with "consumers"
        provider2 = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="consumers",
        )
        parsed = urlparse(provider2._upstream_authorization_endpoint)
        assert "/consumers/" in parsed.path

    def test_azure_specific_scopes(self):
        """Test handling of Azure-specific scope formats."""
        # Just test that the provider accepts Azure-specific scopes without error
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            required_scopes=[
                "User.Read",
                "Mail.Read",
                "Calendar.ReadWrite",
                "openid",
                "profile",
            ],
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None
