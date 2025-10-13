"""A middleware for response caching."""

import json
from collections.abc import Sequence
from typing import Any, TypedDict, TypeVar, cast

import mcp.types
import pydantic_core
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols.key_value import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.statistics import StatisticsWrapper
from key_value.aio.wrappers.statistics.wrapper import (
    KVStoreCollectionStatistics,
)
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import PromptMessage
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, Self, override

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# Constants
ONE_HOUR_IN_SECONDS = 3600
FIVE_MINUTES_IN_SECONDS = 300

ONE_GB_IN_BYTES = 1024 * 1024 * 1024
ONE_MB_IN_BYTES = 1024 * 1024

GLOBAL_KEY = "__global__"


class CachableToolResult(BaseModel, ToolResult):
    structured_content: dict[str, Any] | None
    content: list[mcp.types.ContentBlock]

    @classmethod
    def from_tool_result(cls, tool_result: ToolResult) -> Self:
        return cls(
            structured_content=tool_result.structured_content,
            content=tool_result.content,
        )

    def get_size(self) -> int:
        return _get_size_of_tool_result(self)


class CachablePrompt(Prompt):
    @override
    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> list[PromptMessage]:
        """Render the prompt with arguments."""
        raise NotImplementedError(
            "Prompt.render() is not implemented on cached prompts"
        )

    @classmethod
    def from_list_prompts(cls, prompts: list[Prompt]) -> list[Self]:
        cachable_prompts: list[Self] = []
        for prompt in prompts:
            cachable_prompts.append(
                cls(
                    name=prompt.name,
                    title=prompt.title,
                    description=prompt.description,
                    arguments=prompt.arguments,
                    meta=prompt.meta,
                    tags=prompt.tags,
                    enabled=prompt.enabled,
                )
            )
        return cachable_prompts


class CachablePromptResult(mcp.types.GetPromptResult): ...


class CachableResource(Resource):
    @override
    async def read(self) -> str | bytes:
        """Read the resource content."""
        raise NotImplementedError(
            "Resource.read() is not implemented on cached resources"
        )

    @classmethod
    def from_list_resources(cls, resources: list[Resource]) -> list[Self]:
        cachable_resources: list[Self] = []
        for resource in resources:
            cachable_resources.append(
                cls(
                    name=resource.name,
                    description=resource.description,
                    uri=resource.uri,
                    mime_type=resource.mime_type,
                    annotations=resource.annotations,
                    meta=resource.meta,
                    tags=resource.tags,
                    enabled=resource.enabled,
                )
            )
        return cachable_resources


class CachableTool(Tool):
    @classmethod
    def from_list_tools(cls, tools: list[Tool]) -> list[Self]:
        cachable_tools: list[Self] = []
        for tool in tools:
            cachable_tools.append(
                cls(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    output_schema=tool.output_schema,
                    annotations=tool.annotations,
                    serializer=tool.serializer,
                    meta=tool.meta,
                    tags=tool.tags,
                    enabled=tool.enabled,
                )
            )
        return cachable_tools


class CachableReadResourceContents(BaseModel, ReadResourceContents): ...


class CachableToolList(BaseModel):
    cachable_tools: list[CachableTool]


class CachableResourceList(BaseModel):
    cachable_resources: list[CachableResource]


class CachablePromptList(BaseModel):
    cachable_prompts: list[CachablePrompt]


class CachableReadResourceContentsList(BaseModel):
    cachable_read_resource_contents: list[CachableReadResourceContents]


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


class MethodSettings(TypedDict):
    """Configuration options for mcp "methods" in the response caching middleware."""

    list_tools: NotRequired[ListToolsSettings]
    call_tool: NotRequired[CallToolSettings]

    list_resources: NotRequired[ListResourcesSettings]
    read_resource: NotRequired[ReadResourceSettings]

    list_prompts: NotRequired[ListPromptsSettings]
    get_prompt: NotRequired[GetPromptSettings]


