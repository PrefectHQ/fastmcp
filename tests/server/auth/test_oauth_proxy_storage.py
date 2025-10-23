"""Tests for OAuth proxy with persistent storage."""

import platform
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from diskcache.core import tempfile
from inline_snapshot import snapshot
from key_value.aio.stores.disk import DiskStore, MultiDiskStore
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

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

    async def test_default_storage_is_platform_appropriate(self, jwt_verifier):
        """Test that proxy defaults to appropriate storage for platform."""
        proxy = self.create_proxy(jwt_verifier, storage=None)
        if platform.system() == "Linux":
            # Linux: no keyring support, use MemoryStore
            assert isinstance(proxy._client_storage, MemoryStore)
        else:
            # Mac/Windows: keyring available, use DiskStore
            assert isinstance(proxy._client_storage, DiskStore)

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
                "client_secret": "secret",
                "client_id_issued_at": None,
                "client_secret_expires_at": None,
                "allowed_redirect_uri_patterns": None,
            }
        )


class TestOAuthProxyKeyring:
    """Tests for OAuth proxy keyring integration.

    All tests mock keyring to prevent pollution of the OS keyring during testing.
    """

    @pytest.fixture
    def jwt_verifier(self):
        """Create a mock JWT verifier."""
        verifier = Mock()
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = AsyncMock(return_value=None)
        return verifier

    @pytest.fixture
    def memory_storage(self) -> MemoryStore:
        """Create in-memory storage for testing."""
        return MemoryStore()

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_keyring_used_on_mac_windows(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test that keyring is used on Mac/Windows platforms."""
        # Simulate Mac platform
        mock_platform.return_value = "Darwin"

        # Mock keyring to return None (first time, no existing key)
        mock_keyring.get_password.return_value = None

        # Create proxy without explicit keys (should use keyring)
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="test-keyring-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=memory_storage,
        )

        # Trigger JWT initialization to activate keyring calls
        await proxy._ensure_jwt_initialized()

        # Verify keyring was accessed for both JWT and encryption keys
        assert mock_keyring.get_password.call_count == 2
        assert mock_keyring.set_password.call_count == 2

        # Verify service name and key names
        jwt_calls = [
            call
            for call in mock_keyring.get_password.call_args_list
            if "jwt-signing" in str(call)
        ]
        encryption_calls = [
            call
            for call in mock_keyring.get_password.call_args_list
            if "token-encryption" in str(call)
        ]

        assert len(jwt_calls) == 1
        assert len(encryption_calls) == 1

        # Check that keys were stored with correct service name
        set_calls = mock_keyring.set_password.call_args_list
        for call in set_calls:
            assert call[0][0] == "fastmcp"  # service name
            assert "test-keyring-client" in call[0][1]  # namespace in key name

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_keyring_skipped_on_linux(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test that keyring is skipped on Linux platforms."""
        # Simulate Linux platform
        mock_platform.return_value = "Linux"

        # Create proxy without explicit keys
        OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="linux-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=memory_storage,
        )

        # Keyring should never be accessed on Linux
        mock_keyring.get_password.assert_not_called()
        mock_keyring.set_password.assert_not_called()

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_explicit_keys_bypass_keyring(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test that explicit keys bypass keyring entirely."""
        mock_platform.return_value = "Darwin"

        # Create proxy with explicit keys
        OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="explicit-keys-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            jwt_signing_key="my-custom-jwt-key",
            token_encryption_key="my-custom-encryption-key",
            client_storage=memory_storage,
        )

        # Keyring should never be accessed when explicit keys provided
        mock_keyring.get_password.assert_not_called()
        mock_keyring.set_password.assert_not_called()

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_keyring_namespace_isolation(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test that different upstream client IDs create isolated keyring entries."""
        mock_platform.return_value = "Darwin"
        mock_keyring.get_password.return_value = None

        # Create first proxy with client-A
        OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="client-A",
            upstream_client_secret="secret-A",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=memory_storage,
        )

        # Reset mock to track second proxy separately
        mock_keyring.reset_mock()
        mock_keyring.get_password.return_value = None

        # Create second proxy with client-B
        OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="client-B",
            upstream_client_secret="secret-B",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=MemoryStore(),  # Different storage instance
        )

        # Verify that client-B keys were stored with different namespace
        set_calls = mock_keyring.set_password.call_args_list
        for call in set_calls:
            assert call[0][0] == "fastmcp"
            assert "client-B" in call[0][1]  # Namespace includes client-B
            assert "client-A" not in call[0][1]  # Not client-A

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_keyring_retrieves_existing_keys(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test that existing keyring keys are retrieved and reused."""
        mock_platform.return_value = "Darwin"

        # Mock existing keys in keyring
        def get_password_side_effect(service, key):
            if "jwt-signing" in key:
                return "existing-jwt-key-base64"
            elif "token-encryption" in key:
                return "existing-encryption-key-base64"
            return None

        mock_keyring.get_password.side_effect = get_password_side_effect

        # Create proxy - should retrieve existing keys
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="existing-keys-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=memory_storage,
        )

        # Trigger JWT initialization
        await proxy._ensure_jwt_initialized()

        # Should retrieve but not set new keys
        assert mock_keyring.get_password.call_count == 2
        mock_keyring.set_password.assert_not_called()

    @patch("fastmcp.utilities.key_management.platform.system")
    @patch("fastmcp.utilities.key_management.keyring")
    async def test_keyring_failure_uses_ephemeral_keys(
        self, mock_keyring, mock_platform, jwt_verifier, memory_storage
    ):
        """Test graceful fallback to ephemeral keys when keyring fails."""
        mock_platform.return_value = "Darwin"

        # Simulate keyring failure
        mock_keyring.get_password.side_effect = Exception("Keyring backend unavailable")

        # Should not raise - should fall back to ephemeral keys
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
            upstream_token_endpoint="https://github.com/login/oauth/access_token",
            upstream_client_id="fallback-client",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://myserver.com",
            client_storage=memory_storage,
        )

        # Proxy should be created successfully despite keyring failure
        assert proxy is not None
