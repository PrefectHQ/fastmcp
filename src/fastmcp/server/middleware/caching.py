import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Generic, Protocol, TypedDict, TypeVar, cast

import mcp.types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import NotRequired, Self, overload, runtime_checkable

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger

try:
    from cachetools import TLRUCache as MemoryCacheClient
    from diskcache import Cache as DiskCacheClient
except ImportError:
    raise ImportError(
        "fastmcp[caching] is required to use the caching middleware. Please install it with `pip install fastmcp[caching] or `uv add fastmcp[caching]`"
    )

logger = get_logger(__name__)

# Constants
ONE_HOUR_IN_SECONDS = 3600
FIVE_MINUTES_IN_SECONDS = 300

ONE_GB_IN_BYTES = 1024 * 1024 * 1024
ONE_MB_IN_BYTES = 1024 * 1024

GLOBAL_KEY = "__global__"


CachableTypes = (
    ToolResult
    | Sequence[Tool]
    | Sequence[Resource]
    | Sequence[Prompt]
    | Sequence[ReadResourceContents]
    | mcp.types.GetPromptResult
)

CachableTypeVar = TypeVar("CachableTypeVar", bound=CachableTypes)


def make_collection_key(collection: str, key: str) -> str:
    return f"{collection}:{key}"


class CachedPrompt(Prompt):
    """A cached prompt."""

    def render(
        self, arguments: dict[str, Any] | None = None
    ) -> list[mcp.types.PromptMessage]:
        raise NotImplementedError(
            "Render called on CachedPrompt, this should never happen"
        )


class CachedResource(Resource):
    """A cached resource."""

    def read(self) -> str | bytes:
        raise NotImplementedError(
            "Read called on CachedResource, this should never happen"
        )


class CachedTool(Tool):
    """A cached tool."""

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError("Run called on CachedTool, this should never happen")


