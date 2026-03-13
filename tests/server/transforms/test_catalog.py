"""Tests for CatalogTransform base class."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.server.transforms.version_filter import VersionFilter
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_versioned_tool(name: str, version: str) -> Tool:
    """Create a tool with a specific version for testing."""

    async def _fn() -> str:
        return f"{name}@{version}"

    return Tool.from_function(fn=_fn, name=name, version=version)


class CatalogReader(CatalogTransform):
    """Minimal CatalogTransform that exposes get_tool_catalog via a tool.

    After calling the ``read_catalog`` tool, the full catalog is available
    on ``self.last_catalog`` for assertions beyond tool names.
    """

    def __init__(self) -> None:
        super().__init__()
        self.last_catalog: Sequence[Tool] = []

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._make_reader_tool()]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "read_catalog":
            return self._make_reader_tool()
        return await call_next(name, version=version)

    def _make_reader_tool(self) -> Tool:
        transform = self

        async def read_catalog(ctx: Context = None) -> list[str]:  # type: ignore[assignment]
            """Return names of tools visible in the catalog."""
            transform.last_catalog = await transform.get_tool_catalog(ctx)
            return [t.name for t in transform.last_catalog]

        return Tool.from_function(fn=read_catalog, name="read_catalog")


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


class TestCatalogDeduplication:
    """get_tool_catalog() deduplicates versioned tools, keeping only the highest."""

    async def test_returns_highest_version_only(self):
        mcp = FastMCP("test")
        mcp.add_tool(_make_versioned_tool("greet", "1"))
        mcp.add_tool(_make_versioned_tool("greet", "2"))
        mcp.add_tool(_make_versioned_tool("greet", "3"))

        reader = CatalogReader()
        mcp.add_transform(reader)

        result = await mcp.call_tool("read_catalog", {})
        names = _extract_result(result)
        assert names == ["greet"]

    async def test_version_metadata_injected(self):
        """Highest-version tool has meta.fastmcp.versions listing all available."""
        mcp = FastMCP("test")
        mcp.add_tool(_make_versioned_tool("greet", "1"))
        mcp.add_tool(_make_versioned_tool("greet", "3"))

        reader = CatalogReader()
        mcp.add_transform(reader)

        await mcp.call_tool("read_catalog", {})
        assert len(reader.last_catalog) == 1
        tool = reader.last_catalog[0]
        assert tool.name == "greet"
        assert tool.version == "3"
        assert tool.meta is not None
        versions = tool.meta["fastmcp"]["versions"]
        assert versions == ["3", "1"]

    async def test_mixed_versioned_and_unversioned(self):
        mcp = FastMCP("test")

        @mcp.tool
        def standalone() -> str:
            return "hi"

        mcp.add_tool(_make_versioned_tool("greet", "1"))
        mcp.add_tool(_make_versioned_tool("greet", "2"))

        reader = CatalogReader()
        mcp.add_transform(reader)

        result = await mcp.call_tool("read_catalog", {})
        names = sorted(_extract_result(result))
        assert names == ["greet", "standalone"]

    async def test_version_filter_applied_before_catalog(self):
        """A VersionFilter added before the CatalogTransform restricts what the catalog sees."""
        mcp = FastMCP("test")
        mcp.add_tool(_make_versioned_tool("greet", "1"))
        mcp.add_tool(_make_versioned_tool("greet", "2"))
        mcp.add_tool(_make_versioned_tool("greet", "3"))

        # Filter keeps only versions < 3 — so v1 and v2 survive, v3 is excluded.
        mcp.add_transform(VersionFilter(version_lt="3"))

        reader = CatalogReader()
        mcp.add_transform(reader)

        await mcp.call_tool("read_catalog", {})
        assert len(reader.last_catalog) == 1
        tool = reader.last_catalog[0]
        assert tool.name == "greet"
        assert tool.version == "2"


class TestCatalogVisibility:
    """get_tool_catalog() respects visibility (disabled tools are excluded)."""

    async def test_disabled_tool_excluded(self):
        mcp = FastMCP("test")

        @mcp.tool
        def public() -> str:
            return "visible"

        @mcp.tool
        def secret() -> str:
            return "hidden"

        mcp.disable(names={"secret"}, components={"tool"})
        reader = CatalogReader()
        mcp.add_transform(reader)

        result = await mcp.call_tool("read_catalog", {})
        names = _extract_result(result)
        assert names == ["public"]


class TestCatalogAuth:
    """get_tool_catalog() respects tool-level auth filtering."""

    async def test_auth_rejected_tool_excluded(self):
        mcp = FastMCP("test")

        @mcp.tool
        def public() -> str:
            return "visible"

        @mcp.tool(auth=lambda _ctx: False)
        def protected() -> str:
            return "hidden"

        reader = CatalogReader()
        mcp.add_transform(reader)

        result = await mcp.call_tool("read_catalog", {})
        names = _extract_result(result)
        assert names == ["public"]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _extract_result(result: object) -> list[str]:
    """Extract the list of names from a call_tool ToolResult."""
    for c in result.content:  # type: ignore[union-attr]
        if isinstance(c, TextContent):
            return ast.literal_eval(c.text)
    raise AssertionError("No text content found")
