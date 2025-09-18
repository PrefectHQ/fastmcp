"""Tests for response caching middleware."""

import tempfile
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types
import pytest
from inline_snapshot import snapshot
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import GetPromptResult, PromptMessage, TextContent, TextResourceContents
from pydantic import AnyUrl, BaseModel

from fastmcp import FastMCP
from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.prompts.prompt import FunctionPrompt, Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.caching import (
    BaseCacheEntry,
    CachablePrompt,
    CachableResource,
    CacheMethodStats,
    CacheProtocol,
    CacheStats,
    CallToolSettings,
    DiskCache,
    GetPromptCacheEntry,
    InMemoryCache,
    ListPromptsCacheEntry,
    ListResourcesCacheEntry,
    ListToolsCacheEntry,
    MethodSettings,
    ReadResourceCacheEntry,
    ResponseCachingMiddleware,
    ToolResultCacheEntry,
)
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

TEST_URI = AnyUrl("https://test_uri")

SAMPLE_READ_RESOURCE_CONTENTS = ReadResourceContents(
    content="test_text",
    mime_type="text/plain",
)


def sample_resource_fn() -> list[ReadResourceContents]:
    return [SAMPLE_READ_RESOURCE_CONTENTS]


SAMPLE_PROMPT_CONTENTS = TextContent(type="text", text="test_text")


def sample_prompt_fn() -> PromptMessage:
    return PromptMessage(role="user", content=SAMPLE_PROMPT_CONTENTS)


SAMPLE_RESOURCE = Resource.from_function(
    fn=sample_resource_fn, uri=TEST_URI, name="test_resource"
)

SAMPLE_PROMPT = Prompt.from_function(fn=sample_prompt_fn, name="test_prompt")
SAMPLE_GET_PROMPT_RESULT = mcp.types.GetPromptResult(
    messages=[
        mcp.types.PromptMessage(
            role="user", content=mcp.types.TextContent(type="text", text="test_text")
        )
    ]
)
SAMPLE_TOOL = Tool(name="test_tool", parameters={"param1": "value1", "param2": 42})
SAMPLE_TOOL_RESULT = ToolResult(
    content=[TextContent(type="text", text="test_text")],
    structured_content={"result": "test_result"},
)
SAMPLE_TOOL_RESULT_LARGE = ToolResult(
    content=[TextContent(type="text", text="test_text" * 100)],
    structured_content={"result": "test_result"},
)


class CrazyModel(BaseModel):
    a: int
    b: int
    c: str
    d: float
    e: bool
    f: list[int]
    g: dict[str, int]
    h: list[dict[str, int]]
    i: dict[str, list[int]]


def extract_content_for_snapshot(result: ToolResult | CallToolResult) -> dict[str, Any]:
    return {
        "content": [c.model_dump() for c in result.content],
        "structured_content": result.structured_content,
    }


def dump_mcp_type(
    model: BaseModel | ToolResult | ReadResourceContents,
) -> dict[str, Any]:
    if isinstance(model, ToolResult):
        return extract_content_for_snapshot(model)

    if isinstance(model, ReadResourceContents):
        return {
            "content": model.content,
            "mime_type": model.mime_type,
        }

    return model.model_dump()


def dump_mcp_types(
    model: BaseModel
    | ToolResult
    | Sequence[BaseModel]
    | Sequence[ToolResult]
    | Sequence[ReadResourceContents],
) -> list[dict[str, Any]]:
    if isinstance(model, Sequence):
        return [dump_mcp_type(model=m) for m in model]

    return dump_mcp_type(model=model)  # type: ignore


@pytest.fixture
def crazy_model() -> CrazyModel:
    return CrazyModel(
        a=5,
        b=10,
        c="test",
        d=1.0,
        e=True,
        f=[1, 2, 3],
        g={"a": 1, "b": 2},
        h=[{"a": 1, "b": 2}],
        i={"a": [1, 2]},
    )


