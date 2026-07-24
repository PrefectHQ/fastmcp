"""`KeyValueResponseCacheStore`: the AsyncKeyValue-backed client response cache store.

The SDK's client response cache (SEP-2549) reads and writes through a pluggable
`ResponseCacheStore`. `KeyValueResponseCacheStore` adapts that contract onto the
`AsyncKeyValue` abstraction FastMCP already uses for its other state surfaces, so
a fleet of clients can share one backend (memory, Redis, etc.).

These tests cover the store in isolation (round-trip, partition isolation,
clear, allowlist) and end-to-end: two independent `fastmcp.Client` instances
sharing one adapter-backed store, where the second client serves the first's
cached `tools/list` with zero wire calls.
"""

from __future__ import annotations

import json
import time

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.client.caching import CacheConfig, CacheEntry, CacheKey
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp_types import ListToolsResult, Tool

from fastmcp import Client, FastMCP
from fastmcp.client.caching import (
    CACHEABLE_RESULT_MODELS,
    KeyValueResponseCacheStore,
    _CacheEnvelope,
)


def _tools_result() -> ListToolsResult:
    return ListToolsResult(
        tools=[Tool(name="add", input_schema={"type": "object"})],
        ttl_ms=60000,
        cache_scope="public",
    )


def _key(
    partition: str, *, method: str = "tools/list", params_key: str = ""
) -> CacheKey:
    # The coordinator packs scope/version/arm into CacheKey.partition as a JSON
    # array; mirror that shape so the derived string key is realistic.
    arm = json.dumps(["public", "2026-07-28", "srv", partition])
    return CacheKey(method, params_key, arm)


def _cached_server(ttl_ms: int = 60000) -> MCPServer:
    """An SDK MCPServer whose tools/list carries a positive ttlMs hint at 2026."""
    server = MCPServer(
        "cached", cache_hints={"tools/list": CacheHint(ttl_ms=ttl_ms, scope="public")}
    )

    @server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    return server


class TestRoundTrip:
    async def test_set_get_reconstructs_model(self):
        """A stored entry round-trips back to an equal result model object."""
        store = KeyValueResponseCacheStore()
        result = _tools_result()
        key = _key("p1")

        await store.set(
            key, CacheEntry(value=result, scope="public", expires_at=time.time() + 60)
        )
        got = await store.get(key)

        assert got is not None
        assert isinstance(got.value, ListToolsResult)
        assert got.value == result
        assert got.scope == "public"

    async def test_get_miss_returns_none(self):
        store = KeyValueResponseCacheStore()
        assert await store.get(_key("p1")) is None

    async def test_delete_removes_entry(self):
        store = KeyValueResponseCacheStore()
        key = _key("p1")
        await store.set(
            key,
            CacheEntry(
                value=_tools_result(), scope="public", expires_at=time.time() + 60
            ),
        )
        await store.delete(key)
        assert await store.get(key) is None

    async def test_private_scope_roundtrips(self):
        store = KeyValueResponseCacheStore()
        key = _key("p1")
        result = ListToolsResult(
            tools=[Tool(name="add", input_schema={"type": "object"})]
        )
        await store.set(
            key, CacheEntry(value=result, scope="private", expires_at=time.time() + 60)
        )
        got = await store.get(key)
        assert got is not None
        assert got.scope == "private"


class TestPartitionIsolation:
    async def test_two_partitions_do_not_bleed(self):
        """Entries written under different CacheKey.partition arms never collide."""
        store = KeyValueResponseCacheStore()
        result = _tools_result()

        await store.set(
            _key("tenant-a"),
            CacheEntry(value=result, scope="public", expires_at=time.time() + 60),
        )

        # A different partition is a distinct key: a clean miss, not a shared hit.
        assert await store.get(_key("tenant-b")) is None
        assert await store.get(_key("tenant-a")) is not None

    async def test_method_and_params_key_isolate(self):
        """Distinct method / params_key never collide in the derived string key."""
        store = KeyValueResponseCacheStore()
        result = _tools_result()
        await store.set(
            _key("p1", method="resources/read", params_key="file:///a"),
            CacheEntry(value=result, scope="public", expires_at=time.time() + 60),
        )
        assert (
            await store.get(_key("p1", method="resources/read", params_key="file:///b"))
            is None
        )
        assert await store.get(_key("p1", method="tools/list")) is None