class CacheEntry(BaseModel, Generic[CachableTypeVar]):
    """A cache entry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, arbitrary_types_allowed=True
    )

    key: str
    collection: str
    value: CachableTypeVar

    created_at: datetime

    ttl: int

    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(tz=timezone.utc) > self.expires_at

    @classmethod
    def from_value(
        cls, collection: str, key: str, value: CachableTypeVar, ttl: int
    ) -> Self:
        return cls(
            collection=collection,
            key=key,
            value=value,
            created_at=datetime.now(tz=timezone.utc),
            ttl=ttl,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=ttl),
        )


@runtime_checkable
class CacheProtocol(Protocol):
    """A protocol for a cache client."""

    async def get_entry(
        self,
        collection: str,
        key: str,
    ) -> CacheEntry[CachableTypes] | None:
        """Get a cache entry from the cache."""

    async def get_value(
        self,
        collection: str,
        key: str,
    ) -> CachableTypes | None:
        """Get a value from the cache."""

        if not (cache_entry := await self.get_entry(collection=collection, key=key)):
            return None

        return cache_entry.value

    async def set_entry(
        self,
        cache_entry: CacheEntry[CachableTypes],
    ) -> None:
        """Set a value in the cache."""

    async def set_value(
        self,
        collection: str,
        key: str,
        value: CachableTypes,
        ttl: int,
    ) -> None:
        """Set a value in the cache."""

        await self.set_entry(
            cache_entry=CacheEntry.from_value(
                collection=collection, key=key, value=value, ttl=ttl
            )
        )

    async def delete(
        self,
        collection: str,
        key: str,
    ) -> None:
        """Delete a value from the cache."""

    def make_collection_key(self, collection: str, key: str) -> str:
        return f"{collection}:{key}"


class DiskCache(CacheProtocol):
    """A caching client that uses the DiskCache library to cache to disk."""

    @overload
    def __init__(self, *, disk_cache: DiskCacheClient):
        """Initialize the disk cache with a diskcache client."""

    @overload
    def __init__(self, path: str, *, size_limit: int = ONE_GB_IN_BYTES):
        """Initialize a 1GB disk cache at the provided path."""

    def __init__(
        self,
        path: str | None = None,
        *,
        disk_cache: DiskCacheClient | None = None,
        size_limit: int = ONE_GB_IN_BYTES,
    ):
        self._cache = disk_cache or DiskCacheClient(
            directory=path, size_limit=size_limit
        )

    async def get_entry(
        self, collection: str, key: str
    ) -> CacheEntry[CachableTypes] | None:
        collection_key = self.make_collection_key(collection=collection, key=key)

        cache_entry = self._cache.get(key=collection_key)

        if cache_entry is None:
            return None

        return cache_entry  # pyright: ignore[reportReturnType]

    async def set_entry(
        self,
        cache_entry: CacheEntry[CachableTypes],
    ) -> None:
        collection_key = self.make_collection_key(
            collection=cache_entry.collection, key=cache_entry.key
        )

        self._cache.set(key=collection_key, value=cache_entry, expire=cache_entry.ttl)

    async def delete(self, collection: str, key: str) -> None:
        collection_key = self.make_collection_key(collection=collection, key=key)

        self._cache.delete(key=collection_key)


DEFAULT_MEMORY_CACHE_MAX_ENTRIES = 1000


def _memory_cache_ttu(_key: Any, value: CacheEntry[CachableTypes], now: float) -> float:
    return now + value.ttl


def _memory_cache_getsizeof(value: CacheEntry[CachableTypes]) -> int:
    return 1


class InMemoryCache(CacheProtocol):
    """A simple in-memory cache."""

    def __init__(self, max_entries: int = DEFAULT_MEMORY_CACHE_MAX_ENTRIES):
        """Initialize the in-memory cache.

        Args:
            max_entries: The maximum number of entries to store in the cache. Defaults to 1000.
        """
        self.max_entries = max_entries
        self._cache = MemoryCacheClient(
            maxsize=max_entries,
            ttu=_memory_cache_ttu,
            getsizeof=_memory_cache_getsizeof,
        )

    async def get_entry(
        self, collection: str, key: str
    ) -> CacheEntry[CachableTypes] | None:
        collection_key = self.make_collection_key(collection=collection, key=key)

        return self._cache.get(collection_key)

    async def set_entry(
        self,
        cache_entry: CacheEntry[CachableTypes],
    ) -> None:
        collection_key = self.make_collection_key(
            collection=cache_entry.collection, key=cache_entry.key
        )

        self._cache[collection_key] = cache_entry

    async def delete(self, collection: str, key: str) -> None:
        collection_key = self.make_collection_key(collection=collection, key=key)

        self._cache.pop(collection_key, None)

    async def setup(self) -> None:
        return None

    async def clear(self) -> None:
        self._cache.clear()


class CacheMethodStats(BaseModel):
    """Stats for a cache method."""

    hits: int = Field(default=0)
    misses: int = Field(default=0)
    too_big: int = Field(default=0)


class CacheStats(BaseModel):
    """Stats for the cache."""

    collections: dict[str, CacheMethodStats] = Field(
        default_factory=lambda: defaultdict[str, CacheMethodStats](CacheMethodStats)
    )

    def get_misses(self, collection: str) -> int:
        return self.collections[collection].misses

    def get_hits(self, collection: str) -> int:
        return self.collections[collection].hits

    def get_too_big(self, collection: str) -> int:
        return self.collections[collection].too_big

    def mark_miss(self, collection: str) -> None:
        self.collections[collection].misses += 1

    def mark_hit(self, collection: str) -> None:
        self.collections[collection].hits += 1

    def mark_too_big(self, collection: str) -> None:
        self.collections[collection].too_big += 1


class SharedMethodSettings(TypedDict):
    """Shared config for a cache method."""

    ttl: NotRequired[int]


class ListToolsSettings(SharedMethodSettings):
    pass


class ListResourcesSettings(SharedMethodSettings):
    pass


class ListPromptsSettings(SharedMethodSettings):
    pass


class CallToolSettings(SharedMethodSettings):
    """Extra configuration options for Tool-related caching."""

    included_tools: NotRequired[list[str]]
    excluded_tools: NotRequired[list[str]]


class ReadResourceSettings(SharedMethodSettings):
    pass


class GetPromptSettings(SharedMethodSettings):
    pass


class MethodSettings(TypedDict):
    """Config for the response caching middleware methods."""

    list_tools: NotRequired[ListToolsSettings]
    call_tool: NotRequired[CallToolSettings]

    list_resources: NotRequired[ListResourcesSettings]
    read_resource: NotRequired[ReadResourceSettings]

    list_prompts: NotRequired[ListPromptsSettings]
    get_prompt: NotRequired[GetPromptSettings]


MethodSettingsType = TypeVar("MethodSettingsType", bound=SharedMethodSettings)

NOTIFICATION_TO_COLLECTION = {
    mcp.types.ToolListChangedNotification: "tools/list",
    mcp.types.ResourceListChangedNotification: "resources/list",
    mcp.types.PromptListChangedNotification: "prompts/list",
}

MCP_METHOD_TO_METHOD_SETTINGS_KEY = {
    "tools/list": "list_tools",
    "tools/call": "call_tool",
    "resources/list": "list_resources",
    "resources/read": "read_resource",
    "prompts/list": "list_prompts",
    "prompts/get": "get_prompt",
}

DEFAULT_METHOD_SETTINGS: MethodSettings = MethodSettings(
    list_tools=SharedMethodSettings(
        ttl=FIVE_MINUTES_IN_SECONDS,
    ),
    call_tool=CallToolSettings(
        ttl=ONE_HOUR_IN_SECONDS,
    ),
    list_resources=SharedMethodSettings(
        ttl=FIVE_MINUTES_IN_SECONDS,
    ),
    list_prompts=SharedMethodSettings(
        ttl=FIVE_MINUTES_IN_SECONDS,
    ),
    read_resource=SharedMethodSettings(
        ttl=ONE_HOUR_IN_SECONDS,
    ),
    get_prompt=SharedMethodSettings(
        ttl=ONE_HOUR_IN_SECONDS,
    ),
)


class ResponseCachingMiddleware(Middleware):
    """Caches tool call responses based on method name and params.

    Notes:
    - Only caches `tools/call` requests.
    - Cache key derived from tool name and arguments.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol | None = None,
        method_settings: MethodSettings | None = None,
        default_ttl: int = ONE_HOUR_IN_SECONDS,
        max_item_size: int | None = None,
    ):
        """Initialize the response caching middleware.

        Args:
            cache_backend: The cache backend to use. If None, an in-memory cache is used.
            method_settings: The settings for the middleware. If None, the default settings are used.
            default_ttl: The default TTL for cached responses. Defaults to one hour.
            max_item_size: The maximum size of an item to cache. Defaults to no size limit.
        """
        self._default_ttl: int = default_ttl
        self._backend: CacheProtocol = cache_backend or InMemoryCache()
        self._max_item_size: int | None = max_item_size

        self._stats = CacheStats()

        self.method_settings: MethodSettings = (
            method_settings or DEFAULT_METHOD_SETTINGS
        )

    async def on_list_tools(
        self,
        context: MiddlewareContext[mcp.types.ListToolsRequest],
        call_next: CallNext[mcp.types.ListToolsRequest, list[Tool]],
    ) -> list[Tool]:
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        if cached_value := await self._get_cache(
            context=context, call_next=call_next, key=None
        ):
            return cached_value

        result: list[Tool] = await call_next(context)

        # Convert tool subclasses to Tool objects
        result = [
            CachedTool(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                parameters=tool.parameters,
                output_schema=tool.output_schema,
                annotations=tool.annotations,
                meta=tool.meta,
                tags=tool.tags,
            )
            for tool in result
        ]

        return await self._store_in_cache_and_return(
            context=context,
            key=None,
            value=result,
        )

    async def on_list_resources(
        self,
        context: MiddlewareContext[mcp.types.ListResourcesRequest],
        call_next: CallNext[mcp.types.ListResourcesRequest, list[Resource]],
    ) -> list[Resource]:
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        if cached_value := await self._get_cache(
            context=context,
            call_next=call_next,
            key=None,
        ):
            return cached_value

        result: list[Resource] = await call_next(context)

        result = [
            CachedResource(
                **resource.model_dump(exclude={"fn"}),
            )
            for resource in result
        ]

        return await self._store_in_cache_and_return(
            context=context,
            key=None,
            value=result,
        )

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mcp.types.ListPromptsRequest],
        call_next: CallNext[mcp.types.ListPromptsRequest, list[Prompt]],
    ) -> list[Prompt]:
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        if cached_value := await self._get_cache(
            context=context,
            call_next=call_next,
            key=None,
        ):
            return cached_value

        result: list[Prompt] = await call_next(context)

        result = [
            CachedPrompt(
                name=prompt.name,
                title=prompt.title,
                description=prompt.description,
                arguments=prompt.arguments,
                meta=prompt.meta,
            )
            for prompt in result
        ]

        return await self._store_in_cache_and_return(
            context=context,
            key=None,
            value=result,
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> Any:
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        if not self._matches_tool_cache_settings(context=context):
            return await call_next(context=context)

        return await self._cached_call_next(
            context=context,
            call_next=call_next,
            key=_make_call_tool_cache_key(msg=context.message),
        )

    async def on_read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        call_next: CallNext[
            mcp.types.ReadResourceRequestParams, list[ReadResourceContents]
        ],
    ) -> list[ReadResourceContents]:
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        return await self._cached_call_next(
            context=context,
            call_next=call_next,
            key=_make_read_resource_cache_key(msg=context.message),
        )

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mcp.types.GetPromptRequestParams],
        call_next: CallNext[
            mcp.types.GetPromptRequestParams, mcp.types.GetPromptResult
        ],
    ) -> mcp.types.GetPromptResult:
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        return await self._cached_call_next(
            context=context,
            call_next=call_next,
            key=_make_get_prompt_cache_key(msg=context.message),
        )

    async def on_notification(
        self,
        context: MiddlewareContext[mcp.types.Notification],
        call_next: CallNext[mcp.types.Notification, Any],
    ) -> Any:
        if collection := NOTIFICATION_TO_COLLECTION.get(context.message):
            await self._backend.delete(collection=collection, key=GLOBAL_KEY)

        return await call_next(context)

    async def _cached_call_next(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, CachableTypeVar],
        key: str | None = None,
    ) -> CachableTypeVar:
        if key is None:
            key = GLOBAL_KEY

        if cached_value := await self._get_cache(
            context=context,
            call_next=call_next,
            key=key,
        ):
            return cached_value

        result: CachableTypeVar = await call_next(context)

        return await self._store_in_cache_and_return(
            context=context,
            key=key,
            value=result,
        )

    async def _get_cache(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, CachableTypeVar],
        key: str | None = None,
    ) -> CachableTypeVar | None:
        if key is None:
            key = GLOBAL_KEY

        if not (collection := context.method):
            logger.warning("No method found on context, skipping cache")
            return None

        if cached_value := await self._backend.get_value(
            collection=collection, key=key
        ):
            self._stats.mark_hit(collection=collection)
            return cast(CachableTypeVar, cached_value)

        self._stats.mark_miss(collection=collection)

        return None

    async def _store_in_cache_and_return(
        self,
        context: MiddlewareContext[Any],
        key: str | None,
        value: CachableTypeVar,
    ) -> CachableTypeVar:
        if key is None:
            key = GLOBAL_KEY

        if not (collection := context.method):
            logger.warning("No method found on context, skipping cache")
            return value

        if self._max_item_size is not None:
            if get_size_of_value(value=value) > self._max_item_size:
                self._stats.mark_too_big(collection=collection)
                return value

        ttl: int = self._get_cache_ttl(context=context)

        await self._backend.set_value(
            collection=collection,
            key=key,
            value=value,
            ttl=ttl,
        )

        return value

    def _matches_tool_cache_settings(
        self, context: MiddlewareContext[mcp.types.CallToolRequestParams]
    ) -> bool:
        tool_name = context.message.name

        tool_call_cache_settings: CallToolSettings | None = self._get_cache_settings(
            context=context,
            settings_type=CallToolSettings,
        )

        if not tool_call_cache_settings:
            return True

        if included_tools := tool_call_cache_settings.get("included_tools"):
            if tool_name not in included_tools:
                return False

        if excluded_tools := tool_call_cache_settings.get("excluded_tools"):
            if tool_name in excluded_tools:
                return False

        return True

    def _get_cache_settings(
        self,
        context: MiddlewareContext[Any],
        settings_type: type[MethodSettingsType] = SharedMethodSettings,
    ) -> MethodSettingsType | None:
        if not context.method:
            return None

        method_settings_key = MCP_METHOD_TO_METHOD_SETTINGS_KEY.get(
            context.method, None
        )

        if (
            method_settings_key is None
            or method_settings_key not in self.method_settings
        ):
            return None

        return cast(MethodSettingsType, self.method_settings[method_settings_key])

    def _get_cache_ttl(self, context: MiddlewareContext[Any]) -> int:
        settings: SharedMethodSettings | None = self._get_cache_settings(
            context=context
        )

        if not settings or "ttl" not in settings:
            return self._default_ttl

        return settings["ttl"]

    def _should_bypass_caching(self, context: MiddlewareContext[Any]) -> bool:
        if not self._get_cache_settings(context=context):
            return True

        return False


