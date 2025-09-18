# Elasticsearch Cache Backend – Contrib Module for FastMCP

This backend plugs into `ResponseCachingMiddleware` and stores cache entries in Elasticsearch.

- **Package**: `fastmcp.contrib.middleware.caching.elasticsearch`
- **Class**: `ElasticsearchCache`
- **Works with**: `fastmcp.server.middleware.caching.ResponseCachingMiddleware`

---

## 📦 Installation

Install Elasticsearch async client and FastMCP caching extras:

```bash
uv add elasticsearch fastmcp[contrib-middleware-elasticsearch_cache]
# or
pip install elasticsearch fastmcp[contrib-middleware-elasticsearch_cache]
```

---

## 🚀 Quick Start

```python
from elasticsearch import AsyncElasticsearch
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.contrib.middleware.caching.elasticsearch import ElasticsearchCache

mcp = FastMCP(name="Cache Demo")

es = AsyncElasticsearch(hosts=["http://localhost:9200"])  # configure as needed
cache = ElasticsearchCache(elasticsearch_client=es, index="fastmcp-response-cache")

mcp.add_middleware(ResponseCachingMiddleware(cache_backend=cache))

# ... define tools/resources/prompts ...

# When using FastMCP.run(), ensure the event loop closes the ES client when done
# await es.close() in your shutdown path if you manage the loop yourself.
```

This enables caching for default MCP methods per `ResponseCachingMiddleware`:
- **tools**: `tools/call`, `tools/list`
- **resources**: `resources/read`, `resources/list`
- **prompts**: `prompts/get`, `prompts/list`

---

## ⚙️ Configuration

Constructor:

```python
ElasticsearchCache(
    elasticsearch_client: AsyncElasticsearch,
    index: str | None = None,
    mapping: dict[str, object] | None = None,
)
```

- **elasticsearch_client**: A live `AsyncElasticsearch` instance
- **index**: Target index name. Default: `"fastmcp-response-cache"`
- **mapping**: Custom index mapping. Default mapping is optimized for cache fields:

```json
{
  "properties": {
    "created_at": {"type": "date"},
    "expires_at": {"type": "date"},
    "ttl": {"type": "integer"},
    "collection": {"type": "keyword"},
    "key": {"type": "keyword"},
    "value": {"type": "keyword", "index": false}
  }
}
```

Notes:
- Values are stored as JSON strings under `value` (non-indexed) for size and simplicity.
- Each cache entry is keyed by `f"{collection}:{key}"` and saved as the document `_id`.

---

## 🔧 Lifecycle and Maintenance

The backend lazily creates the index on first use via `setup()`; calls are internally guarded by an async lock.

Available maintenance helpers:

```python
# Drop the entire cache index
await cache.clear()

# Delete only expired entries
await cache.cull()
```

- `clear()` deletes the index.
- `cull()` issues a `delete_by_query` filtering `expires_at < now`.

---

## ✅ Compatibility

- Works with the core cache entry types defined by `ResponseCachingMiddleware`:
  - `tools/call` (tool results)
  - `resources/read` (resource contents)
  - `prompts/get` (prompt result)
  - List endpoints for tools, resources, prompts
- Uses Pydantic discriminators to serialize/validate cache entries round-trip.

---

## 🛡️ Production Notes

- Ensure your ES cluster has sufficient storage.
- Consider a dedicated index per environment or service instance via the `index` parameter.
- Close the `AsyncElasticsearch` client on shutdown to avoid warnings and resource leaks.

---

## 🧪 Manual Cache Lifecycle

```python
import asyncio
from elasticsearch import AsyncElasticsearch
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.contrib.middleware.caching.elasticsearch import ElasticsearchCache

async def main():
    es = AsyncElasticsearch(hosts=["http://localhost:9200"])
    cache = ElasticsearchCache(es)
    await cache.setup()  # optional; auto-runs on first access

    # Simulate storing an entry through middleware flow
    # In practice, the middleware calls set_entry() for you.

    await cache.cull()
    await es.close()

asyncio.run(main())
```

---

## 📚 Imports

```python
from fastmcp.contrib.middleware.caching.elasticsearch import ElasticsearchCache
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
```

This module is part of `fastmcp.contrib`. See `docs/servers/middleware.mdx` and `docs/servers/tools.mdx` for response caching details.
