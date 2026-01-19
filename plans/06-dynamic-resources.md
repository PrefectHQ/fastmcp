# Dynamic Resources

## Problem

Tools that return large results bloat agent context:
- Search results: 10MB of JSON
- Generated files: large CSV, PDF, images
- Log files: thousands of lines
- Database dumps: extensive data

Current workarounds:
- Return truncated data (loses information)
- Return everything (wastes tokens)
- Write to filesystem manually (not portable, cleanup issues)

The GitHub MCP server pioneered a pattern: write large results to a file and return a reference. Clients can then read/search the file as needed.

## Solution

Allow tools to create ephemeral resources at runtime that clients can read, search, and navigate without bloating context.

## API

### Basic Usage

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("server")

@mcp.tool
async def search(query: str, ctx: Context) -> str:
    # Perform search - returns 10MB of data
    results = do_search(query)

    # Create ephemeral resource instead of returning everything
    uri = await ctx.create_resource(
        f"results://search/{ctx.request_id}",
        content=results,
        ttl=3600,  # Auto-cleanup after 1 hour
    )

    return f"Found {len(results)} results. Access at {uri}"
```

Client can then:
```python
result = await client.call_tool("search", {"query": "python async"})
# "Found 1000 results. Access at results://search/abc123"

# Read the full results
data = await client.read_resource("results://search/abc123")

# Or use resource tools (if ResourceToolsProvider is enabled)
preview = await client.call_tool("read_resource", {
    "uri": "results://search/abc123",
    "limit": 10
})
```

### With Metadata

```python
uri = await ctx.create_resource(
    "results://analysis/output.json",
    content={"data": [...], "summary": "..."},
    name="Analysis Results",
    description="Detailed analysis output",
    mime_type="application/json",
    ttl=7200,  # 2 hours
)
```

### Binary Content

```python
@mcp.tool
async def generate_pdf(ctx: Context) -> str:
    pdf_bytes = create_pdf()

    uri = await ctx.create_resource(
        f"output://pdf/{ctx.request_id}.pdf",
        content=pdf_bytes,
        mime_type="application/pdf",
        ttl=1800,
    )

    return f"PDF generated: {uri}"
```

### Update Resource

```python
@mcp.tool
async def update_results(ctx: Context, uri: str, new_data: dict) -> str:
    # Update existing resource
    await ctx.update_resource(uri, content=new_data)
    return f"Updated {uri}"
```

### Delete Resource

```python
@mcp.tool
async def cleanup(ctx: Context, uri: str) -> str:
    await ctx.delete_resource(uri)
    return f"Deleted {uri}"
```

## Storage Backends

### In-Memory (Default)

```python
mcp = FastMCP("server")
# Uses in-memory storage - lost on restart
```

### Filesystem

```python
from fastmcp.server.dynamic_resources import FilesystemStorage

mcp = FastMCP(
    "server",
    dynamic_resource_storage=FilesystemStorage(
        directory="/tmp/mcp-resources"
    )
)
```

### Redis

```python
from fastmcp.server.dynamic_resources import RedisStorage

mcp = FastMCP(
    "server",
    dynamic_resource_storage=RedisStorage(
        url="redis://localhost:6379"
    )
)
```

### S3

```python
from fastmcp.server.dynamic_resources import S3Storage

mcp = FastMCP(
    "server",
    dynamic_resource_storage=S3Storage(
        bucket="my-mcp-resources",
        prefix="dynamic/",
    )
)
```

## Implementation

### Location

- `src/fastmcp/server/dynamic_resources.py` - Core implementation
- `src/fastmcp/server/context.py` - Add context methods
- `src/fastmcp/server/server.py` - Integrate with server

### Storage Interface

```python
from abc import ABC, abstractmethod
from typing import Protocol

class DynamicResourceStorage(Protocol):
    """Storage backend for dynamic resources."""

    async def write(
        self,
        uri: str,
        content: str | bytes | dict,
        *,
        metadata: dict | None = None,
        ttl: int | None = None,
    ) -> None:
        """Write a resource."""
        ...

    async def read(self, uri: str) -> tuple[bytes, dict]:
        """Read a resource. Returns (content, metadata)."""
        ...

    async def delete(self, uri: str) -> None:
        """Delete a resource."""
        ...

    async def exists(self, uri: str) -> bool:
        """Check if resource exists."""
        ...

    async def list(self, prefix: str | None = None) -> list[str]:
        """List all URIs, optionally filtered by prefix."""
        ...
```

### In-Memory Storage

```python
import asyncio
from datetime import datetime, timedelta

