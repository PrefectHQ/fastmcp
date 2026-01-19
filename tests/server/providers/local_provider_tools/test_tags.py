"""Tests for tool tags."""

from dataclasses import dataclass

import pytest
from pydantic import BaseModel
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        return {k: _normalize_anyof_order(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_normalize_anyof_order(item) for item in schema]
    return schema


class PersonTypedDict(TypedDict):
    name: str
    age: int


class PersonModel(BaseModel):
    name: str
    age: int


@dataclass
class PersonDataclass:
    name: str
    age: int


class TestToolTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.tool(tags={"a", "b"})
        def tool_1() -> int:
            return 1

        @mcp.tool(tags={"b", "c"})
        def tool_2() -> int:
            return 2

        return mcp

    async def test_include_tags_all_tools(self):
        mcp = self.create_server(include_tags={"a", "b"})
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"tool_1", "tool_2"}

    async def test_include_tags_some_tools(self):
        mcp = self.create_server(include_tags={"a", "z"})
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"tool_1"}

    async def test_exclude_tags_all_tools(self):
        mcp = self.create_server(exclude_tags={"a", "b"})
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == set()

    async def test_exclude_tags_some_tools(self):
        mcp = self.create_server(exclude_tags={"a", "z"})
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"tool_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"tool_2"}

    async def test_call_included_tool(self):
        mcp = self.create_server(include_tags={"a"})
        result_1 = await mcp.call_tool("tool_1", {})
        assert result_1.structured_content == {"result": 1}

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("tool_2", {})

    async def test_call_excluded_tool(self):
        mcp = self.create_server(exclude_tags={"a"})
        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("tool_1", {})

        result_2 = await mcp.call_tool("tool_2", {})
        assert result_2.structured_content == {"result": 2}
