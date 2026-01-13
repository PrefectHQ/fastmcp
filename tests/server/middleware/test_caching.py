"""Tests for response caching middleware."""

import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

import mcp.types
import pytest
from inline_snapshot import snapshot
from key_value.aio.stores.disk import DiskStore
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.statistics.wrapper import (
    GetStatistics,
    KVStoreCollectionStatistics,
    PutStatistics,
)
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import TextContent, TextResourceContents
from pydantic import AnyUrl, BaseModel

from fastmcp import Context, FastMCP
from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.prompts.prompt import Message, Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.caching import (
    CachableToolResult,
    CallToolSettings,
    ResponseCachingMiddleware,
    ResponseCachingStatistics,
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


def sample_prompt_fn() -> Message:
    return Message("test_text")


SAMPLE_RESOURCE = Resource.from_function(
    fn=sample_resource_fn, uri=TEST_URI, name="test_resource"
)

SAMPLE_PROMPT = Prompt.from_function(fn=sample_prompt_fn, name="test_prompt")
SAMPLE_GET_PROMPT_RESULT = mcp.types.GetPromptResult(
    messages=[Message("test_text").to_mcp_prompt_message()]
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
    very_large_response_calls: int

    def __init__(self):
        self.add_calls = 0
        self.multiply_calls = 0
        self.crazy_calls = 0
        self.very_large_response_calls = 0

    def add(self, a: int, b: int) -> int:
        self.add_calls += 1
        return a + b

    def multiply(self, a: int, b: int) -> int:
        self.multiply_calls += 1
        return a * b

    def very_large_response(self) -> str:
        self.very_large_response_calls += 1
        return "istenchars" * 100000  # 1,000,000 characters, 1mb

    def crazy(self, a: CrazyModel) -> CrazyModel:
        self.crazy_calls += 1
        return a

    def how_to_calculate(self, a: int, b: int) -> str:
        return f"To calculate {a} + {b}, you need to add {a} and {b} together."

    def get_add_calls(self) -> str:
        return str(self.add_calls)

    def get_multiply_calls(self) -> str:
        return str(self.multiply_calls)

    def get_crazy_calls(self) -> str:
        return str(self.crazy_calls)

    async def update_tool_list(self, context: Context):
        import mcp.types

        await context.send_notification(mcp.types.ToolListChangedNotification())

    def add_tools(self, fastmcp: FastMCP, prefix: str = ""):
        _ = fastmcp.add_tool(tool=Tool.from_function(fn=self.add, name=f"{prefix}add"))
        _ = fastmcp.add_tool(
            tool=Tool.from_function(fn=self.multiply, name=f"{prefix}multiply")
        )
        _ = fastmcp.add_tool(
            tool=Tool.from_function(fn=self.crazy, name=f"{prefix}crazy")
        )
        _ = fastmcp.add_tool(
            tool=Tool.from_function(
                fn=self.very_large_response, name=f"{prefix}very_large_response"
            )
        )
        _ = fastmcp.add_tool(
            tool=Tool.from_function(
                fn=self.update_tool_list, name=f"{prefix}update_tool_list"
            )
        )

    def add_prompts(self, fastmcp: FastMCP, prefix: str = ""):
        _ = fastmcp.add_prompt(
            prompt=FunctionPrompt.from_function(
                fn=self.how_to_calculate, name=f"{prefix}how_to_calculate"
            )
        )

    def add_resources(self, fastmcp: FastMCP, prefix: str = ""):
        _ = fastmcp.add_resource(
            resource=Resource.from_function(
                fn=self.get_add_calls,
                uri="resource://add_calls",
                name=f"{prefix}add_calls",
            )
        )
        _ = fastmcp.add_resource(
            resource=Resource.from_function(
                fn=self.get_multiply_calls,
                uri="resource://multiply_calls",
                name=f"{prefix}multiply_calls",
            )
        )
        _ = fastmcp.add_resource(
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
            content=[TextContent(type="text", text="test result")],
            structured_content={"result": "success", "value": 123},
        )
    )


@pytest.fixture
def sample_tool_result() -> ToolResult:
    """Create a sample tool result for testing."""
    return ToolResult(
        content=[TextContent(type="text", text="cached result")],
        structured_content={"cached": True, "data": "test"},
    )


class TestResponseCachingMiddleware:
    """Test ResponseCachingMiddleware functionality."""

    def test_initialization(self):
        """Test middleware initialization."""
        assert ResponseCachingMiddleware(
            call_tool_settings=CallToolSettings(
                included_tools=["tool1"],
                excluded_tools=["tool2"],
            ),
        )

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
            call_tool_settings=CallToolSettings(
                included_tools=included_tools, excluded_tools=excluded_tools
            ),
        )
        assert middleware1._matches_tool_cache_settings(tool_name=tool_name) is result


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="SQLite caching tests are flaky on Windows due to temp directory issues.",
)
class TestResponseCachingMiddlewareIntegration:
    """Integration tests with real FastMCP server."""

    @pytest.fixture(params=["memory", "disk"])
    async def caching_server(
        self,
        tracking_calculator: TrackingCalculator,
        request: pytest.FixtureRequest,
    ):
        """Create a FastMCP server for caching tests."""
        mcp = FastMCP("CachingTestServer")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            disk_store: DiskStore = DiskStore(directory=temp_dir)
            response_caching_middleware = ResponseCachingMiddleware(
                cache_storage=disk_store if request.param == "disk" else MemoryStore(),
            )

            mcp.add_middleware(middleware=response_caching_middleware)

            tracking_calculator.add_tools(fastmcp=mcp)
            tracking_calculator.add_resources(fastmcp=mcp)
            tracking_calculator.add_prompts(fastmcp=mcp)

            yield mcp

            await disk_store.close()

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
            assert len(pre_tool_list) == 5

            # Add a tool and make sure it's missing from the list tool response
            _ = caching_server.add_tool(
                tool=Tool.from_function(fn=tracking_calculator.add, name="add_2")
            )

            post_tool_list: list[mcp.types.Tool] = await client.list_tools()
            assert len(post_tool_list) == 5

            assert pre_tool_list == post_tool_list

    async def test_call_tool(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(fastmcp=caching_server)

        async with Client[FastMCPTransport](transport=caching_server) as client:
            call_tool_result_one: CallToolResult = await client.call_tool(
                "add", {"a": 5, "b": 3}
            )

            assert tracking_calculator.add_calls == 1
            call_tool_result_two: CallToolResult = await client.call_tool(
                "add", {"a": 5, "b": 3}
            )
            assert call_tool_result_one == call_tool_result_two

    async def test_call_tool_very_large_value(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(fastmcp=caching_server)

        async with Client[FastMCPTransport](transport=caching_server) as client:
            call_tool_result_one: CallToolResult = await client.call_tool(
                "very_large_response", {}
            )

            assert tracking_calculator.very_large_response_calls == 1
            call_tool_result_two: CallToolResult = await client.call_tool(
                "very_large_response", {}
            )
            assert call_tool_result_one == call_tool_result_two
            assert tracking_calculator.very_large_response_calls == 2

    async def test_call_tool_crazy_value(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
        crazy_model: CrazyModel,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(fastmcp=caching_server)

        async with Client[FastMCPTransport](transport=caching_server) as client:
            call_tool_result_one: CallToolResult = await client.call_tool(
                "crazy", {"a": crazy_model}
            )

            assert tracking_calculator.crazy_calls == 1
            call_tool_result_two: CallToolResult = await client.call_tool(
                "crazy", {"a": crazy_model}
            )
            assert call_tool_result_one == call_tool_result_two
            assert tracking_calculator.crazy_calls == 1

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

    async def test_statistics(
        self,
        caching_server: FastMCP,
    ):
        """Test that statistics are collected correctly."""
        caching_middleware = caching_server.middleware[0]
        assert isinstance(caching_middleware, ResponseCachingMiddleware)

        async with Client[FastMCPTransport](transport=caching_server) as client:
            statistics = caching_middleware.statistics()
            assert statistics == snapshot(ResponseCachingStatistics())

            _ = await client.call_tool("add", {"a": 5, "b": 3})

            statistics = caching_middleware.statistics()
            assert statistics == snapshot(
                ResponseCachingStatistics(
                    list_tools=KVStoreCollectionStatistics(
                        get=GetStatistics(count=2, hit=1, miss=1),
                        put=PutStatistics(count=1),
                    ),
                    call_tool=KVStoreCollectionStatistics(
                        get=GetStatistics(count=1, miss=1), put=PutStatistics(count=1)
                    ),
                )
            )

            _ = await client.call_tool("add", {"a": 5, "b": 3})

            statistics = caching_middleware.statistics()
            assert statistics == snapshot(
                ResponseCachingStatistics(
                    list_tools=KVStoreCollectionStatistics(
                        get=GetStatistics(count=2, hit=1, miss=1),
                        put=PutStatistics(count=1),
                    ),
                    call_tool=KVStoreCollectionStatistics(
                        get=GetStatistics(count=2, hit=1, miss=1),
                        put=PutStatistics(count=1),
                    ),
                )
            )


class TestCachableToolResult:
    def test_wrap_and_unwrap(self):
        tool_result = ToolResult(
            "unstructured content",
            structured_content={"structured": "content"},
            meta={"meta": "data"},
        )

        cached_tool_result = CachableToolResult.wrap(tool_result).unwrap()

        assert cached_tool_result.content == tool_result.content
        assert cached_tool_result.structured_content == tool_result.structured_content
        assert cached_tool_result.meta == tool_result.meta


class TestCachingWithImportedServerPrefixes:
    """Test that caching preserves prefixes from imported servers.

    Regression tests for issue #2300: ResponseCachingMiddleware was losing
    prefix information when caching components from imported servers.
    """

    @pytest.fixture
    async def parent_with_imported_child(self, tracking_calculator: TrackingCalculator):
        """Create a parent server with an imported child server (prefixed)."""
        child = FastMCP("child")
        tracking_calculator.add_tools(fastmcp=child)
        tracking_calculator.add_resources(fastmcp=child)
        tracking_calculator.add_prompts(fastmcp=child)

        parent = FastMCP("parent")
        parent.add_middleware(ResponseCachingMiddleware())
        parent.mount(child, namespace="child")

        return parent

    async def test_tool_prefixes_preserved_after_cache_hit(
        self, parent_with_imported_child: FastMCP
    ):
        """Tool names should retain prefix after being served from cache."""
        async with Client(parent_with_imported_child) as client:
            # First call populates cache
            tools_first = await client.list_tools()
            tool_names_first = [t.name for t in tools_first]

            # Second call should come from cache
            tools_cached = await client.list_tools()
            tool_names_cached = [t.name for t in tools_cached]

            # All tools should have prefix in both calls
            assert all(name.startswith("child_") for name in tool_names_first)
            assert all(name.startswith("child_") for name in tool_names_cached)
            assert tool_names_first == tool_names_cached

    async def test_resource_prefixes_preserved_after_cache_hit(
        self, parent_with_imported_child: FastMCP
    ):
        """Resource URIs should retain prefix after being served from cache."""
        async with Client(parent_with_imported_child) as client:
            # First call populates cache
            resources_first = await client.list_resources()
            resource_uris_first = [str(r.uri) for r in resources_first]

            # Second call should come from cache
            resources_cached = await client.list_resources()
            resource_uris_cached = [str(r.uri) for r in resources_cached]

            # All resources should have prefix in URI path in both calls
            # Resources get path-style prefix: resource://child/path
            assert all("://child/" in uri for uri in resource_uris_first)
            assert all("://child/" in uri for uri in resource_uris_cached)
            assert resource_uris_first == resource_uris_cached

    async def test_prompt_prefixes_preserved_after_cache_hit(
        self, parent_with_imported_child: FastMCP
    ):
        """Prompt names should retain prefix after being served from cache."""
        async with Client(parent_with_imported_child) as client:
            # First call populates cache
            prompts_first = await client.list_prompts()
            prompt_names_first = [p.name for p in prompts_first]

            # Second call should come from cache
            prompts_cached = await client.list_prompts()
            prompt_names_cached = [p.name for p in prompts_cached]

            # All prompts should have prefix in both calls
            assert all(name.startswith("child_") for name in prompt_names_first)
            assert all(name.startswith("child_") for name in prompt_names_cached)
            assert prompt_names_first == prompt_names_cached

    async def test_prefixed_tool_callable_after_cache_hit(
        self,
        parent_with_imported_child: FastMCP,
        tracking_calculator: TrackingCalculator,
    ):
        """Prefixed tools should be callable after cache populates."""
        async with Client(parent_with_imported_child) as client:
            # Trigger cache population
            await client.list_tools()
            await client.list_tools()  # From cache

            # Tool should be callable with prefixed name
            result = await client.call_tool("child_add", {"a": 5, "b": 3})
            assert not result.is_error
            assert tracking_calculator.add_calls == 1