class TrackingCalculator:
    add_calls: int
    multiply_calls: int
    crazy_calls: int

    def __init__(self):
        self.add_calls = 0
        self.multiply_calls = 0
        self.crazy_calls = 0

    def add(self, a: int, b: int) -> int:
        self.add_calls += 1
        return a + b

    def multiply(self, a: int, b: int) -> int:
        self.multiply_calls += 1
        return a * b

    def crazy(self, a: CrazyModel) -> CrazyModel:
        self.crazy_calls += 1
        return a

    def how_to_calculate(self, a: int, b: int) -> str:
        return f"To calculate {a} + {b}, you need to add {a} and {b} together."

    def get_add_calls(self) -> int:
        return self.add_calls

    def get_multiply_calls(self) -> int:
        return self.multiply_calls

    def get_crazy_calls(self) -> int:
        return self.crazy_calls

    def add_tools(self, fastmcp: FastMCP, prefix: str = ""):
        fastmcp.add_tool(tool=Tool.from_function(fn=self.add, name=f"{prefix}add"))
        fastmcp.add_tool(
            tool=Tool.from_function(fn=self.multiply, name=f"{prefix}multiply")
        )
        fastmcp.add_tool(tool=Tool.from_function(fn=self.crazy, name=f"{prefix}crazy"))

    def add_prompts(self, fastmcp: FastMCP, prefix: str = ""):
        fastmcp.add_prompt(
            prompt=FunctionPrompt.from_function(
                fn=self.how_to_calculate, name=f"{prefix}how_to_calculate"
            )
        )

    def add_resources(self, fastmcp: FastMCP, prefix: str = ""):
        fastmcp.add_resource(
            resource=Resource.from_function(
                fn=self.get_add_calls,
                uri="resource://add_calls",
                name=f"{prefix}add_calls",
            )
        )
        fastmcp.add_resource(
            resource=Resource.from_function(
                fn=self.get_multiply_calls,
                uri="resource://multiply_calls",
                name=f"{prefix}multiply_calls",
            )
        )
        fastmcp.add_resource(
            resource=Resource.from_function(
                fn=self.get_crazy_calls,
                uri="resource://crazy_calls",
                name=f"{prefix}crazy_calls",
            )
        )


@pytest.fixture
def tracking_calculator() -> TrackingCalculator:
    return TrackingCalculator()


@pytest.fixture
def mock_context() -> MiddlewareContext[mcp.types.CallToolRequestParams]:
    """Create a mock middleware context for tool calls."""
    context = MagicMock(spec=MiddlewareContext[mcp.types.CallToolRequestParams])
    context.message = mcp.types.CallToolRequestParams(
        name="test_tool", arguments={"param1": "value1", "param2": 42}
    )
    context.method = "tools/call"
    return context


@pytest.fixture
def mock_call_next() -> CallNext[mcp.types.CallToolRequestParams, ToolResult]:
    """Create a mock call_next function."""
    return AsyncMock(
        return_value=ToolResult(
            content=[{"type": "text", "text": "test result"}],
            structured_content={"result": "success", "value": 123},
        )
    )


@pytest.fixture
def sample_tool_result() -> ToolResult:
    """Create a sample tool result for testing."""
    return ToolResult(
        content=[{"type": "text", "text": "cached result"}],
        structured_content={"cached": True, "data": "test"},
    )


