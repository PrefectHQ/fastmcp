"""A client response cache store backed by AsyncKeyValue.

The MCP SDK's client response cache (SEP-2549) reads and writes through a
pluggable `ResponseCacheStore` protocol; the default is a per-client in-memory
LRU. This module adapts that protocol onto the `AsyncKeyValue` key-value
abstraction FastMCP already uses for its other state-management surfaces (the
event store, the OAuth proxy, the response-caching middleware), so a fleet of
FastMCP clients — for example a set of proxy replicas — can share one
Redis-backed response cache.

Because a shared store mingles cached responses across principals, the SDK
requires an explicit `partition` on any custom store (and FastMCP additionally
requires a `target_id`). The partition is folded into every stored key so
entries can never collide or leak across authorization contexts, and the
adapter round-trips each result through a small type-tagged envelope validated
against an allowlist of cacheable result models — a stored value that names an
unknown type is treated as a miss, never imported by name.

Example:
    ```python
    from fastmcp import Client
    from fastmcp.client.caching import KeyValueResponseCacheStore
    from mcp.client.caching import CacheConfig
    from key_value.aio.stores.redis import RedisStore

    store = KeyValueResponseCacheStore(storage=RedisStore(url="redis://localhost"))
    config = CacheConfig(store=store, partition="tenant-a", target_id="weather-api")
    client = Client("https://example.com/mcp", mode="auto", cache=config)
    ```
"""

from __future__ import annotations

import hashlib
import time
from types import UnionType
from typing import Literal, get_args

from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.protocols.key_value import (
    AsyncDestroyCollectionProtocol,
    AsyncEnumerateKeysProtocol,
)
from key_value.aio.stores.memory import MemoryStore
from mcp.client.caching import CacheEntry, CacheKey
from mcp_types import CacheableResult
from mcp_types.methods import MONOLITH_RESULTS

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import FastMCPBaseModel

logger = get_logger(__name__)

DEFAULT_CACHE_COLLECTION = "fastmcp_response_cache"
"""Collection namespace owned by one adapter instance; `clear()` never reaches beyond it."""


def _cacheable_result_models() -> dict[str, type[CacheableResult]]:
    """Allowlist of `{class name: model}` for every cacheable result type.

    Derived from `MONOLITH_RESULTS` (the SDK's per-method result registry) so it
    tracks the CACHEABLE_METHODS surface automatically. The class name is the
    type tag written into the envelope; reconstruction looks the model up here
    rather than importing an arbitrary name from store contents.
    """
    models: dict[str, type[CacheableResult]] = {}
    for row in MONOLITH_RESULTS.values():
        arms = get_args(row) if isinstance(row, UnionType) else (row,)
        for arm in arms:
            if isinstance(arm, type) and issubclass(arm, CacheableResult):
                models[arm.__name__] = arm
    return models


CACHEABLE_RESULT_MODELS = _cacheable_result_models()
"""Type tag -> model class allowlist for envelope reconstruction."""


class _CacheEnvelope(FastMCPBaseModel):
    """Serializable form of a `CacheEntry` for a remote store.

    A `CacheEntry.value` is a cacheable result model; a remote store cannot hold
    it as an object, so it is serialized to `value_json` under a `type_tag`
    (the model class name) and reconstructed against the allowlist on read. The
    freshness/sharing metadata (`scope`, `expires_at`) round-trips alongside it.
    """

    type_tag: str
    value_json: str
    scope: str
    expires_at: float | None


