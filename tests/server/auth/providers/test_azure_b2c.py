"""Tests for the Azure AD B2C OAuth provider."""

from urllib.parse import parse_qs, urlparse

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.providers.azure import OIDC_SCOPES
from fastmcp.server.auth.providers.azure_b2c import AzureB2CProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


@pytest.fixture
def memory_storage() -> MemoryStore:
    """Provide a MemoryStore for tests to avoid SQLite initialization on Windows."""
    return MemoryStore()


@pytest.fixture
def provider(memory_storage: MemoryStore) -> AzureB2CProvider:
    """A minimal AzureB2CProvider for reuse across tests."""
    return AzureB2CProvider(
        tenant_name="mytenant",
        policy_name="B2C_1_susi",
        client_id="00000000-0000-0000-0000-000000000001",
        client_secret="client-secret",
        required_scopes=["mcp-access"],
        base_url="https://myserver.com",
        jwt_signing_key="test-secret",
        client_storage=memory_storage,
    )


class TestAzureB2CProviderInit:
    """Initialization behaviour and derived endpoint/scope values."""

    def test_b2c_authority_host_in_authorization_endpoint(
        self, memory_storage: MemoryStore
    ) -> None:
        """Authorization endpoint must use {tenant}.b2clogin.com, not login.microsoftonline.com."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "mytenant.b2clogin.com" in provider._upstream_authorization_endpoint
        assert (
            "login.microsoftonline.com" not in provider._upstream_authorization_endpoint
        )

    def test_b2c_authority_host_in_token_endpoint(
        self, memory_storage: MemoryStore
    ) -> None:
        """Token endpoint must use {tenant}.b2clogin.com."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "mytenant.b2clogin.com" in provider._upstream_token_endpoint
        assert "login.microsoftonline.com" not in provider._upstream_token_endpoint

    def test_policy_name_embedded_in_endpoints(
        self, memory_storage: MemoryStore
    ) -> None:
        """Both OAuth endpoints must contain the policy name in the URL path."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "B2C_1_susi" in provider._upstream_authorization_endpoint
        assert "B2C_1_susi" in provider._upstream_token_endpoint

    def test_exact_authorization_endpoint(self, memory_storage: MemoryStore) -> None:
        """Authorization endpoint must follow the B2C URL pattern exactly."""
        provider = AzureB2CProvider(
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

    def test_exact_token_endpoint(self, memory_storage: MemoryStore) -> None:
        """Token endpoint must follow the B2C URL pattern exactly."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider._upstream_token_endpoint == (
            "https://mytenant.b2clogin.com"
            "/mytenant.onmicrosoft.com/B2C_1_susi"
            "/oauth2/v2.0/token"
        )

    def test_identifier_uri_uses_https_scheme(
        self, memory_storage: MemoryStore
    ) -> None:
        """B2C identifier URI must start with https://, not api://."""
        provider = AzureB2CProvider(
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

    def test_issuer_defaults_to_none(self, memory_storage: MemoryStore) -> None:
        """Issuer validation must be disabled by default for B2C compatibility."""
        provider = AzureB2CProvider(
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

    def test_explicit_token_issuer_is_respected(
        self, memory_storage: MemoryStore
    ) -> None:
        """When token_issuer is supplied it must be passed to the JWT verifier."""
        explicit_issuer = (
            "https://mytenant.b2clogin.com/11111111-2222-3333-4444-555555555555/v2.0/"
        )
        provider = AzureB2CProvider(
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

    def test_offline_access_automatically_included(
        self, memory_storage: MemoryStore
    ) -> None:
        """offline_access must be added to additional_authorize_scopes automatically."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert "offline_access" in provider.additional_authorize_scopes

    def test_offline_access_not_duplicated(self, memory_storage: MemoryStore) -> None:
        """offline_access must not appear twice when already in additional_authorize_scopes."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            additional_authorize_scopes=["openid", "offline_access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider.additional_authorize_scopes.count("offline_access") == 1

    def test_custom_policy_name(self, memory_storage: MemoryStore) -> None:
        """Custom policy name (B2C_1A_*) must appear in the endpoints."""
        provider = AzureB2CProvider(
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


class TestAzureB2CCustomDomain:
    """Custom domain support for B2C tenants."""

    def test_custom_domain_replaces_b2clogin_host(
        self, memory_storage: MemoryStore
    ) -> None:
        """When custom_domain is set it must be used instead of {tenant}.b2clogin.com."""
        provider = AzureB2CProvider(
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

    def test_custom_domain_exact_authorization_endpoint(
        self, memory_storage: MemoryStore
    ) -> None:
        """Authorization endpoint with a custom domain must follow the correct pattern."""
        provider = AzureB2CProvider(
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

        assert provider._upstream_authorization_endpoint == (
            "https://auth.mycompany.com"
            "/mytenant.onmicrosoft.com/B2C_1_susi"
            "/oauth2/v2.0/authorize"
        )


class TestAzureB2CScopeHandling:
    """B2C-specific scope prefixing behaviour."""

    def test_prefix_scopes_uses_https_identifier_uri(
        self, provider: AzureB2CProvider
    ) -> None:
        """Custom scopes must be prefixed with the https:// identifier URI."""
        result = provider._prefix_scopes_for_azure(["mcp-access", "read"])

        assert all(s.startswith("https://") for s in result)

    def test_prefix_scopes_exact_format(self, memory_storage: MemoryStore) -> None:
        """Prefixed scope must be identifier_uri/scope_name."""
        provider = AzureB2CProvider(
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

    def test_oidc_scopes_not_prefixed(self, provider: AzureB2CProvider) -> None:
        """OIDC scopes must pass through without any prefix."""
        result = provider._prefix_scopes_for_azure(
            list(OIDC_SCOPES)  # openid, profile, email, offline_access
        )

        assert set(result) == OIDC_SCOPES

    def test_mixed_oidc_and_custom_scopes(self, provider: AzureB2CProvider) -> None:
        """OIDC scopes stay unprefixed; custom scopes receive the https:// prefix."""
        result = provider._prefix_scopes_for_azure(["mcp-access", "openid", "profile"])

        assert "openid" in result
        assert "profile" in result
        assert any("mcp-access" in s and s.startswith("https://") for s in result)
        assert "https://mytenant.onmicrosoft.com" not in result  # not the URI itself

    def test_already_qualified_uris_pass_through(
        self, provider: AzureB2CProvider
    ) -> None:
        """Fully-qualified URIs (containing ://) must not be prefixed again."""
        already_qualified = [
            "https://mytenant.onmicrosoft.com/client-id/read",
            "https://graph.microsoft.com/.default",
        ]
        result = provider._prefix_scopes_for_azure(already_qualified)

        assert result == already_qualified

    def test_scopes_with_slash_pass_through(self, provider: AzureB2CProvider) -> None:
        """Scopes containing a slash (but no ://) must not be prefixed."""
        scopes_with_slash = ["some/scope"]
        result = provider._prefix_scopes_for_azure(scopes_with_slash)

        assert result == scopes_with_slash

    def test_prepare_scopes_for_token_exchange_uses_b2c_prefix(
        self, memory_storage: MemoryStore
    ) -> None:
        """Token-exchange scopes must carry the B2C https:// prefix."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="aabbccdd",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        result = provider._prepare_scopes_for_token_exchange(["mcp-access"])

        assert "https://mytenant.onmicrosoft.com/aabbccdd/mcp-access" in result
        assert not any(s.startswith("api://") for s in result)

    def test_prepare_scopes_for_upstream_refresh_uses_b2c_prefix(
        self, memory_storage: MemoryStore
    ) -> None:
        """Refresh-token scopes must carry the B2C https:// prefix."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="aabbccdd",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        result = provider._prepare_scopes_for_upstream_refresh(["mcp-access"])

        assert "https://mytenant.onmicrosoft.com/aabbccdd/mcp-access" in result
        assert "offline_access" in result
        assert not any(s.startswith("api://") for s in result)


class TestAzureB2CTokenValidation:
    """JWT token acceptance and rejection for B2C audiences."""

    async def test_token_accepted_with_client_id_audience(
        self, memory_storage: MemoryStore
    ) -> None:
        """Tokens whose audience is the client_id GUID must be accepted."""
        key_pair = RSAKeyPair.generate()
        provider = AzureB2CProvider(
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

    async def test_token_accepted_with_identifier_uri_audience(
        self, memory_storage: MemoryStore
    ) -> None:
        """Tokens whose audience is the B2C identifier URI must be accepted."""
        key_pair = RSAKeyPair.generate()
        provider = AzureB2CProvider(
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
            audience="https://mytenant.onmicrosoft.com/my-client-id",
            additional_claims={"scp": "mcp-access"},
        )
        result = await verifier.load_access_token(token)
        assert result is not None

    async def test_token_rejected_with_wrong_audience(
        self, memory_storage: MemoryStore
    ) -> None:
        """Tokens for a different application must be rejected even without issuer validation."""
        key_pair = RSAKeyPair.generate()
        provider = AzureB2CProvider(
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
            audience="some-other-app-id",
            additional_claims={"scp": "mcp-access"},
        )
        result = await verifier.load_access_token(token)
        assert result is None

    async def test_token_rejected_with_wrong_scope(
        self, memory_storage: MemoryStore
    ) -> None:
        """Tokens missing the required scope must be rejected."""
        key_pair = RSAKeyPair.generate()
        provider = AzureB2CProvider(
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
            additional_claims={"scp": "wrong-scope"},
        )
        result = await verifier.load_access_token(token)
        assert result is None

    async def test_explicit_issuer_enforced(self, memory_storage: MemoryStore) -> None:
        """When token_issuer is set, tokens with a different issuer must be rejected."""
        key_pair = RSAKeyPair.generate()
        expected_issuer = "https://mytenant.b2clogin.com/11111111-guid/v2.0/"
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="my-client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            token_issuer=expected_issuer,
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert isinstance(provider._token_validator, JWTVerifier)
        verifier = provider._token_validator
        verifier.public_key = key_pair.public_key
        verifier.jwks_uri = None

        # Correct issuer — must be accepted
        good_token = key_pair.create_token(
            subject="test-user",
            issuer=expected_issuer,
            audience="my-client-id",
            additional_claims={"scp": "mcp-access"},
        )
        assert await verifier.load_access_token(good_token) is not None

        # Wrong issuer — must be rejected
        bad_token = key_pair.create_token(
            subject="test-user",
            issuer="https://wrong-tenant.b2clogin.com/22222222-guid/v2.0/",
            audience="my-client-id",
            additional_claims={"scp": "mcp-access"},
        )
        assert await verifier.load_access_token(bad_token) is None


class TestAzureB2CAuthorizeFlow:
    """OAuth authorize() override behaviour (resource filtering, scope storage)."""

    async def test_authorize_drops_resource_and_stores_unprefixed_scopes(
        self, memory_storage: MemoryStore
    ) -> None:
        """authorize() must drop the resource param and store MCP scopes unprefixed."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="client-id",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://srv.example",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        await provider.register_client(
            OAuthClientInformationFull(
                client_id="dummy",
                client_secret="s",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )
        )

        client = OAuthClientInformationFull(
            client_id="dummy",
            client_secret="s",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=["mcp-access"],
            state="abc",
            code_challenge="xyz",
            resource="https://should.be.ignored",
        )

        url = await provider.authorize(client, params)

        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert "txn_id" in qs, "Should redirect to consent page with transaction ID"
        txn_id = qs["txn_id"][0]

        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        # Scopes stored unprefixed for MCP clients
        assert "mcp-access" in transaction.scopes
        # Resource parameter is filtered
        assert transaction.resource is None

    async def test_upstream_authorize_url_uses_b2c_scope_prefix(
        self, memory_storage: MemoryStore
    ) -> None:
        """The Azure authorization URL sent upstream must use the B2C https:// prefix."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="aabbccdd",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://srv.example",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        await provider.register_client(
            OAuthClientInformationFull(
                client_id="dummy",
                client_secret="s",
                redirect_uris=[AnyUrl("http://localhost:12345/callback")],
            )
        )

        client = OAuthClientInformationFull(
            client_id="dummy",
            client_secret="s",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            scopes=["mcp-access"],
            state="abc",
            code_challenge="xyz",
        )

        url = await provider.authorize(client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        txn_id = qs["txn_id"][0]

        transaction = await provider._transaction_store.get(key=txn_id)
        assert transaction is not None
        upstream_url = provider._build_upstream_authorize_url(
            txn_id, transaction.model_dump()
        )

        # B2C scope with https:// prefix must appear in the upstream URL
        assert (
            "https%3A%2F%2Fmytenant.onmicrosoft.com%2Faabbccdd%2Fmcp-access"
            in upstream_url
            or "https://mytenant.onmicrosoft.com/aabbccdd/mcp-access" in upstream_url
        )
        # The api:// prefix must NOT appear
        assert "api%3A%2F%2F" not in upstream_url
        assert "api://" not in upstream_url


class TestAzureB2CInputValidation:
    """Input validation for tenant_name, policy_name, and custom_domain."""

    @pytest.mark.parametrize(
        "tenant_name",
        [
            "mytenant.onmicrosoft.com",
            "my.onmicrosoft.com.tenant",
        ],
    )
    def test_tenant_name_with_onmicrosoft_suffix_rejected(
        self, memory_storage: MemoryStore, tenant_name: str
    ) -> None:
        """tenant_name containing .onmicrosoft.com must be rejected."""
        with pytest.raises(ValueError, match="onmicrosoft.com"):
            AzureB2CProvider(
                tenant_name=tenant_name,
                policy_name="B2C_1_susi",
                client_id="client-id",
                client_secret="secret",
                required_scopes=["mcp-access"],
                base_url="https://myserver.com",
                jwt_signing_key="test-secret",
                client_storage=memory_storage,
            )

    @pytest.mark.parametrize("tenant_name", ["my/tenant", "https://mytenant"])
    def test_tenant_name_with_slashes_or_scheme_rejected(
        self, memory_storage: MemoryStore, tenant_name: str
    ) -> None:
        """tenant_name with slashes or scheme must be rejected."""
        with pytest.raises(ValueError, match="tenant_name"):
            AzureB2CProvider(
                tenant_name=tenant_name,
                policy_name="B2C_1_susi",
                client_id="client-id",
                client_secret="secret",
                required_scopes=["mcp-access"],
                base_url="https://myserver.com",
                jwt_signing_key="test-secret",
                client_storage=memory_storage,
            )

    @pytest.mark.parametrize("policy_name", ["B2C/1_susi", "https://B2C_1_susi"])
    def test_policy_name_with_slashes_or_scheme_rejected(
        self, memory_storage: MemoryStore, policy_name: str
    ) -> None:
        """policy_name with slashes or scheme must be rejected."""
        with pytest.raises(ValueError, match="policy_name"):
            AzureB2CProvider(
                tenant_name="mytenant",
                policy_name=policy_name,
                client_id="client-id",
                client_secret="secret",
                required_scopes=["mcp-access"],
                base_url="https://myserver.com",
                jwt_signing_key="test-secret",
                client_storage=memory_storage,
            )

    def test_custom_domain_with_scheme_is_normalised(
        self, memory_storage: MemoryStore
    ) -> None:
        """custom_domain with https:// prefix must be stripped and accepted."""
        provider = AzureB2CProvider(
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


class TestAzureB2CCustomIdentifierUri:
    """Tests for the optional identifier_uri override."""

    def test_custom_identifier_uri_overrides_default(
        self, memory_storage: MemoryStore
    ) -> None:
        """An explicit identifier_uri must replace the derived B2C default."""
        custom_uri = "https://mycompany.com/api/mcp"
        provider = AzureB2CProvider(
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

    def test_custom_identifier_uri_used_in_scope_prefix(
        self, memory_storage: MemoryStore
    ) -> None:
        """Scopes must be prefixed with the custom identifier_uri."""
        custom_uri = "https://mycompany.com/api/mcp"
        provider = AzureB2CProvider(
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

        result = provider._prefix_scopes_for_azure(["mcp-access"])
        assert result == [f"{custom_uri}/mcp-access"]

    def test_default_identifier_uri_when_not_provided(
        self, memory_storage: MemoryStore
    ) -> None:
        """When identifier_uri is omitted the B2C default must be derived."""
        provider = AzureB2CProvider(
            tenant_name="mytenant",
            policy_name="B2C_1_susi",
            client_id="aabbccdd",
            client_secret="secret",
            required_scopes=["mcp-access"],
            base_url="https://myserver.com",
            jwt_signing_key="test-secret",
            client_storage=memory_storage,
        )

        assert provider.identifier_uri == "https://mytenant.onmicrosoft.com/aabbccdd"


class TestAzureB2COBORejection:
    """On-Behalf-Of flow must be explicitly blocked for B2C."""

    async def test_get_obo_credential_raises_not_implemented(
        self, provider: AzureB2CProvider
    ) -> None:
        """get_obo_credential() must raise NotImplementedError for B2C."""
        with pytest.raises(NotImplementedError, match="does not support.*OBO"):
            await provider.get_obo_credential(user_assertion="fake-token")