def _make_call_tool_cache_key(msg: mcp.types.CallToolRequestParams) -> str:
    raw = f"{msg.name}:{_get_arguments_str(msg.arguments)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_read_resource_cache_key(msg: mcp.types.ReadResourceRequestParams) -> str:
    raw = f"{msg.uri}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_get_prompt_cache_key(msg: mcp.types.GetPromptRequestParams) -> str:
    raw = f"{msg.name}:{_get_arguments_str(msg.arguments)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_arguments_str(arguments: dict[str, Any] | None) -> str:
    if arguments is None:
        return "null"

    try:
        return json.dumps(arguments, sort_keys=True, separators=(",", ":"))

    except TypeError:
        return repr(arguments)


def get_size_of_content_blocks(
    value: mcp.types.ContentBlock | Sequence[mcp.types.ContentBlock],
) -> int:
    if isinstance(value, mcp.types.ContentBlock):
        value = [value]

    return sum([len(item.model_dump_json()) for item in value])


def get_size_of_tool_result(value: ToolResult) -> int:
    content_size = get_size_of_content_blocks(value.content)
    structured_content_size = len(
        json.dumps(
            value.structured_content, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )

    return content_size + structured_content_size


def get_size_of_one_value(value: BaseModel | ToolResult | ReadResourceContents) -> int:
    if isinstance(value, ToolResult):
        return get_size_of_tool_result(value)
    if isinstance(value, ReadResourceContents):
        return len(value.content)
    return len(value.model_dump_json())


def get_size_of_value(value: CachableTypes) -> int:
    if isinstance(value, BaseModel | ToolResult | ReadResourceContents):
        return get_size_of_one_value(value)

    return sum(get_size_of_one_value(item) for item in value)
