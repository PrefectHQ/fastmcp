import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated, Any

from elasticsearch import AsyncElasticsearch
from pydantic import Field, TypeAdapter

from fastmcp.server.middleware.caching import (
    CacheEntryTypes,
    CacheProtocol,
)

DEFAULT_MAPPING = {
    "properties": {
        "created_at": {
            "type": "date",
        },
        "expires_at": {
            "type": "date",
        },
        "ttl": {
            "type": "integer",
        },
        "collection": {
            "type": "keyword",
        },
        "key": {
            "type": "keyword",
        },
        "value": {
            "type": "keyword",
            "index": False,
            "doc_values": False,
            "ignore_above": 256,
        },
    },
}


class ElasticsearchCache(CacheProtocol):
    """A cache client that uses Elasticsearch."""

    setup_called: bool
    setup_lock: asyncio.Lock
    cached_entry_typeadapter: TypeAdapter[CacheEntryTypes]

    def __init__(
        self,
        elasticsearch_client: AsyncElasticsearch,
        index: str | None = None,
        mapping: dict[str, Any] | None = None,
    ):
        """Initialize the Elasticsearch cache.

        Args:
            elasticsearch_client: The Elasticsearch client to use.
            index: The index to use for the cache. Defaults to "fastmcp-response-cache".
            mapping: The mapping to use for the cache. Defaults to the default mapping.
        """
        self.elasticsearch_client = elasticsearch_client
        self.index = index or "fastmcp-response-cache"
        self.mapping = mapping or DEFAULT_MAPPING
        self.setup_called = False
        self.setup_lock = asyncio.Lock()
        self.cached_entry_typeadapter = TypeAdapter(
            Annotated[CacheEntryTypes, Field(discriminator="collection")],
        )

    async def get_entry(self, collection: str, key: str) -> CacheEntryTypes | None:
        if not self.setup_called:
            await self.setup()

        collection_key = self.make_collection_key(collection=collection, key=key)

        elasticsearch_response = await self.elasticsearch_client.options(
            ignore_status=404
        ).get(index=self.index, id=collection_key)

        if (
            elasticsearch_response.body is None
            or elasticsearch_response.body.get("error")
            or not elasticsearch_response.body.get("found")
        ):
            return None

        source = elasticsearch_response.body.get("_source")

        source["value"] = json.loads(source["value"])

        cache_entry = self.cached_entry_typeadapter.validate_python(source)
        if cache_entry.is_expired():
            await self.delete(collection=collection, key=key)
            return None

        return cache_entry

    async def set_entry(
        self,
        cache_entry: CacheEntryTypes,
    ) -> None:
        if not self.setup_called:
            await self.setup()

        collection_key = self.make_collection_key(
            collection=cache_entry.collection, key=cache_entry.key
        )

        document = json.loads(cache_entry.model_dump_json(serialize_as_any=True))

        document["value"] = json.dumps(document["value"])

        await self.elasticsearch_client.index(
            index=self.index,
            id=collection_key,
            body=document,
        )

    async def delete(self, collection: str, key: str) -> None:
        collection_key = self.make_collection_key(collection=collection, key=key)

        await self.elasticsearch_client.options(ignore_status=404).delete(
            index=self.index, id=collection_key
        )

    async def setup(self) -> None:
        if self.setup_called:
            return

        async with self.setup_lock:
            if self.setup_called:
                return

            if await self.elasticsearch_client.options(
                ignore_status=404
            ).indices.exists(index=self.index):
                return

            await self.elasticsearch_client.options(ignore_status=404).indices.create(
                index=self.index,
                mappings=self.mapping,
            )

            self.setup_called = True

    async def clear(self) -> None:
        await self.elasticsearch_client.options(ignore_status=404).indices.delete(
            index=self.index,
        )

    async def cull(self) -> None:
        await self.elasticsearch_client.options(ignore_status=404).delete_by_query(
            index=self.index,
            body={
                "query": {
                    "range": {
                        "expires_at": {"lt": datetime.now(tz=timezone.utc).timestamp()},
                    },
                },
            },
        )
