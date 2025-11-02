"""A middleware for response caching."""

from collections.abc import Sequence
from logging import Logger
from typing import Any, TypedDict

import mcp.types
import pydantic_core
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols.key_value import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.limit_size import LimitSizeWrapper
from key_value.aio.wrappers.statistics import StatisticsWrapper
from key_value.aio.wrappers.statistics.wrapper import (
    KVStoreCollectionStatistics,
)
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, Self, override

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger

logger: Logger = get_logger(name=__name__)

# Constants
ONE_HOUR_IN_SECONDS = 3600
FIVE_MINUTES_IN_SECONDS = 300

ONE_MB_IN_BYTES = 1024 * 1024

GLOBAL_KEY = "__global__"


class CachableReadResourceContents(BaseModel):
    """A wrapper for ReadResourceContents that can be cached."""

    content: str | bytes
    mime_type: str | None = None

    def get_size(self) -> int:
        return len(self.model_dump_json())

    @classmethod
    def get_sizes(cls, values: Sequence[Self]) -> int:
        return sum(item.get_size() for item in values)

    @classmethod
    def wrap(cls, values: Sequence[ReadResourceContents]) -> list[Self]:
        return [cls(content=item.content, mime_type=item.mime_type) for item in values]

    @classmethod
    def unwrap(cls, values: Sequence[Self]) -> list[ReadResourceContents]:
        return [
            ReadResourceContents(content=item.content, mime_type=item.mime_type)
            for item in values
        ]


class CachableToolResult(BaseModel):
    content: list[mcp.types.ContentBlock]
    structured_content: dict[str, Any] | None
    meta: dict[str, Any] | None

    @classmethod
    def wrap(cls, value: ToolResult) -> Self:
        return cls(
            content=value.content,
            structured_content=value.structured_content,
            meta=value.meta,
        )

    def unwrap(self) -> ToolResult:
        return ToolResult(
            content=self.content,
            structured_content=self.structured_content,
            meta=self.meta,
        )


class SharedMethodSettings(TypedDict):
    """Shared config for a cache method."""

    ttl: NotRequired[int]
    enabled: NotRequired[bool]


class ListToolsSettings(SharedMethodSettings):
    """Configuration options for Tool-related caching."""


class ListResourcesSettings(SharedMethodSettings):
    """Configuration options for Resource-related caching."""


class ListPromptsSettings(SharedMethodSettings):
    """Configuration options for Prompt-related caching."""


class CallToolSettings(SharedMethodSettings):
    """Configuration options for Tool-related caching."""

    included_tools: NotRequired[list[str]]
    excluded_tools: NotRequired[list[str]]


class ReadResourceSettings(SharedMethodSettings):
    """Configuration options for Resource-related caching."""


class GetPromptSettings(SharedMethodSettings):
    """Configuration options for Prompt-related caching."""


class ResponseCachingStatistics(BaseModel):
    list_tools: KVStoreCollectionStatistics | None = Field(default=None)
    list_resources: KVStoreCollectionStatistics | None = Field(default=None)
    list_prompts: KVStoreCollectionStatistics | None = Field(default=None)
    read_resource: KVStoreCollectionStatistics | None = Field(default=None)
    get_prompt: KVStoreCollectionStatistics | None = Field(default=None)
    call_tool: KVStoreCollectionStatistics | None = Field(default=None)


