"""Tests for response caching middleware."""

import tempfile
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types
import pytest
from inline_snapshot import snapshot
from key_value.aio.stores.disk import DiskStore
from key_value.aio.stores.memory import MemoryStore
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import PromptMessage, TextContent, TextResourceContents
from pydantic import AnyUrl, BaseModel

from fastmcp import FastMCP
from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.prompts.prompt import FunctionPrompt, Prompt
from fastmcp.resources.resource import Resource
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    MethodSettings,
    ResponseCachingMiddleware,
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
        fastmcp.add_tool(
            tool=Tool.from_function(
                fn=self.very_large_response, name=f"{prefix}very_large_response"
            )
        )

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

    def test_method_settings(self):
        """Test method TTL."""
        middleware = ResponseCachingMiddleware(
            method_settings={
                "list_tools": {"ttl": 100},
                "call_tool": {"enabled": False},
            },
            default_ttl=1000,
        )

        tool_list_settings = middleware._get_cache_settings(
            context=MiddlewareContext(method="tools/list", message=MagicMock())
        )
        assert tool_list_settings == {"ttl": 100}

        call_tool_settings = middleware._get_cache_settings(
            context=MiddlewareContext(method="tools/call", message=MagicMock())
        )
        assert call_tool_settings == {"enabled": False}

        other_methods = [
            "resources/list",
            "prompts/list",
            "resources/read",
            "prompts/get",
        ]
        for method in other_methods:
            cache_settings = middleware._get_cache_settings(
                context=MiddlewareContext(method=method, message=MagicMock())
            )
            assert cache_settings is None

            should_bypass = middleware._should_bypass_caching(
                context=MiddlewareContext(method=method, message=MagicMock())
            )
            assert should_bypass

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

        assert key == snapshot('test_tool:{"param1":"value1","param2":42}')

        msg = mcp.types.ReadResourceRequestParams(
            uri=AnyUrl("https://test_uri"),
        )

        key = _make_read_resource_cache_key(msg)

        assert key == snapshot("https://test_uri/")

        msg = mcp.types.GetPromptRequestParams(
            name="test_prompt", arguments={"param1": "value1"}
        )

        key = _make_get_prompt_cache_key(msg)

        assert key == snapshot('test_prompt:{"param1":"value1"}')


class TestResponseCachingMiddlewareIntegration:
    """Integration tests with real FastMCP server."""

    @pytest.fixture(params=["memory", "disk"])
    async def caching_server(
        self,
        tracking_calculator: TrackingCalculator,
        request,
    ):
        """Create a FastMCP server for caching tests."""
        mcp = FastMCP("CachingTestServer")

        with tempfile.TemporaryDirectory() as temp_dir:
            disk_store = DiskStore(directory=temp_dir)
            response_caching_middleware = ResponseCachingMiddleware(
                cache_store=disk_store if request.param == "disk" else MemoryStore(),
                max_item_size=100000,  # 100kb
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
            assert len(pre_tool_list) == 4

            # Add a tool and make sure it's missing from the list tool response
            caching_server.add_tool(
                tool=Tool.from_function(fn=tracking_calculator.add, name="add_2")
            )

            post_tool_list: list[mcp.types.Tool] = await client.list_tools()
            assert len(post_tool_list) == 4

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

    async def test_call_tool_very_large_value(
        self,
        caching_server: FastMCP,
        tracking_calculator: TrackingCalculator,
    ):
        """Test that caching works with a real FastMCP server."""
        tracking_calculator.add_tools(fastmcp=caching_server)

        async with Client[FastMCPTransport](caching_server) as client:
            call_tool_result_one: CallToolResult = await client.call_tool(
                "very_large_response", {}
            )

            assert tracking_calculator.very_large_response_calls == 1
            call_tool_result_two: CallToolResult = await client.call_tool(
                "very_large_response", {}
            )
            assert call_tool_result_one == call_tool_result_two
            assert tracking_calculator.very_large_response_calls == 2

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