MethodSettingsType = TypeVar("MethodSettingsType", bound=SharedMethodSettings)


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
        cache_store: AsyncKeyValue | None = None,
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
        self._backend: AsyncKeyValue = cache_store or MemoryStore()
        self._stats: StatisticsWrapper = StatisticsWrapper(store=self._backend)

        self._max_item_size: int | None = max_item_size

        self.method_settings: MethodSettings = (
            method_settings or DEFAULT_METHOD_SETTINGS
        )

        self._list_tools_cache: PydanticAdapter[CachableToolList] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=CachableToolList,
            default_collection="tools/list",
        )

        self._list_resources_cache: PydanticAdapter[CachableResourceList] = (
            PydanticAdapter(
                key_value=self._stats,
                pydantic_model=CachableResourceList,
                default_collection="resources/list",
            )
        )

        self._list_prompts_cache: PydanticAdapter[CachablePromptList] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=CachablePromptList,
            default_collection="prompts/list",
        )

        self._read_resource_cache: PydanticAdapter[CachableReadResourceContentsList] = (
            PydanticAdapter(
                key_value=self._stats,
                pydantic_model=CachableReadResourceContentsList,
                default_collection="resources/read",
            )
        )

        self._get_prompt_cache: PydanticAdapter[CachablePromptResult] = PydanticAdapter(
            key_value=self._stats,
            pydantic_model=CachablePromptResult,
            default_collection="prompts/get",
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
        call_next: CallNext[mcp.types.ListToolsRequest, list[Tool]],
    ) -> list[Tool]:
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        if cached_value := await self._list_tools_cache.get(key=GLOBAL_KEY):
            return cached_value.cachable_tools

        value: list[Tool] = await call_next(context=context)

        cachable_tools: list[CachableTool] = CachableTool.from_list_tools(tools=value)

        await self._list_tools_cache.put(
            key=GLOBAL_KEY,
            value=CachableToolList(cachable_tools=cachable_tools),
        )

        return cachable_tools

    @override
    async def on_list_resources(
        self,
        context: MiddlewareContext[mcp.types.ListResourcesRequest],
        call_next: CallNext[mcp.types.ListResourcesRequest, list[Resource]],
    ) -> list[Resource]:
        """List resources from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        if cached_value := await self._list_resources_cache.get(key=GLOBAL_KEY):
            return cached_value.cachable_resources

        value: list[Resource] = await call_next(context=context)

        cachable_resources: list[CachableResource] = (
            CachableResource.from_list_resources(resources=value)
        )

        await self._list_resources_cache.put(
            key=GLOBAL_KEY,
            value=CachableResourceList(cachable_resources=cachable_resources),
        )

        return cachable_resources

    @override
    async def on_list_prompts(
        self,
        context: MiddlewareContext[mcp.types.ListPromptsRequest],
        call_next: CallNext[mcp.types.ListPromptsRequest, list[Prompt]],
    ) -> list[Prompt]:
        """List prompts from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        if cached_value := await self._list_prompts_cache.get(key=GLOBAL_KEY):
            return cached_value.cachable_prompts

        value: list[Prompt] = await call_next(context=context)

        cachable_prompts: list[CachablePrompt] = CachablePrompt.from_list_prompts(
            prompts=value
        )

        await self._list_prompts_cache.put(
            key=GLOBAL_KEY,
            value=CachablePromptList(cachable_prompts=cachable_prompts),
        )

        return cachable_prompts

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> Any:
        """Call a tool from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        if not self._matches_tool_cache_settings(context=context):
            return await call_next(context=context)

        if cached_value := await self._call_tool_cache.get(
            key=_make_call_tool_cache_key(msg=context.message)
        ):
            return cached_value

        tool_result: ToolResult = await call_next(context=context)

        cachable_value: CachableToolResult = CachableToolResult.from_tool_result(
            tool_result=tool_result
        )

        if self._max_item_size and cachable_value.get_size() > self._max_item_size:
            return tool_result

        await self._call_tool_cache.put(
            key=_make_call_tool_cache_key(msg=context.message),
            value=cachable_value,
        )

        return cachable_value

    @override
    async def on_read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        call_next: CallNext[
            mcp.types.ReadResourceRequestParams, list[ReadResourceContents]
        ],
    ) -> list[ReadResourceContents]:
        """Read a resource from the cache, if caching is enabled, and the result is in the cache. Otherwise,
        otherwise call the next middleware and store the result in the cache if caching is enabled."""
        if self._should_bypass_caching(context=context):
            return await call_next(context=context)

        if cached_value := await self._read_resource_cache.get(
            key=_make_read_resource_cache_key(msg=context.message)
        ):
            return cached_value.cachable_read_resource_contents

        value: list[ReadResourceContents] = await call_next(context=context)

        cachable_read_resource_contents: list[CachableReadResourceContents] = [
            CachableReadResourceContents(content=item.content, mime_type=item.mime_type)
            for item in value
        ]

        await self._read_resource_cache.put(
            key=_make_read_resource_cache_key(msg=context.message),
            value=CachableReadResourceContentsList(
                cachable_read_resource_contents=cachable_read_resource_contents
            ),
        )

        return cachable_read_resource_contents

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
        if self._should_bypass_caching(context=context):
            return await call_next(context)

        if cached_value := await self._get_prompt_cache.get(
            key=_make_get_prompt_cache_key(msg=context.message)
        ):
            return cached_value

        value: mcp.types.GetPromptResult = await call_next(context=context)

        cachable_value: CachablePromptResult = CachablePromptResult(
            messages=value.messages, description=value.description, _meta=value.meta
        )

        await self._get_prompt_cache.put(
            key=_make_get_prompt_cache_key(msg=context.message),
            value=cachable_value,
        )

        return cachable_value

    @override
    async def on_notification(
        self,
        context: MiddlewareContext[mcp.types.Notification[Any, Any]],
        call_next: CallNext[mcp.types.Notification[Any, Any], Any],
    ) -> Any:
        """Handle a notification from the server. If the notification is a tool/resource/prompt list changed
        notification, delete the cache for the affected method."""
        if isinstance(context.message, mcp.types.ToolListChangedNotification):
            _ = await self._list_tools_cache.delete(key=GLOBAL_KEY)
        elif isinstance(context.message, mcp.types.ResourceListChangedNotification):
            _ = await self._list_resources_cache.delete(key=GLOBAL_KEY)
        elif isinstance(context.message, mcp.types.PromptListChangedNotification):
            _ = await self._list_prompts_cache.delete(key=GLOBAL_KEY)
        else:
            pass

        return await call_next(context=context)

    def _matches_tool_cache_settings(
        self, context: MiddlewareContext[mcp.types.CallToolRequestParams]
    ) -> bool:
        """Check if the tool matches the cache settings for tool calls."""

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
        """Get the cache settings for a method."""

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
        """Get the cache TTL for a method."""

        settings: SharedMethodSettings | None = self._get_cache_settings(
            context=context,
        )

        if not settings or "ttl" not in settings:
            return self._default_ttl

        return settings["ttl"]

    def _should_bypass_caching(self, context: MiddlewareContext[Any]) -> bool:
        """Check if the method should bypass caching."""

        if not (cache_settings := self._get_cache_settings(context=context)):
            return True

        if cache_settings.get("enabled") is False:
            return True

        return False

    def statistics(self) -> ResponseCachingStatistics:
        return ResponseCachingStatistics(
            list_tools=self._stats.statistics.collections.get("tools/list"),
            list_resources=self._stats.statistics.collections.get("resources/list"),
            list_prompts=self._stats.statistics.collections.get("prompts/list"),
            read_resource=self._stats.statistics.collections.get("resources/read"),
            get_prompt=self._stats.statistics.collections.get("prompts/get"),
            call_tool=self._stats.statistics.collections.get("tools/call"),
        )


def _make_call_tool_cache_key(msg: mcp.types.CallToolRequestParams) -> str:
    """Make a cache key for a tool call by hashing the tool name and its arguments."""

    return f"{msg.name}:{_get_arguments_str(msg.arguments)}"


def _make_read_resource_cache_key(msg: mcp.types.ReadResourceRequestParams) -> str:
    """Make a cache key for a resource read by hashing the resource URI."""

    return f"{msg.uri}"


def _make_get_prompt_cache_key(msg: mcp.types.GetPromptRequestParams) -> str:
    """Make a cache key for a prompt get by hashing the prompt name and its arguments."""

    return f"{msg.name}:{_get_arguments_str(msg.arguments)}"


def _get_arguments_str(arguments: dict[str, Any] | None) -> str:
    """Get a string representation of the arguments."""

    if arguments is None:
        return "null"

    try:
        return pydantic_core.to_json(value=arguments, fallback=str).decode()

    except TypeError:
        return repr(arguments)


def _get_size_of_content_blocks(
    value: mcp.types.ContentBlock | Sequence[mcp.types.ContentBlock],
) -> int:
    """Get the size of a series of content blocks by summing the size of the JSON representation of each block."""

    if isinstance(value, mcp.types.ContentBlock):
        value = [value]

    return sum([len(item.model_dump_json()) for item in value])


def _get_size_of_tool_result(value: ToolResult) -> int:
    """Get the size of a tool result by summing the size of the content blocks and the size of the structured content."""

    content_size = _get_size_of_content_blocks(value.content)
    structured_content_size = len(
        json.dumps(
            value.structured_content, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )

    return content_size + structured_content_size