class TestClear:
    async def test_clear_empties_and_keeps_collection_usable(self):
        store = KeyValueResponseCacheStore()
        key = _key("p1")
        await store.set(
            key,
            CacheEntry(
                value=_tools_result(), scope="public", expires_at=time.time() + 60
            ),
        )

        await store.clear()
        assert await store.get(key) is None

        # The collection remains usable for subsequent writes.
        await store.set(
            key,
            CacheEntry(
                value=_tools_result(), scope="public", expires_at=time.time() + 60
            ),
        )
        assert await store.get(key) is not None

    async def test_clear_scoped_to_own_collection(self):
        """Two adapters over one backend clear independently."""
        backend = MemoryStore()
        store_a = KeyValueResponseCacheStore(backend, collection="cache_a")
        store_b = KeyValueResponseCacheStore(backend, collection="cache_b")
        key = _key("p1")
        entry = CacheEntry(
            value=_tools_result(), scope="public", expires_at=time.time() + 60
        )

        await store_a.set(key, entry)
        await store_b.set(key, entry)

        await store_a.clear()

        assert await store_a.get(key) is None
        assert await store_b.get(key) is not None  # untouched


class TestAllowlist:
    async def test_unknown_type_tag_is_a_miss(self):
        """A stored envelope naming a type outside the allowlist is a miss, never imported."""
        store = KeyValueResponseCacheStore()
        key = _key("p1")
        forged = _CacheEnvelope(
            type_tag="EvilResult",
            value_json="{}",
            scope="public",
            expires_at=time.time() + 60,
        )
        await store._adapter.put(key=store._string_key(key), value=forged)

        assert await store.get(key) is None

    def test_allowlist_matches_cacheable_methods(self):
        """The allowlist covers exactly the SDK's cacheable result models."""
        assert set(CACHEABLE_RESULT_MODELS) == {
            "DiscoverResult",
            "ListPromptsResult",
            "ListResourceTemplatesResult",
            "ListResourcesResult",
            "ListToolsResult",
            "ReadResourceResult",
        }


class TestFastMCPConstruction:
    def test_custom_store_without_target_id_raises(self):
        """FastMCP requires a target_id for a custom shared store on an in-memory transport."""
        store = KeyValueResponseCacheStore()
        with pytest.raises(ValueError, match="requires CacheConfig.target_id"):
            Client(FastMCP("x"), cache=CacheConfig(store=store, partition="p"))

    def test_custom_store_without_partition_raises(self):
        """The SDK requires an explicit partition for any custom store."""
        with pytest.raises(ValueError, match="requires an explicit partition"):
            CacheConfig(store=KeyValueResponseCacheStore(), target_id="srv")

    def test_custom_store_builds_cache(self):
        store = KeyValueResponseCacheStore()
        config = CacheConfig(store=store, partition="p", target_id="srv")
        client = Client(FastMCP("x"), mode="auto", cache=config)
        assert client._response_cache is not None


class TestDistributedSharing:
    async def test_second_client_serves_first_clients_cache(self):
        """Two independent Clients sharing one adapter-backed store: client B's first
        list_tools is served from the entry client A populated, with zero wire calls."""
        backend = MemoryStore()
        store = KeyValueResponseCacheStore(backend)

        def make_client() -> Client:
            config = CacheConfig(store=store, partition="tenant-a", target_id="cached")
            return Client(_cached_server(), mode="auto", cache=config)

        async with make_client() as client_a:
            first = await client_a.list_tools()
            assert [t.name for t in first] == ["add"]

        async with make_client() as client_b:
            calls = {"n": 0}
            original = client_b.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client_b.session.list_tools = spy  # type: ignore[method-assign]
            served = await client_b.list_tools()

        assert calls["n"] == 0  # served from the shared store, no wire round-trip
        assert [t.name for t in served] == ["add"]

    async def test_distinct_partitions_do_not_share(self):
        """Two clients on the same store but different partitions each hit the wire."""
        backend = MemoryStore()
        store = KeyValueResponseCacheStore(backend)

        config_a = CacheConfig(store=store, partition="tenant-a", target_id="cached")
        async with Client(_cached_server(), mode="auto", cache=config_a) as client_a:
            await client_a.list_tools()

        config_b = CacheConfig(store=store, partition="tenant-b", target_id="cached")
        async with Client(_cached_server(), mode="auto", cache=config_b) as client_b:
            calls = {"n": 0}
            original = client_b.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client_b.session.list_tools = spy  # type: ignore[method-assign]
            await client_b.list_tools()

        assert calls["n"] == 1  # different partition -> not shared, hits the wire
