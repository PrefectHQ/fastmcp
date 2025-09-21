"""Tests for OAuth proxy with persistent storage."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.utilities.storage import InMemoryStorage, JSONFileStorage


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
    def temp_storage(self, tmp_path: Path) -> JSONFileStorage:
        """Create file-based storage for testing."""
        return JSONFileStorage(tmp_path / "oauth-clients")

    @pytest.fixture
    def memory_storage(self) -> InMemoryStorage:
        """Create in-memory storage for testing."""
        return InMemoryStorage()

    def create_proxy(self, jwt_verifier, storage=None) -> OAuthProxy:
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
        )

    async def test_default_storage_is_file_based(self, jwt_verifier):
        """Test that proxy defaults to file-based storage."""
        proxy = self.create_proxy(jwt_verifier, storage=None)
        assert isinstance(proxy._client_storage, JSONFileStorage)

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
        assert client.client_secret == "secret-456"
        assert client.scope == "read write"

    async def test_client_persists_across_proxy_instances(
        self, jwt_verifier, temp_storage
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
        assert client.client_secret == "persistent-secret"
        assert client.scope == "openid profile"

    async def test_nonexistent_client_returns_none(self, jwt_verifier, temp_storage):
        """Test that requesting non-existent client returns None."""
        proxy = self.create_proxy(jwt_verifier, storage=temp_storage)
        client = await proxy.get_client("does-not-exist")
        assert client is None

    async def test_proxy_dcr_client_redirect_validation(
        self, jwt_verifier, temp_storage
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
        storage = InMemoryStorage()
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
        proxy3 = self.create_proxy(jwt_verifier, storage=InMemoryStorage())
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
        raw_data = await temp_storage.get("structured-client")
        assert raw_data is not None
        assert "client" in raw_data
        assert "allowed_redirect_uri_patterns" in raw_data

    async def test_cleanup_old_clients(self, jwt_verifier, temp_storage):
        """Test cleanup of old clients using storage's cleanup method."""
        import json
        import time

        proxy = self.create_proxy(jwt_verifier, storage=temp_storage)

        # Register some clients
        client1 = OAuthClientInformationFull(
            client_id="old-client",
            client_secret="secret1",
            redirect_uris=[AnyUrl("http://localhost:8080/callback")],
        )
        await proxy.register_client(client1)

        client2 = OAuthClientInformationFull(
            client_id="recent-client",
            client_secret="secret2",
            redirect_uris=[AnyUrl("http://localhost:9090/callback")],
        )
        await proxy.register_client(client2)

        # Manually make the first client old by modifying the file directly
        old_client_path = temp_storage._get_file_path("old-client")
        wrapper = json.loads(old_client_path.read_text())
        wrapper["timestamp"] = time.time() - (35 * 24 * 60 * 60)  # 35 days old
        old_client_path.write_text(json.dumps(wrapper))

        # Run cleanup directly on storage
        removed_count = await temp_storage.cleanup_old_entries(
            max_age_seconds=30 * 24 * 60 * 60
        )
        assert removed_count == 1

        # Old client should be gone
        assert await proxy.get_client("old-client") is None

        # Recent client should still exist
        assert await proxy.get_client("recent-client") is not None