class InMemoryStorage:
    """In-memory storage with TTL support."""

    def __init__(self):
        self._data: dict[str, tuple[bytes, dict, datetime | None]] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def write(
        self,
        uri: str,
        content: str | bytes | dict,
        *,
        metadata: dict | None = None,
        ttl: int | None = None,
    ) -> None:
        # Serialize content
        if isinstance(content, dict):
            content_bytes = json.dumps(content).encode()
        elif isinstance(content, str):
            content_bytes = content.encode()
        else:
            content_bytes = content

        # Calculate expiry
        expiry = None
        if ttl:
            expiry = datetime.now() + timedelta(seconds=ttl)

        self._data[uri] = (content_bytes, metadata or {}, expiry)

        # Start cleanup task if not running
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def read(self, uri: str) -> tuple[bytes, dict]:
        if uri not in self._data:
            raise KeyError(f"Resource not found: {uri}")

        content, metadata, expiry = self._data[uri]

        # Check expiry
        if expiry and datetime.now() > expiry:
            del self._data[uri]
            raise KeyError(f"Resource expired: {uri}")

        return content, metadata

    async def delete(self, uri: str) -> None:
        self._data.pop(uri, None)

    async def exists(self, uri: str) -> bool:
        return uri in self._data

    async def list(self, prefix: str | None = None) -> list[str]:
        if prefix:
            return [uri for uri in self._data if uri.startswith(prefix)]
        return list(self._data.keys())

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired resources."""
        while True:
            await asyncio.sleep(60)  # Check every minute

            now = datetime.now()
            expired = [
                uri for uri, (_, _, expiry) in self._data.items()
                if expiry and now > expiry
            ]

            for uri in expired:
                del self._data[uri]

            if not self._data:
                break  # Stop cleanup if empty
```

### Filesystem Storage

```python
import aiofiles
import os
from pathlib import Path

class FilesystemStorage:
    """Filesystem-backed storage."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    async def write(
        self,
        uri: str,
        content: str | bytes | dict,
        *,
        metadata: dict | None = None,
        ttl: int | None = None,
    ) -> None:
        # Create path from URI
        path = self._uri_to_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize
        if isinstance(content, dict):
            content_bytes = json.dumps(content).encode()
        elif isinstance(content, str):
            content_bytes = content.encode()
        else:
            content_bytes = content

        # Write content
        async with aiofiles.open(path, "wb") as f:
            await f.write(content_bytes)

        # Write metadata
        meta_path = path.with_suffix(path.suffix + ".meta")
        async with aiofiles.open(meta_path, "w") as f:
            meta = metadata or {}
            if ttl:
                meta["expires_at"] = (datetime.now() + timedelta(seconds=ttl)).isoformat()
            await f.write(json.dumps(meta))

    async def read(self, uri: str) -> tuple[bytes, dict]:
        path = self._uri_to_path(uri)

        if not path.exists():
            raise KeyError(f"Resource not found: {uri}")

        # Check expiry
        meta_path = path.with_suffix(path.suffix + ".meta")
        if meta_path.exists():
            async with aiofiles.open(meta_path, "r") as f:
                metadata = json.loads(await f.read())

            if "expires_at" in metadata:
                expiry = datetime.fromisoformat(metadata["expires_at"])
                if datetime.now() > expiry:
                    # Delete expired
                    path.unlink()
                    meta_path.unlink()
                    raise KeyError(f"Resource expired: {uri}")
        else:
            metadata = {}

        # Read content
        async with aiofiles.open(path, "rb") as f:
            content = await f.read()

        return content, metadata

    async def delete(self, uri: str) -> None:
        path = self._uri_to_path(uri)
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".meta").unlink(missing_ok=True)

    async def exists(self, uri: str) -> bool:
        return self._uri_to_path(uri).exists()

    async def list(self, prefix: str | None = None) -> list[str]:
        uris = []
        for path in self.directory.rglob("*"):
            if path.is_file() and not path.name.endswith(".meta"):
                uri = self._path_to_uri(path)
                if not prefix or uri.startswith(prefix):
                    uris.append(uri)
        return uris

    def _uri_to_path(self, uri: str) -> Path:
        """Convert URI to filesystem path."""
        # Strip scheme
        path_part = uri.split("://", 1)[1] if "://" in uri else uri
        return self.directory / path_part

    def _path_to_uri(self, path: Path) -> str:
        """Convert filesystem path to URI."""
        rel = path.relative_to(self.directory)
        return f"file://{rel}"
```

### Context Methods

In `context.py`:

```python
class Context:
    async def create_resource(
        self,
        uri: str,
        content: str | bytes | dict,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        ttl: int | None = None,
    ) -> str:
        """Create a dynamic resource.

        Args:
            uri: Resource URI
            content: Resource content (string, bytes, or dict)
            name: Human-readable name
            description: Resource description
            mime_type: MIME type
            ttl: Time-to-live in seconds (default: 86400 = 1 day)

        Returns:
            The URI of the created resource
        """
        metadata = {}
        if name:
            metadata["name"] = name
        if description:
            metadata["description"] = description
        if mime_type:
            metadata["mime_type"] = mime_type

        ttl = ttl or 86400  # Default 1 day

        await self._server._dynamic_resource_storage.write(
            uri, content, metadata=metadata, ttl=ttl
        )

        return uri

    async def update_resource(
        self,
        uri: str,
        content: str | bytes | dict,
    ) -> None:
        """Update an existing dynamic resource."""
        # Read existing metadata
        _, metadata = await self._server._dynamic_resource_storage.read(uri)

        # Write with updated content, preserve metadata
        await self._server._dynamic_resource_storage.write(
            uri, content, metadata=metadata
        )

    async def delete_resource(self, uri: str) -> None:
        """Delete a dynamic resource."""
        await self._server._dynamic_resource_storage.delete(uri)
