"""Tests for Azure (Microsoft Entra) OAuth provider."""

from urllib.parse import parse_qs, urlparse

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestAzureProvider:
    """Test Azure OAuth provider functionality."""

    def test_init_with_explicit_params(self, memory_storage: MemoryStore):
        """Test AzureProvider initialization with explicit parameters."""
        provider = AzureProvider(
            client_id="12345678-1234-1234-1234-123456789012",
            client_secret="azure_secret_123",
            tenant_id="87654321-4321-4321-4321-210987654321",
            base_url="https://myserver.com",
            required_scopes=["read", "write"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_client_id == "12345678-1234-1234-1234-123456789012"
        assert provider._upstream_client_secret is not None
        assert provider._upstream_client_secret.get_secret_value() == "azure_secret_123"
        assert str(provider.base_url) == "https://myserver.com/"
        # Check tenant is in the endpoints
        parsed_auth = urlparse(provider._upstream_authorization_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_auth.path
        parsed_token = urlparse(provider._upstream_token_endpoint)
        assert "87654321-4321-4321-4321-210987654321" in parsed_token.path

    def test_init_defaults(self, memory_storage: MemoryStore):
        """Test that default values are applied correctly."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Check defaults
        assert provider._redirect_path == "/auth/callback"
        # Azure provider defaults are set but we can't easily verify them without accessing internals

    def test_offline_access_automatically_included(self, memory_storage: MemoryStore):
        """Test that offline_access is automatically added to get refresh tokens."""
        # Without specifying offline_access
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "offline_access" in provider.additional_authorize_scopes

    def test_offline_access_not_duplicated(self, memory_storage: MemoryStore):
        """Test that offline_access is not duplicated if already specified."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            additional_authorize_scopes=["User.Read", "offline_access"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Should appear exactly once
        assert provider.additional_authorize_scopes.count("offline_access") == 1
        assert "User.Read" in provider.additional_authorize_scopes

    def test_oauth_endpoints_configured_correctly(self, memory_storage: MemoryStore):
        """Test that OAuth endpoints are configured correctly."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test_secret",
            client_storage=memory_storage,
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

    def test_special_tenant_values(self, memory_storage: MemoryStore):
        """Test that special tenant values are accepted."""
        # Test with "organizations"
        provider1 = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="organizations",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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
            client_storage=memory_storage,
        )
        parsed = urlparse(provider2._upstream_authorization_endpoint)
        assert "/consumers/" in parsed.path

    def test_azure_specific_scopes(self, memory_storage: MemoryStore):
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
            client_storage=memory_storage,
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

    def test_init_does_not_require_api_client_id_anymore(
        self, memory_storage: MemoryStore
    ):
        """API client ID is no longer required; audience is client_id."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )
        assert provider is not None

    def test_init_with_custom_audience_uses_jwt_verifier(
        self, memory_storage: MemoryStore
    ):
        """When audience is provided, JWTVerifier is configured with JWKS and issuer."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=[".default"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._token_validator is not None
        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        assert verifier.jwks_uri is not None
        assert verifier.jwks_uri.startswith(
            "https://login.microsoftonline.com/my-tenant/discovery/v2.0/keys"
        )
        assert verifier.issuer == "https://login.microsoftonline.com/my-tenant/v2.0"
        assert verifier.audience == ["test_client", "api://my-api"]
        # Scopes are stored unprefixed for token validation
        # (Azure returns unprefixed scopes like ".default" in JWT tokens)
        assert verifier.required_scopes == [".default"]

    async def test_token_accepted_with_client_id_audience(
        self, memory_storage: MemoryStore
    ):
        """Azure AD v2 tokens use the bare client_id as aud — must be accepted."""
        key_pair = RSAKeyPair.generate()
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://login.microsoftonline.com/my-tenant/v2.0",
            audience="test_client",
            additional_claims={"scp": "read"},
        )
        result = await verifier.load_access_token(token)
        assert result is not None

    async def test_token_accepted_with_identifier_uri_audience(
        self, memory_storage: MemoryStore
    ):
        """Azure AD v1 tokens use the identifier_uri as aud — must be accepted."""
        key_pair = RSAKeyPair.generate()
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://login.microsoftonline.com/my-tenant/v2.0",
            audience="api://my-api",
            additional_claims={"scp": "read"},
        )
        result = await verifier.load_access_token(token)
        assert result is not None

    async def test_token_rejected_with_wrong_audience(
        self, memory_storage: MemoryStore
    ):
        """Tokens for a different application must be rejected."""
        key_pair = RSAKeyPair.generate()
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://login.microsoftonline.com/my-tenant/v2.0",
            audience="wrong-app-id",
            additional_claims={"scp": "read"},
        )
        result = await verifier.load_access_token(token)
        assert result is None

    async def test_authorize_filters_resource_and_stores_unprefixed_scopes(
        self, memory_storage: MemoryStore
    ):
        """authorize() should drop resource parameter and store unprefixed scopes for MCP clients."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="common",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            base_url="https://srv.example",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    async def test_authorize_appends_additional_scopes(
        self, memory_storage: MemoryStore
    ):
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
            client_storage=memory_storage,
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

    def test_base_authority_defaults_to_public_cloud(self, memory_storage: MemoryStore):
        """Test that base_authority defaults to login.microsoftonline.com."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    def test_base_authority_azure_government(self, memory_storage: MemoryStore):
        """Test Azure Government endpoints with login.microsoftonline.us."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="gov-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    def test_base_authority_from_parameter(self, memory_storage: MemoryStore):
        """Test that base_authority can be set via parameter."""
        provider = AzureProvider(
            client_id="env-client-id",
            client_secret="env-secret",
            tenant_id="env-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    def test_base_authority_with_special_tenant_values(
        self, memory_storage: MemoryStore
    ):
        """Test that base_authority works with special tenant values like 'organizations'."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="organizations",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        parsed = urlparse(provider._upstream_authorization_endpoint)
        assert parsed.netloc == "login.microsoftonline.us"
        assert "/organizations/" in parsed.path

    def test_prepare_scopes_for_upstream_refresh_basic_prefixing(
        self, memory_storage: MemoryStore
    ):
        """Test that unprefixed scopes are correctly prefixed for Azure token refresh."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Unprefixed scopes from storage should be prefixed
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "offline_access" in result  # Auto-included for refresh tokens
        assert len(result) == 3

    def test_prepare_scopes_for_upstream_refresh_already_prefixed(
        self, memory_storage: MemoryStore
    ):
        """Test that already-prefixed scopes remain unchanged."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Already prefixed scopes should pass through unchanged
        result = provider._prepare_scopes_for_upstream_refresh(
            ["api://my-api/read", "api://other-api/admin"]
        )

        assert "api://my-api/read" in result
        assert "api://other-api/admin" in result
        assert "offline_access" in result  # Auto-included for refresh tokens
        assert len(result) == 3

    def test_prepare_scopes_for_upstream_refresh_with_additional_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test that only OIDC scopes from additional_authorize_scopes are added.

        Azure only allows ONE resource per token request (AADSTS28000), so
        non-OIDC scopes like User.Read are excluded from refresh requests.
        """
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=[
                "User.Read",  # Not OIDC - excluded
                "openid",
                "profile",
                "offline_access",
            ],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Base scopes should be prefixed, only OIDC scopes appended
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "User.Read" not in result  # Not OIDC, excluded
        assert "openid" in result
        assert "profile" in result
        assert "offline_access" in result
        assert len(result) == 5

    def test_prepare_scopes_for_upstream_refresh_filters_duplicate_additional_scopes(
        self,
        memory_storage: MemoryStore,
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
            client_storage=memory_storage,
        )

        # If additional scopes were accidentally stored, they should be filtered
        # User.Read is not OIDC so won't be added
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "User.Read", "openid"]
        )

        # Should have: api://my-api/read (prefixed) + openid + offline_access (OIDC scopes)
        # User.Read is filtered from storage AND not added (not OIDC)
        assert "api://my-api/read" in result
        assert "User.Read" not in result  # Not OIDC
        assert result.count("openid") == 1
        assert "offline_access" in result  # Auto-included and is OIDC
        assert len(result) == 3

    def test_prepare_scopes_for_upstream_refresh_mixed_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test mixed scenario with both prefixed and unprefixed scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["openid"],  # OIDC scope
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Mix of prefixed and unprefixed scopes
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "api://other-api/admin", "write"]
        )

        assert "api://my-api/read" in result
        assert "api://other-api/admin" in result  # Already prefixed, unchanged
        assert "api://my-api/write" in result
        assert "openid" in result
        assert "offline_access" in result  # Auto-included
        assert len(result) == 5

    def test_prepare_scopes_for_upstream_refresh_scope_with_slash(
        self, memory_storage: MemoryStore
    ):
        """Test that scopes containing '/' are not prefixed."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Scopes with "/" should not be prefixed (already fully qualified)
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "https://graph.microsoft.com/.default"]
        )

        assert "api://my-api/read" in result
        assert (
            "https://graph.microsoft.com/.default" in result
        )  # Not prefixed (contains ://)

    def test_prepare_scopes_for_upstream_refresh_empty_scopes(
        self, memory_storage: MemoryStore
    ):
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
            client_storage=memory_storage,
        )

        # Empty scopes should still add OIDC scopes (not User.Read)
        result = provider._prepare_scopes_for_upstream_refresh([])

        assert "User.Read" not in result  # Not OIDC
        assert "openid" in result
        assert "offline_access" in result  # Auto-included
        assert len(result) == 2  # Only OIDC scopes: openid + offline_access

    def test_prepare_scopes_for_upstream_refresh_no_additional_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test behavior when no additional_authorize_scopes are configured."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Should prefix base scopes, plus auto-added offline_access
        result = provider._prepare_scopes_for_upstream_refresh(["read", "write"])

        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "offline_access" in result  # Auto-included
        assert len(result) == 3

    def test_prepare_scopes_for_upstream_refresh_deduplicates_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test that duplicate scopes are deduplicated while preserving order."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["openid", "profile"],  # OIDC scopes only
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Test with duplicate base scopes
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "write", "read", "openid"]
        )

        # Should have deduplicated results in order (OIDC scopes added, offline_access auto-added)
        assert result == [
            "api://my-api/read",
            "api://my-api/write",
            "openid",
            "profile",
            "offline_access",
        ]
        assert len(result) == 5

    def test_prepare_scopes_for_upstream_refresh_deduplicates_prefixed_variants(
        self, memory_storage: MemoryStore
    ):
        """Test that both prefixed and unprefixed variants are deduplicated."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Test with both prefixed and unprefixed variants of same scope
        result = provider._prepare_scopes_for_upstream_refresh(
            ["read", "api://my-api/read", "write"]
        )

        # Should deduplicate - first occurrence wins (api://my-api/read from "read")
        assert "api://my-api/read" in result
        assert "api://my-api/write" in result
        assert "offline_access" in result  # Auto-included
        # Should have 3 items (read deduplicated, plus offline_access)
        assert len(result) == 3
        assert result.count("api://my-api/read") == 1


