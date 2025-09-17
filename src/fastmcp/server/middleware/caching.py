import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Protocol

from mcp.types import CallToolRequestParams, ContentBlock
from pydantic import BaseModel, ConfigDict
from typing_extensions import Self

from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult


class CacheEntry(BaseModel):
    """A cache entry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    key: str

    content: list[ContentBlock] | Any | None
    structured_content: str | None

    created_at: datetime
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(tz=timezone.utc) > self.expires_at

    def to_tool_result(self) -> ToolResult:
        return ToolResult(
            content=self.content,
            structured_content=json.loads(self.structured_content)
            if self.structured_content is not None
            else None,
        )

    @classmethod
    def from_tool_result(cls, key: str, value: ToolResult, ttl: int) -> Self:
        return cls(
            key=key,
            content=value.content,
            structured_content=json.dumps(value.structured_content)
            if value.structured_content is not None
            else None,
            created_at=datetime.now(tz=timezone.utc),
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=ttl),
        )


class CacheProtocol(Protocol):
    """A protocol for a cache client."""

    async def get(self, key: str) -> ToolResult | None: ...

    async def set(self, key: str, value: ToolResult, ttl: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def setup(self) -> None: ...

    async def clear(self) -> None: ...


class InMemoryCache(CacheProtocol):
    """A simple in-memory cache."""

    def __init__(self, max_size: int = 1000):
        self._cache: dict[str, CacheEntry] = {}
        self._max_size = max_size

    async def get(self, key: str) -> ToolResult | None:
        cached_entry = self._cache.get(key)

        if cached_entry is None:
            return None

        if cached_entry.is_expired():
            self._cache.pop(key, None)
            return None

        return ToolResult(
            content=cached_entry.content,
            structured_content=json.loads(cached_entry.structured_content)
            if cached_entry.structured_content is not None
            else None,
        )

    async def set(self, key: str, value: Any, ttl: int) -> None:
        if len(self._cache) >= self._max_size:
            self._cache.pop(next(iter(self._cache)))

        self._cache[key] = CacheEntry.from_tool_result(key=key, value=value, ttl=ttl)

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    async def setup(self) -> None:
        return None

    async def clear(self) -> None:
        self._cache.clear()


class CacheStats(BaseModel):
    """Stats for the cache."""

    hits: int
    misses: int


class ResponseCachingMiddleware(Middleware):
    """Caches tool call responses based on method name and params.

    Notes:
    - Only caches `tools/call` requests.
    - Cache key derived from tool name and arguments.
    """

    _stats: CacheStats

    def __init__(
        self,
        cache_backend: CacheProtocol,
        included_tools: list[str] | None = None,
        excluded_tools: list[str] | None = None,
        default_ttl: int = 3600,
    ):
        self._default_ttl = default_ttl
        self._backend = cache_backend
        self._stats = CacheStats(hits=0, misses=0)
        self._included_tools = included_tools
        self._excluded_tools = excluded_tools

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, Any],
    ) -> Any:
        if not self._should_cache_tool(context.message.name):
            return await call_next(context)

        key = self._make_cache_key(context.message)

        if cached_entry := await self._backend.get(key):
            self._stats.hits += 1
            return cached_entry

        # Cache miss: call downstream
        self._stats.misses += 1
        result = await call_next(context)

        await self._backend.set(key, result, self._default_ttl)

        return result

    def _should_cache_tool(self, tool_name: str) -> bool:
        if self._excluded_tools is not None and tool_name in self._excluded_tools:
            return False
        if self._included_tools is not None and tool_name not in self._included_tools:
            return False
        return True

    def _make_cache_key(self, msg: CallToolRequestParams) -> str:
        raw = f"{self._get_tool_key(msg)}:{self._get_tool_arguments_str(msg)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _get_tool_key(self, msg: CallToolRequestParams) -> str:
        return msg.name

    def _get_tool_arguments_str(self, msg: CallToolRequestParams) -> str:
        if msg.arguments is None:
            return "null"

        try:
            return json.dumps(msg.arguments, sort_keys=True, separators=(",", ":"))

        except TypeError:
            return repr(msg.arguments)