```

### Server Integration

In `server.py`:

```python
class FastMCP:
    def __init__(
        self,
        name: str,
        *,
        dynamic_resource_storage: DynamicResourceStorage | None = None,
        **kwargs
    ):
        self._dynamic_resource_storage = (
            dynamic_resource_storage or InMemoryStorage()
        )

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a resource - check dynamic storage first."""
        # Check dynamic resources
        if await self._dynamic_resource_storage.exists(uri):
            content, metadata = await self._dynamic_resource_storage.read(uri)

            # Create a dynamic resource wrapper
            return DynamicResource(
                uri=uri,
                content=content,
                name=metadata.get("name"),
                description=metadata.get("description"),
                mime_type=metadata.get("mime_type"),
            )

        # Fall back to provider resources
        return await super().get_resource(uri)

    async def list_resources(self) -> list[Resource]:
        """List resources - include dynamic ones."""
        # Get provider resources
        provider_resources = await super().list_resources()

        # Get dynamic resources
        dynamic_uris = await self._dynamic_resource_storage.list()
        dynamic_resources = []

        for uri in dynamic_uris:
            _, metadata = await self._dynamic_resource_storage.read(uri)
            dynamic_resources.append(
                DynamicResource(
                    uri=uri,
                    name=metadata.get("name"),
                    description=metadata.get("description"),
                )
            )

        return [*provider_resources, *dynamic_resources]
```

## Edge Cases

1. **URI collisions** - Dynamic resource URI conflicts with provider resource. Dynamic takes precedence.

2. **TTL expiry** - Resource expires while being read. Return error, client should handle.

3. **Large content** - 100MB+ files in memory. Use filesystem or S3 storage.

4. **Session cleanup** - When session ends, should we delete its resources? Optional, controlled by TTL.

5. **Concurrent writes** - Two tools write to same URI. Last write wins (no locking).

6. **URI schemes** - Any scheme is allowed. Convention: `results://`, `output://`, `temp://`.

7. **Storage backend failure** - If Redis is down, operations fail with clear error.

## Testing

Add `tests/server/test_dynamic_resources.py`:

```python
async def test_create_resource():
    mcp = FastMCP("test")

    @mcp.tool
    async def create(ctx: Context) -> str:
        uri = await ctx.create_resource(
            "results://test",
            content={"data": [1, 2, 3]},
            ttl=60
        )
        return uri

    async with Client(mcp) as client:
        uri = await client.call_tool("create", {})
        assert uri == "results://test"

        # Read it back
        resource = await client.read_resource(uri)
        assert resource.contents[0].text == '{"data": [1, 2, 3]}'

async def test_resource_ttl():
    storage = InMemoryStorage()

    await storage.write("test://resource", "data", ttl=1)

    # Should exist immediately
    assert await storage.exists("test://resource")

    # Wait for expiry
    await asyncio.sleep(1.5)

    # Should be gone
    with pytest.raises(KeyError):
        await storage.read("test://resource")

async def test_filesystem_storage(tmp_path):
    storage = FilesystemStorage(tmp_path)

    await storage.write(
        "output://file.json",
        {"key": "value"},
        metadata={"name": "Test File"}
    )

    content, metadata = await storage.read("output://file.json")
    assert json.loads(content) == {"key": "value"}
    assert metadata["name"] == "Test File"

    # Check file exists
    assert (tmp_path / "output" / "file.json").exists()
```

## Documentation

Add to `docs/servers/dynamic-resources.mdx`:

- Why dynamic resources matter
- Creating resources from tools
- Storage backends (memory, filesystem, Redis, S3)
- TTL and cleanup
- Best practices (when to use vs returning data)
- Integration with ResourceToolsProvider

Add example in `docs/examples/`:

```python
# examples/dynamic_resources/search_server.py
"""Search server that returns large results as resources."""

from fastmcp import FastMCP, Context

mcp = FastMCP("Search Server")

@mcp.tool
async def search_logs(query: str, ctx: Context) -> str:
    """Search through millions of log lines."""
    results = search_engine.search(query)  # Returns 10MB

    # Instead of returning all 10MB...
    uri = await ctx.create_resource(
        f"results://search/{ctx.request_id}",
        content=results,
        name=f"Search Results: {query}",
        description=f"Found {len(results)} matches for '{query}'",
        mime_type="application/json",
        ttl=3600,
    )

    return f"Search complete. Found {len(results)} results. Access at {uri}"
```

## Future Enhancements

1. **Resource pagination** - Auto-paginate large dynamic resources
2. **Compression** - Compress content before storing
3. **Access control** - Per-resource auth checks
4. **Versioning** - Keep multiple versions of same URI
5. **Search** - Full-text search over dynamic resources
