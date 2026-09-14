"""Tests for response caching with versioned components."""

import mcp_types
import pytest
from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.server.middleware.caching import (
    ResponseCachingMiddleware,
    _make_call_tool_cache_key,
    _make_get_prompt_cache_key,
    _make_read_resource_cache_key,
)


@pytest.fixture
def versioned_caching_server() -> tuple[FastMCP, dict[str, int]]:
    mcp_server = FastMCP("VersionedCachingTestServer")
    mcp_server.add_middleware(ResponseCachingMiddleware())
    call_counts = {
        "tool_v1": 0,
        "tool_v2": 0,
        "resource_v1": 0,
        "resource_v2": 0,
        "prompt_v1": 0,
        "prompt_v2": 0,
    }

    @mcp_server.tool(name="calculate", version="1.0")
    def calculate_v1(x: int) -> int:
        call_counts["tool_v1"] += 1
        return x + 1

    @mcp_server.tool(name="calculate", version="2.0")
    def calculate_v2(x: int) -> int:
        call_counts["tool_v2"] += 1
        return x * 2

    @mcp_server.resource("data://config", version="1.0")
    def config_v1() -> str:
        call_counts["resource_v1"] += 1
        return "resource-v1"

    @mcp_server.resource("data://config", version="2.0")
    def config_v2() -> str:
        call_counts["resource_v2"] += 1
        return "resource-v2"

    @mcp_server.prompt(name="greet", version="1.0")
    def greet_v1(name: str) -> str:
        call_counts["prompt_v1"] += 1
        return f"prompt-v1:{name}"

    @mcp_server.prompt(name="greet", version="2.0")
    def greet_v2(name: str) -> str:
        call_counts["prompt_v2"] += 1
        return f"prompt-v2:{name}"

    return mcp_server, call_counts


class TestVersionAwareCaching:
    async def test_call_tool_cache_is_partitioned_by_component_version(
        self, versioned_caching_server: tuple[FastMCP, dict[str, int]]
    ):
        mcp_server, call_counts = versioned_caching_server

        async with Client(mcp_server) as client:
            default = await client.call_tool("calculate", {"x": 5})
            version_one = await client.call_tool("calculate", {"x": 5}, version="1.0")
            version_one_cached = await client.call_tool(
                "calculate", {"x": 5}, version="1.0"
            )

        assert default.data == 10
        assert version_one.data == 6
        assert version_one_cached.data == 6
        assert call_counts["tool_v1"] == 1
        assert call_counts["tool_v2"] == 1

    async def test_read_resource_cache_is_partitioned_by_component_version(
        self, versioned_caching_server: tuple[FastMCP, dict[str, int]]
    ):
        mcp_server, call_counts = versioned_caching_server

        async with Client(mcp_server) as client:
            default = await client.read_resource("data://config")
            version_one = await client.read_resource("data://config", version="1.0")
            version_one_cached = await client.read_resource(
                "data://config", version="1.0"
            )

        assert default[0].text == "resource-v2"
        assert version_one[0].text == "resource-v1"
        assert version_one_cached[0].text == "resource-v1"
        assert call_counts["resource_v1"] == 1
        assert call_counts["resource_v2"] == 1

    async def test_get_prompt_cache_is_partitioned_by_component_version(
        self, versioned_caching_server: tuple[FastMCP, dict[str, int]]
    ):
        mcp_server, call_counts = versioned_caching_server

        async with Client(mcp_server) as client:
            default = await client.get_prompt("greet", {"name": "Ada"})
            version_one = await client.get_prompt(
                "greet", {"name": "Ada"}, version="1.0"
            )
            version_one_cached = await client.get_prompt(
                "greet", {"name": "Ada"}, version="1.0"
            )

        default_content = default.messages[0].content
        version_one_content = version_one.messages[0].content
        version_one_cached_content = version_one_cached.messages[0].content
        assert isinstance(default_content, TextContent)
        assert isinstance(version_one_content, TextContent)
        assert isinstance(version_one_cached_content, TextContent)
        assert default_content.text == "prompt-v2:Ada"
        assert version_one_content.text == "prompt-v1:Ada"
        assert version_one_cached_content.text == "prompt-v1:Ada"
        assert call_counts["prompt_v1"] == 1
        assert call_counts["prompt_v2"] == 1


class TestVersionCacheKeyGeneration:
    def test_call_tool_key_partitions_by_component_version(self):
        default = mcp_types.CallToolRequestParams(name="t", arguments={"a": 1})
        version_one = mcp_types.CallToolRequestParams(
            name="t",
            arguments={"a": 1},
            _meta={"fastmcp": {"version": "1.0"}},
        )

        assert _make_call_tool_cache_key(default) != _make_call_tool_cache_key(
            version_one
        )

    def test_read_resource_key_partitions_by_component_version(self):
        default = mcp_types.ReadResourceRequestParams(uri="file:///tmp/x")
        version_one = mcp_types.ReadResourceRequestParams(
            uri="file:///tmp/x",
            _meta={"fastmcp": {"version": "1.0"}},
        )

        assert _make_read_resource_cache_key(default) != _make_read_resource_cache_key(
            version_one
        )

    def test_get_prompt_key_partitions_by_component_version(self):
        default = mcp_types.GetPromptRequestParams(name="p", arguments={"a": "1"})
        version_one = mcp_types.GetPromptRequestParams(
            name="p",
            arguments={"a": "1"},
            _meta={"fastmcp": {"version": "1.0"}},
        )

        assert _make_get_prompt_cache_key(default) != _make_get_prompt_cache_key(
            version_one
        )
