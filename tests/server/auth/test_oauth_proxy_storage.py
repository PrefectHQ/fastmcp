"""Tests for OAuth proxy with persistent storage."""

import asyncio
import tempfile
import warnings
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from inline_snapshot import snapshot
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp import settings
from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy import proxy as proxy_module


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
    async def temp_storage(self) -> AsyncGenerator[FileTreeStore, None]:
        """Create file-based storage for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                yield FileTreeStore(data_directory=Path(temp_dir))

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
        """Test that OAuthProxyClient is created with redirect URI patterns."""
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

        # Get client back - should be OAuthProxyClient
        client = await proxy.get_client("test-proxy-client")
        assert client is not None

        # OAuthProxyClient should validate dynamic localhost ports
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
                "application_type": "native",
                "issuer": None,
                "client_id": "structured-client",
                "client_secret": None,
                "client_id_issued_at": None,
                "client_secret_expires_at": None,
                "allowed_redirect_uri_patterns": None,
                "cimd_document": None,
                "cimd_fetched_at": None,
            }
        )


def _entry_files(directory: Path) -> list[Path]:
    """On-disk entry files, excluding per-collection index files."""
    return [p for p in directory.rglob("*.json") if not p.name.endswith("-info.json")]


class TestOAuthProxyDefaultStoreCull:
    """Expired entries in the proxy-created default file store must be reaped.

    Regression tests for https://github.com/PrefectHQ/fastmcp/issues/5718:
    the default FileTreeStore was never culled, so expired OAuth state files
    accumulated until the volume ran out of inodes.
    """

    @pytest.fixture
    def jwt_verifier(self):
        verifier = Mock()
        verifier.required_scopes = ["read", "write"]
        verifier.verify_token = AsyncMock(return_value=None)
        return verifier

    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        """Point the default storage root at a temp dir for this test."""
        monkeypatch.setattr(settings, "home", tmp_path)
        return tmp_path

    def create_proxy(self, jwt_verifier, storage=None) -> OAuthProxy:
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

    async def _drain_cull(self, proxy: OAuthProxy) -> None:
        if proxy._cull_task is not None:
            await proxy._cull_task

    async def test_cull_reaps_expired_entries_only(self, jwt_verifier, isolated_home):
        proxy = self.create_proxy(jwt_verifier)
        store = proxy._default_file_store
        assert store is not None

        await store.put("expired", {"v": 1}, collection="c", ttl=0.05)
        await store.put("live", {"v": 2}, collection="c", ttl=3600)
        await store.put("no-ttl", {"v": 3}, collection="c")
        await asyncio.sleep(0.2)
        assert len(_entry_files(isolated_home)) == 3

        proxy._maybe_cull_expired_state()
        await self._drain_cull(proxy)

        assert len(_entry_files(isolated_home)) == 2
        assert await store.get("expired", collection="c") is None
        assert await store.get("live", collection="c") == {"v": 2}
        assert await store.get("no-ttl", collection="c") == {"v": 3}

    async def test_cull_is_noop_with_custom_storage(self, jwt_verifier, isolated_home):
        proxy = self.create_proxy(jwt_verifier, storage=MemoryStore())
        assert proxy._default_file_store is None
        proxy._maybe_cull_expired_state()
        assert proxy._cull_task is None

    async def test_construction_does_not_cull(self, jwt_verifier, isolated_home):
        proxy = self.create_proxy(jwt_verifier)
        assert proxy._cull_task is None

    async def test_cull_is_throttled_and_resumes_after_interval(
        self, jwt_verifier, isolated_home
    ):
        """A long-running server keeps culling, but at most once per interval."""
        proxy = self.create_proxy(jwt_verifier)
        store = proxy._default_file_store
        assert store is not None

        proxy._maybe_cull_expired_state()
        await self._drain_cull(proxy)
        first = proxy._cull_task

        await store.put("stale", {"v": 1}, collection="c", ttl=0.05)
        await asyncio.sleep(0.2)

        # Within the interval: no new cull.
        proxy._maybe_cull_expired_state()
        assert proxy._cull_task is first
        assert len(_entry_files(isolated_home)) == 1

        # Once the interval has passed, the next access culls again.
        assert proxy._last_cull_started is not None
        proxy._last_cull_started -= proxy_module._STATE_CULL_INTERVAL_SECONDS
        proxy._maybe_cull_expired_state()
        assert proxy._cull_task is not first
        await self._drain_cull(proxy)
        assert _entry_files(isolated_home) == []

    async def test_authorize_triggers_cull(self, jwt_verifier, isolated_home):
        """The OAuth write path reaps state left behind by a previous run."""
        first = self.create_proxy(jwt_verifier)
        store = first._default_file_store
        assert store is not None
        await store.put("stale", {"v": 1}, collection="c", ttl=0.05)
        await asyncio.sleep(0.2)

        proxy = self.create_proxy(jwt_verifier)
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )
        await proxy.register_client(client)
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="s",
            code_challenge="c" * 43,
            scopes=["read"],
        )
        await proxy.authorize(client, params)
        await self._drain_cull(proxy)

        assert await store.get("stale", collection="c") is None