class TestCacheEntry:
    """Test CacheEntry class functionality."""

    def test_init_and_expiration(self):
        """Test cache entry initialization and expiration logic."""
        entry: ToolResultCacheEntry[ToolResult] = ToolResultCacheEntry(
            key="test_key",
            value=ToolResult(
                content=[{"type": "text", "text": "success"}],
                structured_content={"result": "success"},
            ),
            ttl=3600,
        )

        assert entry.key == "test_key"
        assert not entry.is_expired()

        # Test expired entry
        expired_entry: ToolResultCacheEntry[ToolResult] = ToolResultCacheEntry(
            key="test_key",
            value=ToolResult(
                content=[{"type": "text", "text": "success"}],
                structured_content={"result": "success"},
            ),
            ttl=-1,
        )

        assert expired_entry.is_expired()

    def test_tool_result_cache_entry_validation(self):
        """Test ToolResultCacheEntry class functionality."""
        entry: ToolResultCacheEntry[ToolResult] = ToolResultCacheEntry(
            key="test_key",
            value=SAMPLE_TOOL_RESULT,
            ttl=3600,
        )

        dumped_entry = entry.model_dump()
        new_entry = ToolResultCacheEntry.model_validate(dumped_entry)

        assert dump_mcp_types(model=new_entry.value.content) == snapshot(
            [{"type": "text", "text": "test_text", "annotations": None, "meta": None}]
        )
        assert new_entry.value.structured_content == snapshot({"result": "test_result"})

    def test_read_resource_cache_entry_validation(self):
        """Test ReadResourceCacheEntry class functionality."""
        entry: ReadResourceCacheEntry[list[ReadResourceContents]] = (
            ReadResourceCacheEntry(
                key="test_key",
                value=[SAMPLE_READ_RESOURCE_CONTENTS],
                ttl=3600,
            )
        )

        dumped_entry = entry.model_dump()
        new_entry = ReadResourceCacheEntry.model_validate(dumped_entry)

        assert new_entry.value == snapshot(
            [ReadResourceContents(content="test_text", mime_type="text/plain")]
        )

    def test_get_prompt_cache_entry_validation(self):
        """Test GetPromptCacheEntry class functionality."""
        entry: GetPromptCacheEntry = GetPromptCacheEntry(
            key="test_key",
            value=SAMPLE_GET_PROMPT_RESULT,
            ttl=3600,
        )

        dumped_entry = entry.model_dump()
        new_entry = GetPromptCacheEntry.model_validate(dumped_entry)

        assert new_entry.value == snapshot(
            GetPromptResult(
                messages=[
                    PromptMessage(
                        role="user", content=TextContent(type="text", text="test_text")
                    )
                ]
            )
        )

    def test_list_tools_cache_entry_validation(self):
        """Test ListToolsCacheEntry class functionality."""
        entry: ListToolsCacheEntry = ListToolsCacheEntry(
            key="test_key",
            value=[SAMPLE_TOOL],
            ttl=3600,
        )

        dumped_entry = entry.model_dump()
        new_entry = ListToolsCacheEntry.model_validate(dumped_entry)

        assert new_entry.value == snapshot(
            [Tool(name="test_tool", parameters={"param1": "value1", "param2": 42})]
        )

    def test_list_resources_cache_entry_validation(self):
        """Test ListResourcesCacheEntry class functionality."""
        entry: ListResourcesCacheEntry = ListResourcesCacheEntry(
            key="test_key",
            value=[SAMPLE_RESOURCE],
            ttl=3600,
        )

        dumped_entry = entry.model_dump()
        new_entry = ListResourcesCacheEntry.model_validate(dumped_entry)

        assert new_entry.value == snapshot(
            [CachableResource(name="test_resource", uri=AnyUrl("https://test_uri/"))]
        )

    def test_list_prompts_cache_entry_validation(self):
        """Test ListPromptsCacheEntry class functionality."""
        entry: ListPromptsCacheEntry = ListPromptsCacheEntry(
            key="test_key",
            value=[SAMPLE_PROMPT],
            ttl=3600,
        )

        dumped_entry = entry.model_dump()
        new_entry = ListPromptsCacheEntry.model_validate(dumped_entry)

        assert new_entry.value == snapshot(
            [CachablePrompt(name="test_prompt", arguments=[])]
        )


