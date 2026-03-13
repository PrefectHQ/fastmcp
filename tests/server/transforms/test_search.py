"""Tests for search transforms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
from mcp.types import TextContent

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.transforms import Visibility
from fastmcp.server.transforms.search.bm25 import (
    BM25SearchTransform,
    _BM25Index,
    _catalog_hash,
)
from fastmcp.server.transforms.search.regex import RegexSearchTransform
from fastmcp.tools.tool import Tool, ToolResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tool_result(result: ToolResult) -> list[dict[str, Any]]:
    """Extract tool list from a ToolResult's structured content."""
    assert result.structured_content is not None
    return result.structured_content["result"]


def _make_server_with_tools() -> FastMCP:
    mcp = FastMCP("test")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @mcp.tool
    def multiply(x: float, y: float) -> float:
        """Multiply two numbers."""
        return x * y

    @mcp.tool
    def search_database(query: str, limit: int = 10) -> str:
        """Search the database for records matching the query."""
        return f"results for {query}"

    @mcp.tool
    def delete_record(record_id: str) -> str:
        """Delete a record from the database by its ID."""
        return f"deleted {record_id}"

    @mcp.tool
    def send_email(to: str, subject: str, body: str) -> str:
        """Send an email to the given recipient."""
        return "sent"

    return mcp


# ---------------------------------------------------------------------------
# Shared behavior tests (parameterized across both transforms)
# ---------------------------------------------------------------------------


class TestBaseTransformBehavior:
    """Tests for behavior shared by all search transforms."""

    async def test_list_tools_hides_tools_regex(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {"search_tools", "call_tool"}

    async def test_list_tools_hides_tools_bm25(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {"search_tools", "call_tool"}

    async def test_always_visible_pins_tools(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform(always_visible=["add"]))
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "add" in names
        assert "search_tools" in names
        assert "call_tool" in names
        assert "multiply" not in names

    async def test_get_tool_returns_synthetic(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        search = await mcp.get_tool("search_tools")
        assert search is not None
        assert search.name == "search_tools"
        call = await mcp.get_tool("call_tool")
        assert call is not None
        assert call.name == "call_tool"

    async def test_get_tool_passes_through_hidden(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        tool = await mcp.get_tool("add")
        assert tool is not None
        assert tool.name == "add"

    async def test_call_tool_proxy_executes(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        # Need to call list_tools to populate catalog
        await mcp.list_tools()
        result = await mcp.call_tool(
            "call_tool", {"name": "add", "arguments": {"a": 2, "b": 3}}
        )
        assert any("5" in c.text for c in result.content if isinstance(c, TextContent))

    async def test_custom_tool_names(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(
            RegexSearchTransform(
                search_tool_name="find_tools",
                call_tool_name="run_tool",
            )
        )
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {"find_tools", "run_tool"}
        assert await mcp.get_tool("find_tools") is not None
        assert await mcp.get_tool("run_tool") is not None

    async def test_search_respects_visibility_filtering(self):
        """Tools disabled via Visibility transform should not appear in search."""
        mcp = _make_server_with_tools()
        mcp.add_transform(Visibility(False, names={"delete_record"}))
        mcp.add_transform(RegexSearchTransform())

        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "delete_record" not in names

        result = await mcp.call_tool("search_tools", {"pattern": "delete"})
        found = _parse_tool_result(result)
        assert not any(t["name"] == "delete_record" for t in found)

    async def test_search_respects_auth_middleware(self):
        """Tools filtered by auth middleware should not appear in search."""

        class BlockAdminTools(Middleware):
            async def on_list_tools(
                self,
                context: MiddlewareContext[mcp_types.ListToolsRequest],
                call_next: CallNext[mcp_types.ListToolsRequest, Sequence[Tool]],
            ) -> Sequence[Tool]:
                tools = await call_next(context)
                return [t for t in tools if t.name != "delete_record"]

        mcp = _make_server_with_tools()
        mcp.add_middleware(BlockAdminTools())
        mcp.add_transform(RegexSearchTransform())

        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "delete_record" not in names
            assert "search_tools" in names

            result = await client.call_tool("search_tools", {"pattern": "delete"})
            found = _parse_tool_result(result)
            assert not any(t["name"] == "delete_record" for t in found)

    async def test_search_respects_session_visibility(self):
        """Tools disabled via session visibility should not appear in search."""
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())

        @mcp.tool
        async def disable_delete(ctx: Context) -> str:
            """Helper tool to disable delete_record for this session."""
            await ctx.disable_components(names={"delete_record"})
            return "disabled"

        async with Client(mcp) as client:
            # Before disabling, search should find delete_record
            result = await client.call_tool("search_tools", {"pattern": "delete"})
            found = _parse_tool_result(result)
            assert any(t["name"] == "delete_record" for t in found)

            # Disable via session visibility
            await client.call_tool("disable_delete", {})

            # After disabling, search should NOT find it
            result = await client.call_tool("search_tools", {"pattern": "delete"})
            found = _parse_tool_result(result)
            assert not any(t["name"] == "delete_record" for t in found)


# ---------------------------------------------------------------------------
# Regex-specific tests
# ---------------------------------------------------------------------------


class TestRegexSearch:
    async def test_search_by_name(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "add"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "add" for t in tools)

    async def test_search_by_description(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "email"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "send_email" for t in tools)

    async def test_search_by_param_name(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "record_id"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "delete_record" for t in tools)

    async def test_search_by_param_description(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "recipient"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "send_email" for t in tools)

    async def test_search_or_pattern(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform(max_results=10))
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "add|multiply"})
        tools = _parse_tool_result(result)
        names = {t["name"] for t in tools}
        assert "add" in names
        assert "multiply" in names

    async def test_search_case_insensitive(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "ADD"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "add" for t in tools)

    async def test_search_invalid_pattern(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "[invalid"})
        tools = _parse_tool_result(result)
        assert tools == []

    async def test_search_max_results(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform(max_results=2))
        await mcp.list_tools()
        # Match everything
        result = await mcp.call_tool("search_tools", {"pattern": ".*"})
        tools = _parse_tool_result(result)
        assert len(tools) == 2

    async def test_search_no_matches(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "zzz_nonexistent"})
        tools = _parse_tool_result(result)
        assert tools == []

    async def test_search_returns_full_schema(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"pattern": "add"})
        tools = _parse_tool_result(result)
        add_tool = next(t for t in tools if t["name"] == "add")
        assert "inputSchema" in add_tool
        assert "properties" in add_tool["inputSchema"]


