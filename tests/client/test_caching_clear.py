from key_value.aio.stores.memory import MemoryStore
from mcp.client.caching import CacheEntry, CacheKey
from mcp.types import ListToolsResult

from fastmcp.client.caching import KeyValueResponseCacheStore


async def test_clear_deletes_beyond_10000_entries():
    store = KeyValueResponseCacheStore(storage=MemoryStore())
    entry = CacheEntry(
        value=ListToolsResult(tools=[]), scope="private", expires_at=None
    )
    keys = [
        CacheKey(method="tools/list", params_key=str(i), partition="tenant-a")
        for i in range(10_050)
    ]

    for key in keys:
        await store.set(key, entry)

    await store.clear()

    remaining = [key for key in keys if await store.get(key) is not None]
    assert remaining == []