class TestMemoryCache:
    """Test InMemoryCache implementation."""

    async def test_size_limit(self, sample_tool_result):
        """Test cache size limit enforcement."""
        cache = InMemoryCache(max_entries=2)

        # Fill cache to capacity
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="key1", value=sample_tool_result, ttl=3600
            )
        )
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="key2", value=sample_tool_result, ttl=3600
            )
        )

        # Add one more - should evict the first
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="key3", value=sample_tool_result, ttl=3600
            )
        )

        assert len(cache._cache) == 2
        assert "tools/call:key1" not in cache._cache
        assert "tools/call:key2" in cache._cache
        assert "tools/call:key3" in cache._cache


class TestCacheImplementations:
    """Test InMemoryCache implementation."""

    @pytest.fixture(params=["memory", "disk"])
    async def cache(self, request):
        if request.param == "memory":
            return InMemoryCache()
        else:
            with tempfile.TemporaryDirectory() as temp_dir:
                return DiskCache(path=temp_dir)

    async def test_get_none_if_not_set(self, cache: CacheProtocol):
        """Test that we get None if a value is not set."""
        assert await cache.get_entry(collection="tools/call", key="test_key") is None

    async def test_list_tools(self, cache: CacheProtocol):
        """Test that we can list tools from the cache."""
        await cache.set_entry(
            cache_entry=ListToolsCacheEntry(
                key="test_key", value=[SAMPLE_TOOL], ttl=3600
            )
        )
        result = await cache.get_entry(collection="tools/list", key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            [
                {
                    "name": "test_tool",
                    "title": None,
                    "description": None,
                    "tags": set(),
                    "meta": None,
                    "enabled": True,
                    "parameters": {"param1": "value1", "param2": 42},
                    "output_schema": None,
                    "annotations": None,
                    "serializer": None,
                }
            ]
        )

    async def test_tool_result(self, cache: CacheProtocol):
        """Test that we can get a tool result from the cache."""
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="test_key", value=SAMPLE_TOOL_RESULT, ttl=3600
            )
        )
        result = await cache.get_entry(collection="tools/call", key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            {
                "content": [
                    {
                        "type": "text",
                        "text": "test_text",
                        "annotations": None,
                        "meta": None,
                    }
                ],
                "structured_content": {"result": "test_result"},
            }
        )

    async def test_list_resources(self, cache: CacheProtocol):
        """Test that we can list resources from the cache."""
        await cache.set_entry(
            cache_entry=ListResourcesCacheEntry(
                key="test_key", value=[SAMPLE_RESOURCE], ttl=3600
            )
        )
        result = await cache.get_entry(collection="resources/list", key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            [
                {
                    "name": "test_resource",
                    "title": None,
                    "description": None,
                    "tags": set(),
                    "meta": None,
                    "enabled": True,
                    "uri": AnyUrl("https://test_uri/"),
                    "mime_type": "text/plain",
                    "annotations": None,
                }
            ]
        )

    async def test_read_resource(self, cache: CacheProtocol):
        """Test that we can read a resource from the cache."""
        await cache.set_entry(
            cache_entry=ReadResourceCacheEntry(
                key="test_key", value=[SAMPLE_READ_RESOURCE_CONTENTS], ttl=3600
            )
        )
        result = await cache.get_entry(collection="resources/read", key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            [{"content": "test_text", "mime_type": "text/plain"}]
        )

    async def test_list_prompts(self, cache: CacheProtocol):
        """Test that we can list prompts from the cache."""
        await cache.set_entry(
            cache_entry=ListPromptsCacheEntry(
                key="test_key", value=[SAMPLE_PROMPT], ttl=3600
            )
        )
        result = await cache.get_entry(collection="prompts/list", key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            [
                {
                    "name": "test_prompt",
                    "title": None,
                    "description": None,
                    "tags": set(),
                    "meta": None,
                    "enabled": True,
                    "arguments": [],
                }
            ]
        )

    async def test_get_prompt(self, cache: CacheProtocol):
        """Test that we can get a prompt from the cache."""
        entry = GetPromptCacheEntry(
            key="test_key", value=SAMPLE_GET_PROMPT_RESULT, ttl=3600
        )
        await cache.set_entry(cache_entry=entry)
        result = await cache.get_entry(collection=entry.collection, key="test_key")

        assert result is not None
        assert dump_mcp_types(model=result.value) == snapshot(
            {
                "meta": None,
                "description": None,
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": "test_text",
                            "annotations": None,
                            "meta": None,
                        },
                    }
                ],
            }
        )

    async def test_set_get_delete_get_value(self, cache: CacheProtocol):
        """Test that we can set, get, delete, and get a value from the cache."""
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="test_key",
                value=SAMPLE_TOOL_RESULT,
                ttl=3600,
            )
        )
        result: BaseCacheEntry | None = await cache.get_entry(
            collection="tools/call", key="test_key"
        )

        assert result is not None
        assert dump_mcp_types(model=result.value) == dump_mcp_types(
            model=SAMPLE_TOOL_RESULT
        )

        await cache.delete(collection="tools/call", key="test_key")

        assert await cache.get_entry(collection="tools/call", key="test_key") is None

    async def test_expiration_and_cleanup(self, cache: CacheProtocol):
        """Test cache expiration and cleanup."""
        # Create an expired entry
        await cache.set_entry(
            cache_entry=ToolResultCacheEntry(
                key="expired_key",
                value=SAMPLE_TOOL_RESULT,
                ttl=-1,
            )
        )

        # Should return None and remove expired entry
        result = await cache.get_entry(collection="tools/call", key="expired_key")

        assert result is None

        assert await cache.get_entry(collection="tools/call", key="expired_key") is None


