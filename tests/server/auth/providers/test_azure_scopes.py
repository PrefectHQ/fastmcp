"""Tests for Azure provider scope handling, JWT verifier, and OBO integration."""

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.providers.azure import (
    OIDC_SCOPES,
    AzureJWTVerifier,
    AzureProvider,
)
from fastmcp.server.auth.providers.jwt import RSAKeyPair


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


class TestOIDCScopeHandling:
    """Tests for OIDC scope handling in Azure provider.

    Azure access tokens do NOT include OIDC scopes (openid, profile, email,
    offline_access) in the `scp` claim - they're only used during authorization.
    These tests verify that:
    1. OIDC scopes are never prefixed with identifier_uri
    2. OIDC scopes are filtered from token validation
    3. OIDC scopes are still advertised to clients via valid_scopes
    """

    def test_oidc_scopes_constant(self, memory_storage: MemoryStore):
        """Verify OIDC_SCOPES contains the standard OIDC scopes."""
        assert OIDC_SCOPES == {"openid", "profile", "email", "offline_access"}

    def test_prefix_scopes_does_not_prefix_oidc_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test that _prefix_scopes_for_azure never prefixes OIDC scopes."""
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

        # All OIDC scopes should pass through unchanged
        result = provider._prefix_scopes_for_azure(
            ["openid", "profile", "email", "offline_access"]
        )

        assert result == ["openid", "profile", "email", "offline_access"]

    def test_prefix_scopes_mixed_oidc_and_custom(self, memory_storage: MemoryStore):
        """Test prefixing with a mix of OIDC and custom scopes."""
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

    def test_prefix_scopes_dot_notation_gets_prefixed(
        self, memory_storage: MemoryStore
    ):
        """Test that dot-notation scopes get prefixed (use additional_authorize_scopes for Graph)."""
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

        # Dot-notation scopes ARE prefixed - use additional_authorize_scopes for Graph
        # or fully-qualified format like https://graph.microsoft.com/User.Read
        result = provider._prefix_scopes_for_azure(["my.scope", "admin.read"])

        assert result == ["api://my-api/my.scope", "api://my-api/admin.read"]

    def test_prefix_scopes_fully_qualified_graph_not_prefixed(
        self, memory_storage: MemoryStore
    ):
        """Test that fully-qualified Graph scopes are not prefixed."""
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

    def test_required_scopes_with_oidc_filters_validation(
        self, memory_storage: MemoryStore
    ):
        """Test that OIDC scopes in required_scopes are filtered from token validation."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "openid", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Token validator should only require non-OIDC scopes
        assert provider._token_validator.required_scopes == ["read"]

    def test_required_scopes_all_oidc_results_in_no_validation(
        self, memory_storage: MemoryStore
    ):
        """Test that if all required_scopes are OIDC, no scope validation occurs."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["openid", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Token validator should have empty required scopes (all were OIDC)
        assert provider._token_validator.required_scopes == []

    def test_valid_scopes_includes_oidc_scopes(self, memory_storage: MemoryStore):
        """Test that valid_scopes advertises OIDC scopes to clients."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "openid", "profile"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
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

    def test_prepare_scopes_for_refresh_handles_oidc_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test that token refresh correctly handles OIDC scopes."""
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


class TestAzureTokenExchangeScopes:
    """Tests for Azure provider's token exchange scope handling.

    Azure requires scopes to be sent during the authorization code exchange.
    The provider overrides _prepare_scopes_for_token_exchange to return
    properly prefixed scopes.
    """

    def test_prepare_scopes_returns_prefixed_scopes(self, memory_storage: MemoryStore):
        """Test that _prepare_scopes_for_token_exchange returns prefixed scopes."""
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

        scopes = provider._prepare_scopes_for_token_exchange(["read", "write"])
        assert len(scopes) > 0
        assert "api://my-api/read" in scopes
        assert "api://my-api/write" in scopes

    def test_prepare_scopes_includes_additional_oidc_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test that _prepare_scopes_for_token_exchange includes OIDC scopes."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["openid", "profile", "offline_access"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        scopes = provider._prepare_scopes_for_token_exchange(["read"])
        assert len(scopes) > 0
        assert "api://my-api/read" in scopes
        assert "openid" in scopes
        assert "profile" in scopes
        assert "offline_access" in scopes

    def test_prepare_scopes_excludes_other_api_scopes(
        self, memory_storage: MemoryStore
    ):
        """Test token exchange excludes other API scopes (Azure AADSTS28000).

        Azure only allows ONE resource per token exchange. Other API scopes
        are requested during authorization but excluded from token exchange.
        """
        provider = AzureProvider(
            client_id="00000000-1111-2222-3333-444444444444",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            required_scopes=["user_impersonation"],
            additional_authorize_scopes=[
                "openid",
                "profile",
                "offline_access",
                "api://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/user_impersonation",
                "api://11111111-2222-3333-4444-555555555555/user_impersonation",
            ],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        scopes = provider._prepare_scopes_for_token_exchange(["user_impersonation"])
        assert len(scopes) > 0
        # Primary API scope should be prefixed with the provider's identifier_uri
        assert "api://00000000-1111-2222-3333-444444444444/user_impersonation" in scopes
        # OIDC scopes should be included
        assert "openid" in scopes
        assert "profile" in scopes
        assert "offline_access" in scopes
        # Other API scopes should NOT be included (Azure multi-resource limitation)
        assert not any("api://aaaaaaaa" in s for s in scopes)
        assert not any("api://11111111" in s for s in scopes)

    def test_prepare_scopes_deduplicates_scopes(self, memory_storage: MemoryStore):
        """Test that duplicate scopes are deduplicated."""
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read"],
            additional_authorize_scopes=["api://my-api/read", "openid"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # Pass a scope that will be prefixed to match one in additional_authorize_scopes
        scopes = provider._prepare_scopes_for_token_exchange(["read"])
        assert len(scopes) > 0
        # Should be deduplicated - api://my-api/read appears only once
        assert scopes.count("api://my-api/read") == 1
        assert "openid" in scopes

    def test_extra_token_params_does_not_contain_scope(
        self, memory_storage: MemoryStore
    ):
        """Test that extra_token_params doesn't contain scope to avoid TypeError.

        Previously, Azure provider set extra_token_params={"scope": ...} during init.
        This caused a TypeError in exchange_refresh_token because it passes both
        scope=... AND **self._extra_token_params, resulting in:
        "got multiple values for keyword argument 'scope'"

        The fix uses the _prepare_scopes_for_token_exchange hook instead.
        """
        provider = AzureProvider(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test-tenant",
            base_url="https://myserver.com",
            identifier_uri="api://my-api",
            required_scopes=["read", "write"],
            additional_authorize_scopes=["openid", "profile", "offline_access"],
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        # extra_token_params should NOT contain "scope" to avoid TypeError during refresh
        assert "scope" not in provider._extra_token_params

        # Instead, scopes should be provided via the hook methods
        exchange_scopes = provider._prepare_scopes_for_token_exchange(["read", "write"])
        assert len(exchange_scopes) > 0

        refresh_scopes = provider._prepare_scopes_for_upstream_refresh(
            ["read", "write"]
        )
        assert len(refresh_scopes) > 0


class TestAzureJWTVerifier:
    """Tests for AzureJWTVerifier pre-configured JWT verifier."""

    def test_auto_configures_from_client_and_tenant(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["access_as_user"],
        )
        assert (
            verifier.jwks_uri
            == "https://login.microsoftonline.com/my-tenant-id/discovery/v2.0/keys"
        )
        assert verifier.issuer == "https://login.microsoftonline.com/my-tenant-id/v2.0"
        assert verifier.audience == "my-client-id"
        assert verifier.algorithm == "RS256"
        assert verifier.required_scopes == ["access_as_user"]

    async def test_validates_short_form_scopes(self):
        key_pair = RSAKeyPair.generate()
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["access_as_user"],
        )
        # Override to use our test key instead of JWKS
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        token = key_pair.create_token(
            subject="test-user",
            issuer="https://login.microsoftonline.com/my-tenant-id/v2.0",
            audience="my-client-id",
            additional_claims={"scp": "access_as_user"},
        )
        result = await verifier.load_access_token(token)
        assert result is not None
        assert "access_as_user" in result.scopes

    def test_scopes_supported_returns_prefixed_form(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["read", "write"],
        )
        assert verifier.scopes_supported == [
            "api://my-client-id/read",
            "api://my-client-id/write",
        ]

    def test_already_prefixed_scopes_pass_through(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["api://my-client-id/read"],
        )
        assert verifier.scopes_supported == ["api://my-client-id/read"]

    def test_oidc_scopes_not_prefixed(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["openid", "read"],
        )
        assert verifier.scopes_supported == ["openid", "api://my-client-id/read"]

    def test_custom_identifier_uri(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["read"],
            identifier_uri="api://custom-uri",
        )
        assert verifier.scopes_supported == ["api://custom-uri/read"]

    def test_custom_base_authority_for_gov_cloud(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
        )
        assert (
            verifier.jwks_uri
            == "https://login.microsoftonline.us/my-tenant-id/discovery/v2.0/keys"
        )
        assert verifier.issuer == "https://login.microsoftonline.us/my-tenant-id/v2.0"

    def test_scopes_supported_empty_when_no_required_scopes(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="my-tenant-id",
        )
        assert verifier.scopes_supported == []

    def test_default_identifier_uri_uses_client_id(self):
        verifier = AzureJWTVerifier(
            client_id="abc-123",
            tenant_id="my-tenant-id",
            required_scopes=["read"],
        )
        assert verifier.scopes_supported == ["api://abc-123/read"]

    def test_multi_tenant_organizations_skips_issuer(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="organizations",
        )
        assert verifier.issuer is None

    def test_multi_tenant_consumers_skips_issuer(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="consumers",
        )
        assert verifier.issuer is None

    def test_multi_tenant_common_skips_issuer(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="common",
        )
        assert verifier.issuer is None

    def test_specific_tenant_sets_issuer(self):
        verifier = AzureJWTVerifier(
            client_id="my-client-id",
            tenant_id="12345678-1234-1234-1234-123456789012",
        )
        assert (
            verifier.issuer
            == "https://login.microsoftonline.com/12345678-1234-1234-1234-123456789012/v2.0"
        )


class TestAzureOBOIntegration:
    """Tests for azure.identity OBO integration (get_obo_credential, EntraOBOToken)."""

    async def test_get_obo_credential_returns_configured_credential(self):
        """Test that get_obo_credential returns a properly configured credential."""
        from unittest.mock import MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        mock_credential = MagicMock()
        with patch(
            "azure.identity.aio.OnBehalfOfCredential", return_value=mock_credential
        ) as mock_class:
            credential = await provider.get_obo_credential(
                user_assertion="user-token-123"
            )

            mock_class.assert_called_once_with(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
                user_assertion="user-token-123",
                authority="https://login.microsoftonline.com",
            )
            assert credential is mock_credential

    async def test_get_obo_credential_caches_by_assertion(self):
        """Test that the same assertion returns the cached credential."""
        from unittest.mock import MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        mock_credential = MagicMock()
        with patch(
            "azure.identity.aio.OnBehalfOfCredential", return_value=mock_credential
        ) as mock_class:
            first = await provider.get_obo_credential(user_assertion="same-token")
            second = await provider.get_obo_credential(user_assertion="same-token")

            assert first is second
            mock_class.assert_called_once()

    async def test_get_obo_credential_different_assertions_get_different_credentials(
        self,
    ):
        """Test that different assertions produce different credentials."""
        from unittest.mock import MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        creds = [MagicMock(), MagicMock()]
        with patch("azure.identity.aio.OnBehalfOfCredential", side_effect=creds):
            first = await provider.get_obo_credential(user_assertion="token-a")
            second = await provider.get_obo_credential(user_assertion="token-b")

            assert first is not second
            assert first is creds[0]
            assert second is creds[1]

    async def test_get_obo_credential_evicts_oldest_when_over_capacity(self):
        """Test that credentials are evicted LRU-style when cache is full."""
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )
        provider._obo_max_credentials = 2

        creds = [MagicMock(close=AsyncMock()) for _ in range(3)]
        with patch("azure.identity.aio.OnBehalfOfCredential", side_effect=creds):
            await provider.get_obo_credential(user_assertion="token-1")
            await provider.get_obo_credential(user_assertion="token-2")
            await provider.get_obo_credential(user_assertion="token-3")

            assert len(provider._obo_credentials) == 2
            creds[0].close.assert_awaited_once()
            # token-1's credential was evicted
            assert (
                await provider.get_obo_credential(user_assertion="token-2") is creds[1]
            )
            assert (
                await provider.get_obo_credential(user_assertion="token-3") is creds[2]
            )

    async def test_close_obo_credentials(self):
        """Test that close_obo_credentials closes all cached credentials."""
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            jwt_signing_key="test-secret",
        )

        creds = [MagicMock(close=AsyncMock()) for _ in range(2)]
        with patch("azure.identity.aio.OnBehalfOfCredential", side_effect=creds):
            await provider.get_obo_credential(user_assertion="token-a")
            await provider.get_obo_credential(user_assertion="token-b")

        await provider.close_obo_credentials()

        assert len(provider._obo_credentials) == 0
        for cred in creds:
            cred.close.assert_awaited_once()

    async def test_get_obo_credential_with_custom_authority(self):
        """Test that get_obo_credential uses custom base_authority."""
        from unittest.mock import MagicMock, patch

        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="gov-tenant-id",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
        )

        mock_credential = MagicMock()
        with patch(
            "azure.identity.aio.OnBehalfOfCredential", return_value=mock_credential
        ) as mock_class:
            await provider.get_obo_credential(user_assertion="user-token")

            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["authority"] == "https://login.microsoftonline.us"

    def test_tenant_and_authority_stored_as_attributes(self):
        """Test that tenant_id and base_authority are stored for OBO credential creation."""
        provider = AzureProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="my-tenant",
            base_url="https://myserver.com",
            required_scopes=["read"],
            base_authority="login.microsoftonline.us",
            jwt_signing_key="test-secret",
        )

        assert provider._tenant_id == "my-tenant"
        assert provider._base_authority == "login.microsoftonline.us"

    def test_entra_obo_token_is_importable(self):
        """Test that EntraOBOToken can be imported."""
        from fastmcp.server.auth.providers.azure import EntraOBOToken

        assert EntraOBOToken is not None

    def test_entra_obo_token_creates_dependency(self):
        """Test that EntraOBOToken creates a dependency with scopes."""
        from fastmcp.server.auth.providers.azure import EntraOBOToken, _EntraOBOToken

        dep = EntraOBOToken(["https://graph.microsoft.com/User.Read"])
        assert isinstance(dep, _EntraOBOToken)
        assert dep.scopes == ["https://graph.microsoft.com/User.Read"]

    def test_entra_obo_token_is_dependency_instance(self):
        """Test that EntraOBOToken is a Dependency instance."""
        try:
            from docket.dependencies import Dependency
        except ImportError:
            from fastmcp._vendor.docket_di import Dependency

        from fastmcp.server.auth.providers.azure import _EntraOBOToken

        dep = _EntraOBOToken(["scope"])
        assert isinstance(dep, Dependency)
