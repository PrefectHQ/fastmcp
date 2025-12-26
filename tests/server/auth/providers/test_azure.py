"""Tests for Azure (Microsoft Entra) OAuth provider."""

from urllib.parse import parse_qs, urlparse

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.providers.azure import OIDC_SCOPES, AzureProvider
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
            jwt_signing_key="test-secret",
        )

        assert provider._upstream_client_id == "12345678-1234-1234-1234-123456789012"
        assert provider._upstream_client_secret.get_secret_value() == "azure_secret_123"
        assert str(provider.base_url) == "https://myserver.com/"
        # Check tenant is in the endpoints
        parsed_auth = urlparse(provider._upstream_authorization_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_auth.path
        parsed_token = urlparse(provider._upstream_token_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_token.path

    def test_init_defaults(self):
        """Test that default values are applied correctly."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Check defaults
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
            jwt_signing_key="test_secret",
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
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )
        parsed = urlparse(provider1._upstream_authorization_endpoint)
        assert "/organizations/" in parsed.path

        # Test with "consumers"
        provider2 = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="consumers",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
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
            base_url="https://myserver.com",
            required_scopes=[
                "read",
                "write",
                "admin",
            ],
            jwt_signing_key="test-secret",
        )

        # Provider should initialize successfully with these scopes
        assert provider is not None
        # Scopes are stored unprefixed for token validation
        # (Azure returns unprefixed scopes in JWT tokens)
        assert provider._token_validator.required_scopes == [
            "read",
            "write",
            "admin",
        ]

    def test_init_does_not_require_api_client_id_anymore(self):
        """API client ID is no longer required; audience is client_id."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )
        assert provider is not None

    def test_init_with_custom_audience_uses_jwt_verifier(self):
        """When audience is provided, JWTVerifier is configured with JWKS and issuer."""
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=[".default"],
            jwt_signing_key="test-secret",
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
        # Scopes are stored unprefixed for token validation
        # (Azure returns unprefixed scopes like ".default" in JWT tokens)
        assert verifier.required_scopes == [".default"]

    async def test_authorize_filters_resource_and_stores_unprefixed_scopes(self):
        """authorize() should drop resource parameter and store unprefixed scopes for MCP clients."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="common",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            base_url="https://srv.example",
            jwt_signing_key="test-secret",
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
                "read",
                "write",
            ],  # Client sends unprefixed scopes (from PRM which advertises unprefixed)
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

        # Verify transaction stores UNPREFIXED scopes for MCP clients
        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert "read" in transaction.scopes
        assert "write" in transaction.scopes
        # Azure provider filters resource parameter (not stored in transaction)
        assert transaction.resource is None

        # Verify the upstream Azure URL will have PREFIXED scopes
        upstream_url = provider._build_upstream_authorize_url(
            txn_id, transaction.model_dump()
        )
        assert (
            "api%3A%2F%2Fmy-api%2Fread" in upstream_url
            or "api://my-api/read" in upstream_url
        )
        assert (
            "api%3A%2F%2Fmy-api%2Fwrite" in upstream_url
            or "api://my-api/write" in upstream_url
        )

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
            jwt_signing_key="test-secret",
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
            scopes=["read"],  # Client sends unprefixed scopes
            state="abc",
            code_challenge="xyz",
        )

        url = await provider.authorize(client, params)

        # Extract transaction ID from consent redirect
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert "txn_id" in qs, "Should redirect to consent page with transaction ID"
        txn_id = qs["txn_id"][0]

        # Verify transaction stores ONLY MCP scopes (unprefixed)
        # additional_authorize_scopes are NOT stored in transaction
        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        assert "read" in transaction.scopes
        assert "Mail.Read" not in transaction.scopes  # Not in transaction
        assert "User.Read" not in transaction.scopes  # Not in transaction

        # Verify upstream URL includes both MCP scopes (prefixed) AND additional Graph scopes
        upstream_url = provider._build_upstream_authorize_url(
            txn_id, transaction.model_dump()
        )
        assert (
            "api%3A%2F%2Fmy-api%2Fread" in upstream_url
            or "api://my-api/read" in upstream_url
        )
        assert "Mail.Read" in upstream_url
        assert "User.Read" in upstream_url

    def test_base_authority_defaults_to_public_cloud(self):
        """Test that base_authority defaults to login.microsoftonline.com."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        assert (
            provider._upstream_authorization_endpoint
            == "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize"
        )
        assert (
            provider._upstream_token_endpoint
            == "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token"
        )
        assert isinstance(provider._token_validator, JWTVerifier)
        assert (
            provider._token_validator.issuer
            == "https://login.microsoftonline.com/test-tenant/v2.0"
        )
        assert (
            provider._token_validator.jwks_uri
            == "https://login.microsoftonline.com/test-tenant/discovery/v2.0/keys"
        )

    def test_base_authority_azure_government(self):
        """Test Azure Government endpoints with login.microsoftonline.us."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="gov-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
        )

        assert (
            provider._upstream_authorization_endpoint
            == "https://login.microsoftonline.us/gov-tenant-id/oauth2/v2.0/authorize"
        )
        assert (
            provider._upstream_token_endpoint
            == "https://login.microsoftonline.us/gov-tenant-id/oauth2/v2.0/token"
        )
        assert isinstance(provider._token_validator, JWTVerifier)
        assert (
            provider._token_validator.issuer
            == "https://login.microsoftonline.us/gov-tenant-id/v2.0"
        )
        assert (
            provider._token_validator.jwks_uri
            == "https://login.microsoftonline.us/gov-tenant-id/discovery/v2.0/keys"
        )

    def test_base_authority_from_parameter(self):
        """Test that base_authority can be set via parameter."""
        provider = AzureProvider(
            client_id="env-client-id",
            client_secret="env-secret",
            tenant_id="env-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
        )

        assert (
            provider._upstream_authorization_endpoint
            == "https://login.microsoftonline.us/env-tenant-id/oauth2/v2.0/authorize"
        )
        assert (
            provider._upstream_token_endpoint
            == "https://login.microsoftonline.us/env-tenant-id/oauth2/v2.0/token"
        )
        assert isinstance(provider._token_validator, JWTVerifier)
        assert (
            provider._token_validator.issuer
            == "https://login.microsoftonline.us/env-tenant-id/v2.0"
        )
        assert (
            provider._token_validator.jwks_uri
            == "https://login.microsoftonline.us/env-tenant-id/discovery/v2.0/keys"
        )

    def test_base_authority_with_special_tenant_values(self):
        """Test that base_authority works with special tenant values like 'organizations'."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="organizations",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
        )

        parsed = urlparse(provider._upstream_authorization_endpoint)
        assert parsed.netloc == "login.microsoftonline.us"
        assert "/organizations/" in parsed.path

    def test_prepare_scopes_for_upstream_refresh_basic_prefixing(self):
        """Test that unprefixed scopes are correctly prefixed for Azure token refresh."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            jwt_signing_key="test-secret",
        )

        # Unprefixed scopes from storage should be prefixed
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert len(result) == 2

    def test_prepare_scopes_for_upstream_refresh_already_prefixed(self):
        """Test that already-prefixed scopes remain unchanged."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Already prefixed scopes should pass through unchanged
        result = provider._prepare_scopes_for_upstream_refresh(
            ["api://my-api/read", "api://other-api/admin"]
        )

        assert "api://my-api/read" in result
        assert "api://other-api/admin" in result
        assert len(result) == 2

    def test_prepare_scopes_for_upstream_refresh_with_additional_scopes(self):
        """Test that additional_authorize_scopes are added during token refresh."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=[
                "User.Read",
                "openid",
                "profile",
                "offline_access",
            ],
            jwt_signing_key="test-secret",
        )

        # Base scopes should be prefixed, additional scopes appended
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "User.Read" in result
        assert "openid" in result
        assert "profile" in result
        assert "offline_access" in result
        assert len(result) == 6

    def test_prepare_scopes_for_upstream_refresh_filters_duplicate_additional_scopes(
        self,
    ):
        """Test that accidentally stored additional_authorize_scopes are filtered out."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["User.Read", "openid"],
            jwt_signing_key="test-secret",
        )

        # If additional scopes were accidentally stored, they should be filtered
        # to prevent accumulation
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "User.Read", "openid"]
        )

        # Should have: api://my-api/read (prefixed) + User.Read + openid (added once)
        assert "api://my-api/read" in result
        assert result.count("User.Read") == 1
        assert result.count("openid") == 1
        assert len(result) == 3

    def test_prepare_scopes_for_upstream_refresh_mixed_scopes(self):
        """Test mixed scenario with both prefixed and unprefixed scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["User.Read"],
            jwt_signing_key="test-secret",
        )

        # Mix of prefixed and unprefixed scopes
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "api://other-api/admin", "write"]
        )

        assert "api://my-api/read" in result
        assert "api://other-api/admin" in result  # Already prefixed, unchanged
        assert "api://my-api/write" in result
        assert "User.Read" in result
        assert len(result) == 4

    def test_prepare_scopes_for_upstream_refresh_scope_with_slash(self):
        """Test that scopes containing '/' are not prefixed."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Scopes with "/" should not be prefixed (already fully qualified)
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "https://graph.microsoft.com/.default"]
        )

        assert "api://my-api/read" in result
        assert (
            "https://graph.microsoft.com/.default" in result
        )  # Not prefixed (contains ://)

    def test_prepare_scopes_for_upstream_refresh_empty_scopes(self):
        """Test behavior with empty scopes list."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["User.Read", "openid"],
            jwt_signing_key="test-secret",
        )

        # Empty scopes should still add additional_authorize_scopes
        result = provider._prepare_scopes_for_upstream_refresh([])

        assert "User.Read" in result
        assert "openid" in result
        assert len(result) == 2

    def test_prepare_scopes_for_upstream_refresh_no_additional_scopes(self):
        """Test behavior when no additional_authorize_scopes are configured."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Should only prefix base scopes, no additional scopes added
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert len(result) == 2

    def test_prepare_scopes_for_upstream_refresh_deduplicates_scopes(self):
        """Test that duplicate scopes are deduplicated while preserving order."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["User.Read", "openid"],
            jwt_signing_key="test-secret",
        )

        # Test with duplicate base scopes and duplicate additional scopes
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "write", "read", "User.Read", "openid"]
        )

        # Should have deduplicated results in order
        assert result == [
            "api://my-api/read",
            "api://my-api/write",
            "User.Read",
            "openid",
        ]
        assert len(result) == 4

    def test_prepare_scopes_for_upstream_refresh_deduplicates_prefixed_variants(self):
        """Test that both prefixed and unprefixed variants are deduplicated."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Test with both prefixed and unprefixed variants of same scope
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "api://my-api/read", "write"]
        )

        # Should deduplicate - first occurrence wins (api://my-api/read from "read")
        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        # Should only have 2 items (read processed twice, but deduplicated)
        assert len(result) == 2
        assert result.count("api://my-api/read") == 1