class TestResponseCachingMiddleware:
    """Test ResponseCachingMiddleware functionality."""

    def test_initialization(self):
        """Test middleware initialization."""
        middleware = ResponseCachingMiddleware(
            method_settings=MethodSettings(
                call_tool=CallToolSettings(
                    included_tools=["tool1"],
                    excluded_tools=["tool2"],
                )
            ),
            default_ttl=1800,
        )

        assert middleware.method_settings == snapshot(
            {"call_tool": {"included_tools": ["tool1"], "excluded_tools": ["tool2"]}}
        )
        assert middleware._default_ttl == 1800
        assert middleware._max_item_size is None

    @pytest.mark.parametrize(
        ("tool_name", "included_tools", "excluded_tools", "result"),
        [
            ("tool", ["tool", "tool2"], [], True),
            ("tool", ["second tool", "third tool"], [], False),
            ("tool", [], ["tool"], False),
            ("tool", [], ["second tool"], True),
            ("tool", ["tool", "second tool"], ["tool"], False),
            ("tool", ["tool", "second tool"], ["second tool"], True),
        ],
        ids=[
            "tool is included",
            "tool is not included",
            "tool is excluded",
            "tool is not excluded",
            "tool is included and excluded (excluded takes precedence)",
            "tool is included and not excluded",
        ],
    )
    def test_tool_call_filtering(
        self,
        tool_name: str,
        included_tools: list[str],
        excluded_tools: list[str],
        result: bool,
    ):
        """Test tool filtering logic."""

        middleware1 = ResponseCachingMiddleware(
            method_settings=MethodSettings(
                call_tool=CallToolSettings(
                    included_tools=included_tools, excluded_tools=excluded_tools
                )
            ),
        )
        assert (
            middleware1._matches_tool_cache_settings(
                context=MiddlewareContext(
                    method="tools/call",
                    message=mcp.types.CallToolRequestParams(name=tool_name),
                )
            )
            is result
        )

    async def test_large_value(self):
        """Test that we can set and get a large value."""
        cache = InMemoryCache()
        middleware = ResponseCachingMiddleware(cache, max_item_size=100)

        await middleware._store_in_cache_and_return(
            context=MiddlewareContext(
                method="tools/call",
                message=mcp.types.CallToolRequestParams(name="test_tool"),
            ),
            key="test_key",
            value=SAMPLE_TOOL_RESULT_LARGE,
        )

        assert middleware._stats.get_too_big("tools/call") == 1

    def test_cache_key_generation(self):
        """Test cache key generation."""
        from fastmcp.server.middleware.caching import (
            _make_call_tool_cache_key,
            _make_get_prompt_cache_key,
            _make_read_resource_cache_key,
        )

        msg = mcp.types.CallToolRequestParams(
            name="test_tool", arguments={"param1": "value1", "param2": 42}
        )

        key = _make_call_tool_cache_key(msg)

        # Should be a SHA256 hash
        assert len(key) == 64
        assert key == snapshot(
            "7fa3d5c7967a202457eeca0731709fd87ec98546ddaee829ee86ca54b1858c59"
        )

        msg = mcp.types.ReadResourceRequestParams(
            uri=AnyUrl("https://test_uri"),
        )

        key = _make_read_resource_cache_key(msg)

        assert len(key) == 64
        assert key == snapshot(
            "e34cc47c03ed1ad54f02501d95ecc463b65646568961c97ca4b730cb274e9d42"
        )

        msg = mcp.types.GetPromptRequestParams(
            name="test_prompt", arguments={"param1": "value1"}
        )

        key = _make_get_prompt_cache_key(msg)

        assert len(key) == 64
        assert key == snapshot(
            "6306ff84fd3ff247a4bd91271e9d727d7f051bba53fb2e3bf80958988c4baf57"
        )

    async def test_cache_miss_and_hit(
        self,
    ):
        """Test cache miss and hit scenarios."""
        middleware = ResponseCachingMiddleware()

        mock_call_next = AsyncMock(
            return_value=ToolResult(
                content=[{"type": "text", "text": "test result"}],
                structured_content={"result": "success", "value": 123},
            )
        )

        mock_context = MagicMock(
            spec=MiddlewareContext[mcp.types.CallToolRequestParams]
        )
        mock_context.message = mcp.types.CallToolRequestParams(
            name="test_tool", arguments={"param1": "value1", "param2": 42}
        )
        mock_context.method = "tools/call"

        # First call - cache miss
        result1 = await middleware.on_call_tool(
            context=mock_context, call_next=mock_call_next
        )
        assert middleware._stats.get_misses("tools/call") == 1
        assert middleware._stats.get_hits("tools/call") == 0

        # Second call - cache hit
        mock_call_next.reset_mock()
        result2 = await middleware.on_call_tool(
            context=mock_context, call_next=mock_call_next
        )

        assert result1.content == result2.content
        assert not mock_call_next.called  # Should not call downstream
        assert middleware._stats.get_hits("tools/call") == 1
        assert middleware._stats.get_misses("tools/call") == 1