# ---------------------------------------------------------------------------
# BM25-specific tests
# ---------------------------------------------------------------------------


class TestBM25Search:
    async def test_search_relevance(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"query": "database"})
        tools = _parse_tool_result(result)
        # Database tools should rank highest
        assert len(tools) > 0
        names = {t["name"] for t in tools}
        assert "search_database" in names

    async def test_search_database_tools(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool(
            "search_tools", {"query": "delete records from database"}
        )
        tools = _parse_tool_result(result)
        assert len(tools) > 0
        # delete_record should be highly relevant
        assert tools[0]["name"] == "delete_record"

    async def test_search_max_results(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform(max_results=2))
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"query": "number"})
        tools = _parse_tool_result(result)
        assert len(tools) <= 2

    async def test_search_no_matches(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"query": "zzz_nonexistent_xyz"})
        tools = _parse_tool_result(result)
        assert tools == []

    async def test_index_rebuilds_on_catalog_change(self):
        """When a new tool is added, the next list_tools + search sees it."""
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()

        # Search before adding tool
        result = await mcp.call_tool("search_tools", {"query": "weather forecast"})
        tools = _parse_tool_result(result)
        assert not any(t["name"] == "get_weather" for t in tools)

        # Add a new tool
        @mcp.tool
        def get_weather(city: str) -> str:
            """Get the weather forecast for a city."""
            return f"sunny in {city}"

        # Must call list_tools to refresh the catalog cache
        await mcp.list_tools()

        result = await mcp.call_tool("search_tools", {"query": "weather forecast"})
        tools = _parse_tool_result(result)
        assert any(t["name"] == "get_weather" for t in tools)

    async def test_search_empty_query(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"query": ""})
        tools = _parse_tool_result(result)
        assert tools == []

    async def test_search_returns_full_schema(self):
        mcp = _make_server_with_tools()
        mcp.add_transform(BM25SearchTransform())
        await mcp.list_tools()
        result = await mcp.call_tool("search_tools", {"query": "add numbers"})
        tools = _parse_tool_result(result)
        add_tool = next(t for t in tools if t["name"] == "add")
        assert "inputSchema" in add_tool
        assert "properties" in add_tool["inputSchema"]


# ---------------------------------------------------------------------------
# BM25 index unit tests
# ---------------------------------------------------------------------------


class TestBM25Index:
    def test_basic_ranking(self):
        index = _BM25Index()
        index.build(
            [
                "search database query records",
                "add two numbers together",
                "send email recipient subject",
            ]
        )
        results = index.query("database records", 3)
        assert results[0] == 0  # Database doc should rank first

    def test_empty_corpus(self):
        index = _BM25Index()
        index.build([])
        assert index.query("anything", 5) == []

    def test_no_matching_tokens(self):
        index = _BM25Index()
        index.build(["alpha beta gamma"])
        assert index.query("zzz", 5) == []


# ---------------------------------------------------------------------------
# call_tool self-reference guard
# ---------------------------------------------------------------------------


class TestCallToolGuard:
    async def test_call_tool_proxy_rejects_itself(self):
        """Calling call_tool(name='call_tool') must not recurse infinitely."""
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())

        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "call_tool", {"name": "call_tool", "arguments": {}}
                )

    async def test_call_tool_proxy_rejects_search_tool(self):
        """Calling call_tool(name='search_tools') must be rejected."""
        mcp = _make_server_with_tools()
        mcp.add_transform(RegexSearchTransform())

        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "call_tool",
                    {"name": "search_tools", "arguments": {"pattern": "add"}},
                )

    async def test_call_tool_proxy_rejects_custom_names(self):
        """Guard works when synthetic tools have custom names."""
        mcp = _make_server_with_tools()
        mcp.add_transform(
            RegexSearchTransform(
                search_tool_name="find_tools", call_tool_name="run_tool"
            )
        )

        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "run_tool", {"name": "run_tool", "arguments": {}}
                )
            with pytest.raises(Exception):
                await client.call_tool(
                    "run_tool", {"name": "find_tools", "arguments": {"pattern": "add"}}
                )


# ---------------------------------------------------------------------------
# catalog hash staleness
# ---------------------------------------------------------------------------


class TestCatalogHash:
    def test_hash_differs_for_same_name_different_description(self):
        """Hash must change when a tool's description changes, not just its name."""
        tool_a = MagicMock()
        tool_a.name = "search"
        tool_a.description = "find records in the database"
        tool_a.parameters = {}

        tool_b = MagicMock()
        tool_b.name = "search"
        tool_b.description = "send an email to a recipient"
        tool_b.parameters = {}

        assert _catalog_hash([tool_a]) != _catalog_hash([tool_b])