class TestAzureProviderTokenIssuer:
    """Tests for the token_issuer parameter on AzureProvider."""

    def test_default_issuer_is_derived(self, memory_storage: MemoryStore):
        """Without token_issuer, the issuer is derived from base_authority/tenant_id."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        assert (
            provider._token_validator.issuer
            == "https://login.microsoftonline.com/my-tenant/v2.0"
        )

    def test_explicit_token_issuer_is_used(self, memory_storage: MemoryStore):
        """An explicit token_issuer string is passed to the verifier."""
        custom_issuer = "https://custom.issuer.com/v2.0"
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            token_issuer=custom_issuer,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        assert provider._token_validator.issuer == custom_issuer

    async def test_explicit_issuer_enforced(self, memory_storage: MemoryStore):
        """With an explicit token_issuer, wrong issuers are rejected."""
        key_pair = RSAKeyPair.generate()
        expected = "https://expected.issuer.com/v2.0"
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            token_issuer=expected,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        good_token = key_pair.create_token(
            subject="test-user",
            issuer=expected,
            audience="test_client",
            additional_claims={"scp": "read"},
        )
        assert await verifier.load_access_token(good_token) is not None

        bad_token = key_pair.create_token(
            subject="test-user",
            issuer="https://wrong.issuer.com/v2.0",
            audience="test_client",
            additional_claims={"scp": "read"},
        )
        assert await verifier.load_access_token(bad_token) is None


class TestAzureProviderFromB2C:
    """Tests for the AzureProvider.from_b2c() classmethod factory."""

    def test_b2c_endpoints_derived_correctly(self, memory_storage: MemoryStore):
        """from_b2c() produces correct B2C authority and tenant path."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_authorization_endpoint == (
            "https://mytenant.b2clogin.com"
            "/mytenant.onmicrosoft.com/B2C_1_susi"
            "/oauth2/v2.0/authorize"
        )
        assert provider._upstream_token_endpoint == (
            "https://mytenant.b2clogin.com"
            "/mytenant.onmicrosoft.com/B2C_1_susi"
            "/oauth2/v2.0/token"
        )

    def test_b2c_identifier_uri_uses_https(self, memory_storage: MemoryStore):
        """from_b2c() sets identifier_uri with https:// scheme, not api://."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="00000000-0000-0000-0000-000000000001",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider.identifier_uri == (
            "https://mytenant.onmicrosoft.com/00000000-0000-0000-0000-000000000001"
        )
        assert provider.identifier_uri.startswith("https://")
        assert not provider.identifier_uri.startswith("api://")

    def test_b2c_issuer_disabled_by_default(self, memory_storage: MemoryStore):
        """from_b2c() disables issuer validation by default (token_issuer=None)."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        assert provider._token_validator.issuer is None

    def test_b2c_explicit_token_issuer(self, memory_storage: MemoryStore):
        """from_b2c() forwards an explicit token_issuer to the verifier."""
        explicit_issuer = (
            "https://mytenant.b2clogin.com/11111111-2222-3333-4444-555555555555/v2.0/"
        )
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            token_issuer=explicit_issuer,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        assert provider._token_validator.issuer == explicit_issuer

    def test_b2c_custom_domain(self, memory_storage: MemoryStore):
        """from_b2c() uses custom_domain in place of {tenant}.b2clogin.com."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            custom_domain="auth.mycompany.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "auth.mycompany.com" in provider._upstream_authorization_endpoint
        assert "auth.mycompany.com" in provider._upstream_token_endpoint
        assert "mytenant.b2clogin.com" not in provider._upstream_authorization_endpoint

    def test_b2c_custom_domain_with_scheme_normalized(
        self, memory_storage: MemoryStore
    ):
        """from_b2c() strips scheme and trailing slash from custom_domain."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            custom_domain="https://auth.mycompany.com/",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "auth.mycompany.com" in provider._upstream_authorization_endpoint
        assert "https://https://" not in provider._upstream_authorization_endpoint

    def test_b2c_custom_identifier_uri(self, memory_storage: MemoryStore):
        """from_b2c() respects an explicit identifier_uri override."""
        custom_uri = "https://mycompany.com/api/mcp"
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            identifier_uri=custom_uri,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider.identifier_uri == custom_uri

    def test_b2c_scope_prefix_uses_https(self, memory_storage: MemoryStore):
        """from_b2c() scopes are prefixed with the https:// identifier URI."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="aabbccdd",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        result = provider._prefix_scopes_for_azure(["mcp-access"])
        assert result == ["https://mytenant.onmicrosoft.com/aabbccdd/mcp-access"]

    def test_b2c_returns_azure_provider_instance(self, memory_storage: MemoryStore):
        """from_b2c() returns an AzureProvider, not a subclass."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert type(provider) is AzureProvider

    def test_b2c_custom_policy_name(self, memory_storage: MemoryStore):
        """from_b2c() accepts custom policy names (B2C_1A_*)."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1A_SIGNUP_SIGNIN",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "B2C_1A_SIGNUP_SIGNIN" in provider._upstream_authorization_endpoint
        assert "B2C_1A_SIGNUP_SIGNIN" in provider._upstream_token_endpoint

    async def test_b2c_token_accepted_with_any_issuer(
        self, memory_storage: MemoryStore
    ):
        """B2C provider (issuer=None) accepts tokens from any issuer."""
        key_pair = RSAKeyPair.generate()
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="my-client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://mytenant.b2clogin.com/11111111-guid/v2.0/",
            audience="my-client-id",
            additional_claims={"scp": "mcp-access"},
        )
        result = await verifier.load_access_token(token)
        assert result is not None

    async def test_b2c_token_rejected_with_wrong_audience(
        self, memory_storage: MemoryStore
    ):
        """B2C provider still rejects tokens with wrong audience."""
        key_pair = RSAKeyPair.generate()
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="my-client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://mytenant.b2clogin.com/11111111-guid/v2.0/",
            audience="wrong-app-id",
            additional_claims={"scp": "mcp-access"},
        )
        result = await verifier.load_access_token(token)
        assert result is None

    async def test_b2c_obo_raises_not_implemented(self, memory_storage: MemoryStore):
        """from_b2c() providers must reject OBO calls with NotImplementedError."""
        provider = AzureProvider.from_b2c(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        with pytest.raises(NotImplementedError, match="OBO"):
            await provider.get_obo_credential(user_assertion="fake-token")


class TestAzureProviderFromB2CInputValidation:
    """Input validation for from_b2c() parameters."""

    @pytest.mark.parametrize(
        "tenant_name",
        [
            "mytenant.onmicrosoft.com",
            "my.onmicrosoft.com.tenant",
        ],
    )
    def test_tenant_name_with_onmicrosoft_suffix_rejected(
        self, memory_storage: MemoryStore, tenant_name: str
    ):
        with pytest.raises(ValueError, match="onmicrosoft.com"):
            AzureProvider.from_b2c(
                tenant_name=tenant_name,
                policy_name="B2C_1_susi",
                client_id="client-id",
                client_secret="secret",
                required_scopes=["mcp-access"],
                base_url="https://myserver.com",
                jwt_signing_key="test-secret",
                client_storage=memory_storage,
            )