class TestResponseCachingMiddlewareIntegration:
    """Integration tests with real FastMCP server."""

    @pytest.fixture(params=["memory", "disk", "elasticsearch"])
    async def caching_server(
        self,
        tracking_calculator: TrackingCalculator,
        request,
    ):
        """Create a FastMCP server for caching tests."""
        mcp = FastMCP("CachingTestServer")

        with tempfile.TemporaryDirectory() as temp_dir:
            response_caching_middleware = ResponseCachingMiddleware(
                cache_backend=DiskCache(path=temp_dir)
                if request.param == "disk"
                else InMemoryCache()
            )

            mcp.add_middleware(middleware=response_caching_middleware)

            tracking_calculator.add_tools(fastmcp=mcp)
            tracking_calculator.add_resources(fastmcp=mcp)
            tracking_calculator.add_prompts(fastmcp=mcp)

            yield mcp

    @pytest.fixture
    def non_caching_server(self, tracking_calculator: TrackingCalculator):
        """Create a FastMCP server for non-caching tests."""
        mcp = FastMCP("NonCachingTestServer")
        tracking_calculator.add_tools(fastmcp=mcp)
        return mcp

    async def test_list_tools(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that tool list caching works with a real FastMCP server."""

        async with Client(caching_server) as client:
            pre_tool_list: list[mcp.types.Tool] = await client.list_tools()
            assert len(pre_tool_list) == 3

            # Add a tool and make sure it's missing from the list tool response
            caching_server.add_tool(
                tool=Tool.from_function(fn=tracking_calculator.add, name="add_2")
            )

            post_tool_list: list[mcp.types.Tool] = await client.list_tools()
            assert len(post_tool_list) == 3

            assert pre_tool_list == post_tool_list

    async def test_call_tool(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(fastmcp=caching_server)

        async with Client[FastMCPTransport](caching_server) as client:
            call_tool_result_one: CallToolResult = await client.call_tool(
                "add", {"a": 5, "b": 3}
            )

            assert tracking_calculator.add_calls == 1
            call_tool_result_two: CallToolResult = await client.call_tool(
                "add", {"a": 5, "b": 3}
            )
            assert call_tool_result_one == call_tool_result_two

    async def test_list_resources(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that list resources caching works with a real FastMCP server."""
        async with Client[FastMCPTransport](transport=caching_server) as client:
            pre_resource_list: list[mcp.types.Resource] = await client.list_resources()

            assert len(pre_resource_list) == 3

            tracking_calculator.add_resources(fastmcp=caching_server)

            post_resource_list: list[mcp.types.Resource] = await client.list_resources()
            assert len(post_resource_list) == 3

            assert pre_resource_list == post_resource_list

    async def test_read_resource(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that get resources caching works with a real FastMCP server."""
        async with Client[FastMCPTransport](transport=caching_server) as client:
            pre_resource = await client.read_resource(uri="resource://add_calls")
            assert isinstance(pre_resource[0], TextResourceContents)
            assert pre_resource[0].text == "0"

            tracking_calculator.add_calls = 1

            post_resource = await client.read_resource(uri="resource://add_calls")
            assert isinstance(post_resource[0], TextResourceContents)
            assert post_resource[0].text == "0"
            assert pre_resource == post_resource

    async def test_list_prompts(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that list prompts caching works with a real FastMCP server."""
        async with Client[FastMCPTransport](transport=caching_server) as client:
            pre_prompt_list: list[mcp.types.Prompt] = await client.list_prompts()

            assert len(pre_prompt_list) == 1

            tracking_calculator.add_prompts(fastmcp=caching_server)

            post_prompt_list: list[mcp.types.Prompt] = await client.list_prompts()

            assert len(post_prompt_list) == 1

            assert pre_prompt_list == post_prompt_list

    async def test_get_prompts(
        self, caching_server: FastMCP, tracking_calculator: TrackingCalculator
    ):
        """Test that get prompts caching works with a real FastMCP server."""
        async with Client[FastMCPTransport](transport=caching_server) as client:
            pre_prompt = await client.get_prompt(
                name="how_to_calculate", arguments={"a": 5, "b": 3}
            )

            pre_prompt_content = pre_prompt.messages[0].content
            assert isinstance(pre_prompt_content, TextContent)
            assert (
                pre_prompt_content.text
                == "To calculate 5 + 3, you need to add 5 and 3 together."
            )

            tracking_calculator.add_prompts(fastmcp=caching_server)

            post_prompt = await client.get_prompt(
                name="how_to_calculate", arguments={"a": 5, "b": 3}
            )

            assert pre_prompt == post_prompt


class TestCacheStats:
    """Test CacheStats functionality."""

    def test_stats_initialization(self):
        """Test cache stats initialization."""
        stats = CacheStats(
            collections={
                "tools/call": CacheMethodStats(hits=5, misses=10),
                "tools/list": CacheMethodStats(hits=0, misses=0),
            }
        )

        assert stats.get_hits("tools/call") == 5
        assert stats.get_misses("tools/call") == 10
        assert stats.get_hits("tools/list") == 0
        assert stats.get_misses("tools/list") == 0