class TestOIDCScopeHandling:
    """Tests for OIDC scope handling in Azure provider.

    Azure access tokens do NOT include OIDC scopes (openid, profile, email,
    offline_access) in the `scp` claim - they're only used during authorization.
    These tests verify that:
    1. OIDC scopes are never prefixed with identifier_uri
    2. OIDC scopes are filtered from token validation
    3. OIDC scopes are still advertised to clients via valid_scopes
    """

    def test_oidc_scopes_constant(self):
        """Verify OIDC_SCOPES contains the standard OIDC scopes."""
        assert OIDC_SCOPES == {"openid", "profile", "email", "offline_access"}

    def test_prefix_scopes_does_not_prefix_oidc_scopes(self):
        """Test that _prefix_scopes_for_azure never prefixes OIDC scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # All OIDC scopes should pass through unchanged
        result = provider._prefix_scopes_for_azure(
            ["openid", "profile", "email", "offline_access"]
        )

        assert result == ["openid", "profile", "email", "offline_access"]

    def test_prefix_scopes_mixed_oidc_and_custom(self):
        """Test prefixing with a mix of OIDC and custom scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        result = provider._prefix_scopes_for_azure(
            ["read", "openid", "write", "profile"]
        )

        # Custom scopes should be prefixed, OIDC scopes should not
        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "openid" in result
        assert "profile" in result
        # Verify OIDC scopes are NOT prefixed
        assert "api://my-api/openid" not in result
        assert "api://my-api/profile" not in result

    def test_prefix_scopes_dot_notation_gets_prefixed(self):
        """Test that dot-notation scopes get prefixed (use additional_authorize_scopes for Graph)."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Dot-notation scopes ARE prefixed - use additional_authorize_scopes for Graph
        # or fully-qualified format like https://graph.microsoft.com/User.Read
        result = provider._prefix_scopes_for_azure(["my.scope", "admin.read"])

        assert result == ["api://my-api/my.scope", "api://my-api/admin.read"]

    def test_prefix_scopes_fully_qualified_graph_not_prefixed(self):
        """Test that fully-qualified Graph scopes are not prefixed."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        result = provider._prefix_scopes_for_azure(
            [
                "https://graph.microsoft.com/User.Read",
                "https://graph.microsoft.com/Mail.Send",
            ]
        )

        # Fully-qualified URIs pass through unchanged
        assert result == [
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Mail.Send",
        ]

    def test_required_scopes_with_oidc_filters_validation(self):
        """Test that OIDC scopes in required_scopes are filtered from token validation."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "openid", "profile"],
            jwt_signing_key="test-secret",
        )

        # Token validator should only require non-OIDC scopes
        assert provider._token_validator.required_scopes == ["read"]

    def test_required_scopes_all_oidc_results_in_no_validation(self):
        """Test that if all required_scopes are OIDC, no scope validation occurs."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["openid", "profile"],
            jwt_signing_key="test-secret",
        )

        # Token validator should have empty required scopes (all were OIDC)
        assert provider._token_validator.required_scopes == []

    def test_valid_scopes_includes_oidc_scopes(self):
        """Test that valid_scopes advertises OIDC scopes to clients."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "openid", "profile"],
            jwt_signing_key="test-secret",
        )

        # required_scopes (used for validation) excludes OIDC scopes
        assert provider.required_scopes == ["read"]
        # But valid_scopes (advertised to clients) includes all scopes
        assert provider.client_registration_options is not None
        assert provider.client_registration_options.valid_scopes == [
            "read",
            "openid",
            "profile",
        ]

    def test_prepare_scopes_for_refresh_handles_oidc_scopes(self):
        """Test that token refresh correctly handles OIDC scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        # Simulate stored scopes that include OIDC scopes
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "openid", "profile"]
        )

        # Custom scope should be prefixed, OIDC scopes should not
        assert "api://my-api/read" in result
        assert "openid" in result
        assert "profile" in result
        assert "api://my-api/openid" not in result
        assert "api://my-api/profile" not in result
