"""Tests for auto-pagination when servers return opaque cursor strings.

Verifies that ``Client.list_tools()``, ``Client.list_resources()``, and
``Client.list_prompts()`` correctly paginate when the server returns a non-null
continuation cursor (including empty strings), per the MCP pagination contract
which treats cursors as opaque tokens and ends pagination only when the cursor
is absent or ``None``.
"""

import pytest
import mcp_types
from mcp_types import ListToolsResult, Tool, ListResourcesResult, Resource, ListPromptsResult, Prompt

from fastmcp import Client, FastMCP


class _EmptyCursorToolsServer(FastMCP):
    """Server that returns an empty string ``next_cursor`` on page 1."""

    async def _on_list_tools(self, ctx, params):
        first_page = params is None or params.cursor is None
        if not first_page:
            assert params.cursor == ""
        return ListToolsResult(
            tools=[Tool(name="first" if first_page else "second", input_schema={"type": "object"})],
            next_cursor="" if first_page else None,
        )


class _EmptyCursorResourcesServer(FastMCP):
    """Server that returns an empty string ``next_cursor`` on page 1."""

    async def _on_list_resources(self, ctx, params):
        first_page = params is None or params.cursor is None
        if not first_page:
            assert params.cursor == ""
        return ListResourcesResult(
            resources=[Resource(uri="first" if first_page else "second", name="r")],
            next_cursor="" if first_page else None,
        )


class _EmptyCursorPromptsServer(FastMCP):
    """Server that returns an empty string ``next_cursor`` on page 1."""

    async def _on_list_prompts(self, ctx, params):
        first_page = params is None or params.cursor is None
        if not first_page:
            assert params.cursor == ""
        return ListPromptsResult(
            prompts=[Prompt(name="first" if first_page else "second")],
            next_cursor="" if first_page else None,
        )


class TestEmptyCursorPagination:
    """Auto-pagination must not stop when next_cursor is an empty string."""

    async def test_list_tools_empty_cursor(self):
        """list_tools must continue past an empty-string cursor."""
        async with Client(_EmptyCursorToolsServer("tools")) as client:
            tools = await client.list_tools(max_pages=5)
            names = [t.name for t in tools]
            assert names == ["first", "second"]

    async def test_list_resources_empty_cursor(self):
        """list_resources must continue past an empty-string cursor."""
        async with Client(_EmptyCursorResourcesServer("resources")) as client:
            resources = await client.list_resources(max_pages=5)
            uris = [r.uri for r in resources]
            assert uris == ["first", "second"]

    async def test_list_prompts_empty_cursor(self):
        """list_prompts must continue past an empty-string cursor."""
        async with Client(_EmptyCursorPromptsServer("prompts")) as client:
            prompts = await client.list_prompts(max_pages=5)
            names = [p.name for p in prompts]
            assert names == ["first", "second"]


class _NonEmptyCursorServer(FastMCP):
    """Server that uses a non-empty opaque cursor."""

    async def _on_list_tools(self, ctx, params):
        cursor = params.cursor if params and params.cursor else None
        if cursor is None:
            return ListToolsResult(
                tools=[Tool(name="page1", input_schema={"type": "object"})],
                next_cursor="opaque-token-abc",
            )
        elif cursor == "opaque-token-abc":
            return ListToolsResult(
                tools=[Tool(name="page2", input_schema={"type": "object"})],
                next_cursor=None,
            )
        return ListToolsResult(tools=[], next_cursor=None)


class TestNonEmptyCursorPagination:
    """Standard non-empty cursor pagination still works."""

    async def test_list_tools_non_empty_cursor(self):
        async with Client(_NonEmptyCursorServer("opaque")) as client:
            tools = await client.list_tools(max_pages=5)
            names = [t.name for t in tools]
            assert names == ["page1", "page2"]
