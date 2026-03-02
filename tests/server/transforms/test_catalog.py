"""Tests for CatalogTransform base class."""

from __future__ import annotations

from collections.abc import Sequence

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec


class ReplacingTransform(CatalogTransform):
    """Minimal subclass that replaces tools with a synthetic tool.

    Uses ``get_tool_catalog()`` to read the real catalog inside the
    synthetic tool's handler, verifying that the bypass mechanism works.
    """

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._make_synthetic_tool()]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "count_tools":
            return self._make_synthetic_tool()
        return await call_next(name, version=version)

    def _make_synthetic_tool(self) -> Tool:
        transform = self

        async def count_tools(ctx: Context = None) -> int:  # type: ignore[assignment]
            """Return the number of real tools in the catalog."""
            catalog = await transform.get_tool_catalog(ctx)
            return len(catalog)

        return Tool.from_function(fn=count_tools, name="count_tools")


class TestCatalogTransformBypass:
    async def test_list_tools_replaced_by_subclass(self):
        mcp = FastMCP("test")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        @mcp.tool
        def multiply(x: float, y: float) -> float:
            return x * y

        mcp.add_transform(ReplacingTransform())
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {"count_tools"}

    async def test_get_tool_catalog_returns_real_tools(self):
        mcp = FastMCP("test")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        @mcp.tool
        def multiply(x: float, y: float) -> float:
            return x * y

        mcp.add_transform(ReplacingTransform())
        result = await mcp.call_tool("count_tools", {})
        assert any("2" in c.text for c in result.content if isinstance(c, TextContent))

    async def test_multiple_instances_have_independent_bypass(self):
        """Each CatalogTransform instance has its own bypass ContextVar."""
        t1 = ReplacingTransform()
        t2 = ReplacingTransform()
        assert t1._instance_id != t2._instance_id
        assert t1._bypass is not t2._bypass