class KeyValueResponseCacheStore:
    """A `ResponseCacheStore` backed by any `AsyncKeyValue` store.

    Implements the SDK client response cache contract (`get`/`set`/`delete`/
    `clear`) over the key-value abstraction FastMCP already uses elsewhere, so a
    distributed deployment can point every client at one shared backend (memory,
    Redis, etc.). Pass an instance as `CacheConfig(store=...)`; the SDK requires
    an explicit `partition` on any custom store, and FastMCP additionally
    requires a `target_id`.

    Each adapter instance owns one collection (`collection`), so `clear()` only
    affects its own namespace and never another tenant's data. `clear()` needs
    the backend to support collection destruction or key enumeration; against a
    backend that supports neither it is a no-op and entries age out by TTL (a
    warning is logged once).

    The SDK wraps every store call defensively — a raised operation degrades to
    a cache miss rather than failing the request — so this adapter does not
    re-wrap its own operations.

    Args:
        storage: The `AsyncKeyValue` backend. Defaults to an in-process `MemoryStore`.
        collection: Collection namespace for this adapter's entries.
    """

    def __init__(
        self,
        storage: AsyncKeyValue | None = None,
        *,
        collection: str = DEFAULT_CACHE_COLLECTION,
    ) -> None:
        self._storage: AsyncKeyValue = storage or MemoryStore()
        self._collection = collection
        self._adapter: PydanticAdapter[_CacheEnvelope] = PydanticAdapter[
            _CacheEnvelope
        ](
            key_value=self._storage,
            pydantic_model=_CacheEnvelope,
            default_collection=collection,
        )
        self._warned_clear_unsupported = False

    @staticmethod
    def _string_key(key: CacheKey) -> str:
        """Derive a stable store key from every `CacheKey` field.

        `CacheKey` is `(method, params_key, partition)`, where the coordinator has
        already packed scope, negotiated protocol version, server arm id, and the
        caller's partition into the `partition` field as a JSON array. Every field
        is folded into the digest, so entries cannot collide across partitions,
        protocol eras, or servers. The fields are length-prefixed before hashing
        so no two distinct field tuples can produce the same pre-image.
        """
        parts = [key.method, key.params_key, key.partition]
        preimage = "".join(f"{len(part)}:{part}" for part in parts)
        return hashlib.sha256(preimage.encode("utf-8")).hexdigest()

    async def get(self, key: CacheKey) -> CacheEntry | None:
        envelope = await self._adapter.get(key=self._string_key(key))
        if envelope is None:
            return None
        model = CACHEABLE_RESULT_MODELS.get(envelope.type_tag)
        if model is None:
            # An unknown tag is never imported by name; a wrong-shape entry is a miss.
            return None
        value = model.model_validate_json(envelope.value_json)
        scope: Literal["public", "private"] = (
            "public" if envelope.scope == "public" else "private"
        )
        return CacheEntry(value=value, scope=scope, expires_at=envelope.expires_at)

    async def set(self, key: CacheKey, entry: CacheEntry) -> None:
        value = entry.value
        if not isinstance(value, CacheableResult):
            return
        type_tag = type(value).__name__
        if type_tag not in CACHEABLE_RESULT_MODELS:
            return
        envelope = _CacheEnvelope(
            type_tag=type_tag,
            value_json=value.model_dump_json(by_alias=True),
            scope=entry.scope,
            expires_at=entry.expires_at,
        )
        ttl = self._entry_ttl(entry)
        await self._adapter.put(key=self._string_key(key), value=envelope, ttl=ttl)

    async def delete(self, key: CacheKey) -> None:
        await self._adapter.delete(key=self._string_key(key))

    async def clear(self) -> None:
        """Clear this adapter's collection only.

        Prefers deleting each enumerated key (which leaves the collection
        usable), and falls back to whole-collection destruction. Against a
        backend that supports neither, this is a no-op (entries age out by TTL)
        and a warning is logged once. Either path is scoped to this adapter's
        own collection, so a shared store's other tenants are never touched.
        """
        storage = self._storage
        if isinstance(storage, AsyncEnumerateKeysProtocol):
            keys = await storage.keys(collection=self._collection)
            for stored_key in keys:
                await storage.delete(key=stored_key, collection=self._collection)
            return
        if isinstance(storage, AsyncDestroyCollectionProtocol):
            await storage.destroy_collection(collection=self._collection)
            return
        if not self._warned_clear_unsupported:
            self._warned_clear_unsupported = True
            logger.warning(
                "Response cache store backend %s supports neither collection "
                "destruction nor key enumeration; clear() is a no-op and entries "
                "will age out by TTL.",
                type(storage).__name__,
            )

    def _entry_ttl(self, entry: CacheEntry) -> float | None:
        """Seconds until the entry's own expiry, so the backend can evict it independently.

        The SDK gates freshness on `expires_at`, but a shared backend should not
        retain a stale entry indefinitely; a store TTL lets it reclaim space. A
        non-positive remaining lifetime stores with no backend TTL (the SDK will
        still treat the already-stale entry as a miss).
        """
        if entry.expires_at is None:
            return None
        remaining = entry.expires_at - time.time()
        return remaining if remaining > 0 else None
