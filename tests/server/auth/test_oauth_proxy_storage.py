"""Tests for OAuth proxy with persistent storage."""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from diskcache.core import tempfile
from inline_snapshot import snapshot
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.disk import MultiDiskStore
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy


class TestOAuthProxyStorage:
    """Tests for OAuth proxy client storage functionality."""

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier."""
        verifier = Mock()
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = AsyncMock(return_value=None)
        return verifier

    @pytest.fixture
    async def temp_storage(self) -> AsyncGenerator[MultiDiskStore, None]:
        """Create file-based storage for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            disk_store = MultiDiskStore(base_directory=Path(temp_dir))
            yield disk_store
            await disk_store.close()

    @pytest.fixture
    def memory_storage(self) -> MemoryStore:
        """Create in-memory storage for testing."""
        return MemoryStore()

    def create_proxy(
        self, jwt_verifier: TokenVerifier, storage: AsyncKeyValue | None = None
    ) -> OAuthProxy:
        """Create an OAuth proxy with specified storage."""
        return OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            redirect_path="/auth/callback",
            client_storage=storage,
            jwt_signing_key="test-secret",
        )

    async def test_register_and_get_client(self, jwt_verifier, temp_storage):
        """Test registering and retrieving a client."""
        proxy = self.create_proxy(jwt_verifier, storage=temp_storage)

        # Register client
        client_info = OAuthClientInformationFull(
            client_id="test-client-123",
            client_secret="secret-456",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
            grant_types=["authorization_code", "refresh_token"],
            scope="read write",
        )
        await proxy.register_client(client_info)

        # Get client back
        client = await proxy.get_client("test-client-123")
        assert client is not None
        assert client.client_id == "test-client-123"
        # Proxy uses token_endpoint_auth_method="none", so client_secret is not stored
        assert client.client_secret is None
        assert client.scope == "read write"

    async def test_client_persists_across_proxy_instances(
        self, jwt_verifier: TokenVerifier, temp_storage: AsyncKeyValue
    ):
        """Test that clients persist when proxy is recreated."""
        # First proxy registers client
        proxy1 = self.create_proxy(jwt_verifier, storage=temp_storage)
        client_info = OAuthClientInformationFull(
            client_id="persistent-client",
            client_secret="persistent-secret",
            redirect_uris=[AnyUrl("http://localhost:9999/callback")],
            scope="openid profile",
        )
        await proxy1.register_client(client_info)

        # Second proxy can retrieve it
        proxy2 = self.create_proxy(jwt_verifier, storage=temp_storage)
        client = await proxy2.get_client("persistent-client")
        assert client is not None
        # Proxy uses token_endpoint_auth_method="none", so client_secret is not stored
        assert client.client_secret is None
        assert client.scope == "openid profile"

    async def test_nonexistent_client_returns_none(
        self, jwt_verifier: TokenVerifier, temp_storage: AsyncKeyValue
    ):
        """Test that requesting non-existent client returns None."""
        proxy = self.create_proxy(jwt_verifier, storage=temp_storage)
        client = await proxy.get_client("does-not-exist")
        assert client is None

    async def test_proxy_dcr_client_redirect_validation(
        self, jwt_verifier: TokenVerifier, temp_storage: AsyncKeyValue
    ):
        """Test that ProxyDCRClient is created with redirect URI patterns."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            allowed_client_redirect_uris=["http://localhost:*"],
            client_storage=temp_storage,
            jwt_signing_key="test-secret",
        )

        client_info = OAuthClientInformationFull(
            client_id="test-proxy-client",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await proxy.register_client(client_info)

        # Get client back - should be ProxyDCRClient
        client = await proxy.get_client("test-proxy-client")
        assert client is not None

        # ProxyDCRClient should validate dynamic localhost ports
        validated = client.validate_redirect_uri(
            AnyUrl("http://localhost:12345/callback")
        )
        assert validated is not None

    async def test_in_memory_storage_option(self, jwt_verifier):
        """Test using in-memory storage explicitly."""
        storage = MemoryStore()
        proxy = self.create_proxy(jwt_verifier, storage=storage)

        client_info = OAuthClientInformationFull(
            client_id="memory-client",
            client_secret="memory-secret",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await proxy.register_client(client_info)

        client = await proxy.get_client("memory-client")
        assert client is not None

        # Create new proxy with same storage instance
        proxy2 = self.create_proxy(jwt_verifier, storage=storage)
        client2 = await proxy2.get_client("memory-client")
        assert client2 is not None

        # But new storage instance won't have it
        proxy3 = self.create_proxy(jwt_verifier, storage=MemoryStore())
        client3 = await proxy3.get_client("memory-client")
        assert client3 is None

    async def test_storage_data_structure(self, jwt_verifier, temp_storage):
        """Test that storage uses proper structured format."""
        proxy = self.create_proxy(jwt_verifier, storage=temp_storage)

        client_info = OAuthClientInformationFull(
            client_id="structured-client",
            client_secret="secret",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await proxy.register_client(client_info)

        # Check raw storage data
        raw_data = await temp_storage.get(
            collection="mcp-oauth-proxy-clients", key="structured-client"
        )
        assert raw_data is not None
        assert raw_data == snapshot(
            {
                "redirect_uris": ["http://localhost:8080/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "read write",
                "client_name": None,
                "client_uri": None,
                "logo_uri": None,
                "contacts": None,
                "tos_uri": None,
                "policy_uri": None,
                "jwks_uri": None,
                "jwks": None,
                "software_id": None,
                "software_version": None,
                "client_id": "structured-client",
                "client_secret": None,
                "client_id_issued_at": None,
                "client_secret_expires_at": None,
                "allowed_redirect_uri_patterns": None,
            }
        )