class ResponseCachingMiddleware(Middleware):
    """The response caching middleware offers a simple way to cache responses to mcp methods. The Middleware
    supports cache invalidation via notifications from the server. The Middleware implements TTL-based caching
    but cache implementations may offer additional features like LRU eviction, size limits, and more.

    When items are retrieved from the cache they will no longer be the original objects, but rather no-op objects
    this means that response caching may not be compatible with other middleware that expects original subclasses.

    Notes:
    - Caches `tools/call`, `resources/read`, `prompts/get`, `tools/list`, `resources/list`, and `prompts/list` requests.
    - Cache keys are derived from method name and arguments.
    """

    def __init__(
        self,
        cache_storage: AsyncKeyValue | None = None,
        list_tools_settings: ListToolsSettings | None = None,
        list_resources_settings: ListResourcesSettings | None = None,
        list_prompts_settings: ListPromptsSettings | None = None,
        read_resource_settings: ReadResourceSettings | None = None,
        get_prompt_settings: GetPromptSettings | None = None,
        call_tool_settings: CallToolSettings | None = None,
        max_item_size: int = ONE_MB_IN_BYTES,
    ):
        """Initialize the response caching middleware.

        Args:
            cache_storage: The cache backend to use. If None, an in-memory cache is used.
            list_tools_settings: The settings for the list tools method. If None, the default settings are used (5 minute TTL).
            list_resources_settings: The settings for the list resources method. If None, the default settings are used (5 minute TTL).
            list_prompts_settings: The settings for the list prompts method. If None, the default settings are used (5 minute TTL).
            read_resource_settings: The settings for the read resource method. If None, the default settings are used (1 hour TTL).
            get_prompt_settings: The settings for the get prompt method. If None, the default settings are used (1 hour TTL).
            call_tool_settings: The settings for the call tool method. If None, the default settings are used (1 hour TTL).
            max_item_size: The maximum size of items eligible for caching. Defaults to 1MB.
        """

        self._backend: AsyncKeyValue = cache_storage or MemoryStore()

        # When the size limit is exceeded, the put will silently fail
        self._size_limiter: LimitSizeWrapper = LimitSizeWrapper(
            key_value=self._backend, max_size=max_item_size, raise_on_too_large=False
        )
        self._stats: StatisticsWrapper = StatisticsWrapper(key_value=self._size_limiter)

        self._list_tools_settings: ListToolsSettings = (
            list_tools_settings or ListToolsSettings()
        )
        self._list_resources_settings: ListResourcesSettings = (
            list_resources_settings or ListResourcesSettings()
        )
        self._list_prompts_settings: ListPromptsSettings = (
            list_prompts_settings or ListPromptsSettings()
        )

        self._read_resource_settings: ReadResourceSettings = (
            read_resource_settings or ReadResourceSettings()
        )
        self._get_prompt_settings: GetPromptSettings = (
            get_prompt_settings or GetPromptSettings()
        )
        self._call_tool_settings: CallToolSettings = (
            call_tool_settings or CallToolSettings()
        )

        self._list_tools_cache: PydanticAdapter[list[Tool]] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=list[Tool],
            default_collection="tools/list",
        )

        self._list_resources_cache: PydanticAdapter[list[Resource]] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=list[Resource],
            default_collection="resources/list",
        )

        self._list_prompts_cache: PydanticAdapter[list[Prompt]] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=list[Prompt],
            default_collection="prompts/list",
        )

        self._read_resource_cache: PydanticAdapter[
            list[CachableReadResourceContents]
        ] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=list[CachableReadResourceContents],
            default_collection="resources/read",
        )

        self._get_prompt_cache: PydanticAdapter[mcp.types.GetPromptResult] = (
            PydanticAdapter(
                key_value=self._stats,
                pydantic_model=mcp.types.GetPromptResult,
                default_collection="prompts/get",
            )
        )

        self._call_tool_cache: PydanticAdapter[CachableToolResult] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=CachableToolResult,
            default_collection="tools/call",
        )

    @override
    async def on_list_tools(
        self,
        context: MiddlewareContext[mcp.types.ListToolsRequest],
        call_next: CallNext[mcp.types.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """List tools from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._list_tools_settings.get("enabled") is False:
            return await call_next(context)

        if cached_value := await self._list_tools_cache.get(key=GLOBAL_KEY):
            return cached_value

        tools: Sequence[Tool] = await call_next(context=context)

        # Turn any subclass of Tool into a Tool
        cachable_tools: list[Tool] = [
            Tool(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                parameters=tool.parameters,
                output_schema=tool.output_schema,
                annotations=tool.annotations,
                meta=tool.meta,
                tags=tool.tags,
                enabled=tool.enabled,
            )
            for tool in tools
        ]

        await self._list_tools_cache.put(
            key=GLOBAL_KEY,
            value=cachable_tools,
            ttl=self._list_tools_settings.get("ttl", FIVE_MINUTES_IN_SECONDS),
        )

        return cachable_tools

    @override
    async def on_list_resources(
        self,
        context: MiddlewareContext[mcp.types.ListResourcesRequest],
        call_next: CallNext[mcp.types.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """List resources from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._list_resources_settings.get("enabled") is False:
            return await call_next(context)

        if cached_value := await self._list_resources_cache.get(key=GLOBAL_KEY):
            return cached_value

        resources: Sequence[Resource] = await call_next(context=context)

        # Turn any subclass of Resource into a Resource
        cachable_resources: list[Resource] = [
            Resource(
                name=resource.name,
                title=resource.title,
                description=resource.description,
                tags=resource.tags,
                meta=resource.meta,
                mime_type=resource.mime_type,
                annotations=resource.annotations,
                enabled=resource.enabled,
                uri=resource.uri,
            )
            for resource in resources
        ]

        await self._list_resources_cache.put(
            key=GLOBAL_KEY,
            value=cachable_resources,
            ttl=self._list_resources_settings.get("ttl", FIVE_MINUTES_IN_SECONDS),
        )

        return cachable_resources

    @override
    async def on_list_prompts(
        self,
        context: MiddlewareContext[mcp.types.ListPromptsRequest],
        call_next: CallNext[mcp.types.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        """List prompts from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._list_prompts_settings.get("enabled") is False:
            return await call_next(context)

        if cached_value := await self._list_prompts_cache.get(key=GLOBAL_KEY):
            return cached_value

        prompts: Sequence[Prompt] = await call_next(context=context)

        # Turn any subclass of Prompt into a Prompt
        cachable_prompts: list[Prompt] = [
            Prompt(
                name=prompt.name,
                title=prompt.title,
                description=prompt.description,
                tags=prompt.tags,
                meta=prompt.meta,
                enabled=prompt.enabled,
                arguments=prompt.arguments,
            )
            for prompt in prompts
        ]

        await self._list_prompts_cache.put(
            key=GLOBAL_KEY,
            value=cachable_prompts,
            ttl=self._list_prompts_settings.get("ttl", FIVE_MINUTES_IN_SECONDS),
        )

        return cachable_prompts

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Call a tool from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        tool_name = context.message.name

        if self._call_tool_settings.get(
            "enabled"
        ) is False or not self._matches_tool_cache_settings(tool_name=tool_name):
            return await call_next(context=context)

        cache_key: str = f"{tool_name}:{_get_arguments_str(context.message.arguments)}"

        if cached_value := await self._call_tool_cache.get(key=cache_key):
            return cached_value.unwrap()

        tool_result: ToolResult = await call_next(context=context)
        cachable_tool_result: CachableToolResult = CachableToolResult.wrap(
            value=tool_result
        )

        await self._call_tool_cache.put(
            key=cache_key,
            value=cachable_tool_result,
            ttl=self._call_tool_settings.get("ttl", ONE_HOUR_IN_SECONDS),
        )

        return cachable_tool_result.unwrap()

    @override
    async def on_read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        call_next: CallNext[
            mcp.types.ReadResourceRequestParams, Sequence[ReadResourceContents]
        ],
    ) -> Sequence[ReadResourceContents]:
        """Read a resource from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._read_resource_settings.get("enabled") is False:
            return await call_next(context=context)

        cache_key: str = str(context.message.uri)
        cached_value: list[CachableReadResourceContents] | None

        if cached_value := await self._read_resource_cache.get(key=cache_key):
            return CachableReadResourceContents.unwrap(values=cached_value)

        value: Sequence[ReadResourceContents] = await call_next(context=context)
        cached_value = CachableReadResourceContents.wrap(values=value)

        await self._read_resource_cache.put(
            key=cache_key,
            value=cached_value,
            ttl=self._read_resource_settings.get("ttl", ONE_HOUR_IN_SECONDS),
        )

        return CachableReadResourceContents.unwrap(values=cached_value)

    @override
    async def on_get_prompt(
        self,
        context: MiddlewareContext[mcp.types.GetPromptRequestParams],
        call_next: CallNext[
            mcp.types.GetPromptRequestParams, mcp.types.GetPromptResult
        ],
    ) -> mcp.types.GetPromptResult:
        """Get a prompt from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._get_prompt_settings.get("enabled") is False:
            return await call_next(context=context)

        cache_key: str = f"{context.message.name}:{_get_arguments_str(arguments=context.message.arguments)}"

        if cached_value := await self._get_prompt_cache.get(key=cache_key):
            return cached_value

        value: mcp.types.GetPromptResult = await call_next(context=context)

        await self._get_prompt_cache.put(
            key=cache_key,
            value=value,
            ttl=self._get_prompt_settings.get("ttl", ONE_HOUR_IN_SECONDS),
        )

        return value

    def _matches_tool_cache_settings(self, tool_name: str) -> bool:
        """Check if the tool matches the cache settings for tool calls."""

        if included_tools := self._call_tool_settings.get("included_tools"):
            if tool_name not in included_tools:
                return False

        if excluded_tools := self._call_tool_settings.get("excluded_tools"):
            if tool_name in excluded_tools:
                return False

        return True

    def statistics(self) -> ResponseCachingStatistics:
        """Get the statistics for the cache."""
        return ResponseCachingStatistics(
            list_tools=self._stats.statistics.collections.get("tools/list"),
            list_resources=self._stats.statistics.collections.get("resources/list"),
            list_prompts=self._stats.statistics.collections.get("prompts/list"),
            read_resource=self._stats.statistics.collections.get("resources/read"),
            get_prompt=self._stats.statistics.collections.get("prompts/get"),
            call_tool=self._stats.statistics.collections.get("tools/call"),
        )


def _get_arguments_str(arguments: dict[str, Any] | None) -> str:
    """Get a string representation of the arguments."""

    if arguments is None:
        return "null"

    try:
        return pydantic_core.to_json(value=arguments, fallback=str).decode()

    except TypeError:
        return repr(arguments)
