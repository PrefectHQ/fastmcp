"""Tests for Azure (Microsoft Entra) OAuth provider."""

import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier


class TestAzureProvider:
    """Test Azure OAuth provider functionality."""

    def test_init_with_explicit_params(self):
        """Test AzureProvider initialization with explicit parameters."""
        provider = AzureProvider(
            client_id="12345678-1234-1234-1234-123456789012",
            client_secret="azure_secret_123",
            tenant_id="87654321-4321-4321-4321-210987654321",
            base_url="https://myserver.com",
            required_scopes=["read", "write"],
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
            "read,write",
            '["read", "write"]',
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
            # Scopes should be prefixed with identifier_uri in token validator
            assert provider._token_validator.required_scopes == [
                "api://env-client-id/read",
                "api://env-client-id/write",
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
            required_scopes=["read"],
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
            required_scopes=["read"],
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
            required_scopes=["read"],
        )
        parsed = urlparse(provider1._upstream_authorization_endpoint)
        assert "/organizations/" in parsed.path

        # Test with "consumers"
        provider2 = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="consumers",
            required_scopes=["read"],
        )
        parsed = urlparse(provider2._upstream_authorization_endpoint)
        assert "/consumers/" in parsed.path

    def test_azure_specific_scopes(self):
        """Test handling of custom API scope formats."""
        # Test that the provider accepts custom API scopes without error
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            required_scopes=[
                "read",
                "write",
                "admin",
            ],
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None
        # Scopes should be prefixed in token validator
        assert provider._token_validator.required_scopes == [
            "api://test_client/read",
            "api://test_client/write",
            "api://test_client/admin",
        ]

    def test_init_does_not_require_api_client_id_anymore(self):
        """API client ID is no longer required; audience is client_id."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            required_scopes=["read"],
        )
        assert provider is not None

    def test_init_with_custom_audience_uses_jwt_verifier(self):
        """When audience is provided, JWTVerifier is configured with JWKS and issuer."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            identifier_uri="api://my-api",
            required_scopes=[".default"],
        )

        assert provider._token_validator is not None
        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        assert verifier.jwks_uri is not None
        assert verifier.jwks_uri.startswith(
            "https://login.microsoftonline.com/my-tenant/discovery/v2.0/keys"
        )
        assert verifier.issuer == "https://login.microsoftonline.com/my-tenant/v2.0"
        assert verifier.audience == "test_client"
        # Scopes should be prefixed with identifier_uri
        assert verifier.required_scopes == ["api://my-api/.default"]

    @pytest.mark.asyncio
    async def test_authorize_filters_resource_and_accepts_prefixed_scopes(self):
        """authorize() should drop resource parameter and accept prefixed scopes from clients."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="common",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            base_url="https://srv.example",
        )

        await provider.register_client(
            OAuthClientInformationFull(
                client_id="dummy",
                client_secret="secret",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )
        )

        client = OAuthClientInformationFull(
            client_id="dummy",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=[
                "api://my-api/read",
                "api://my-api/profile",
            ],  # Client sends prefixed scopes from PRM
            state="abc",
            code_challenge="xyz",
            resource="https://should.be.ignored",
        )

        url = await provider.authorize(client, params)

        # Extract transaction ID from consent redirect
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert "txn_id" in qs, "Should redirect to consent page with transaction ID"
        txn_id = qs["txn_id"][0]

        # Verify transaction contains correct parameters (resource filtered, scopes prefixed)
        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert "api://my-api/read" in transaction.scopes
        assert "api://my-api/profile" in transaction.scopes
        # Azure provider filters resource parameter (not stored in transaction)
        assert transaction.resource is None

    @pytest.mark.asyncio
    async def test_authorize_appends_additional_scopes(self):
        """authorize() should append additional_authorize_scopes to the authorization request."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="common",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            base_url="https://srv.example",
            additional_authorize_scopes=["Mail.Read", "User.Read"],
        )

        await provider.register_client(
            OAuthClientInformationFull(
                client_id="dummy",
                client_secret="secret",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )
        )

        client = OAuthClientInformationFull(
            client_id="dummy",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=["api://my-api/read"],  # Client sends prefixed scopes from PRM
            state="abc",
            code_challenge="xyz",
        )

        url = await provider.authorize(client, params)

        # Extract transaction ID from consent redirect
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert "txn_id" in qs, "Should redirect to consent page with transaction ID"
        txn_id = qs["txn_id"][0]

        # Verify transaction contains correct scopes (prefixed + unprefixed additional)
        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert "api://my-api/read" in transaction.scopes
        assert "Mail.Read" in transaction.scopes
        assert "User.Read" in transaction.scopes
