"""Tests for deprecated add_tool_transformation API."""

import warnings

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.tools.tool_transform import ToolTransformConfig


class TestAddToolTransformationDeprecated:
    """Test that add_tool_transformation still works but emits deprecation warning."""

    async def test_add_tool_transformation_emits_warning(self):
        """add_tool_transformation should emit deprecation warning."""
        mcp = FastMCP("test")

        @mcp.tool
        def my_tool() -> str:
            return "hello"

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mcp.add_tool_transformation(
                "my_tool", ToolTransformConfig(name="renamed_tool")
            )

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "add_tool_transformation is deprecated" in str(w[0].message)

    async def test_add_tool_transformation_still_works(self):
        """add_tool_transformation should still apply the transformation."""
        mcp = FastMCP("test")

        @mcp.tool
        def verbose_tool_name() -> str:
            return "result"

        # Suppress warning for this test - we just want to verify it works
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            mcp.add_tool_transformation(
                "verbose_tool_name", ToolTransformConfig(name="short")
            )

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            # Original name should be gone, renamed version should exist
            assert "verbose_tool_name" not in tool_names
            assert "short" in tool_names

            # Should be callable by new name
            result = await client.call_tool("short", {})
            assert result.content[0].text == "result"

    async def test_remove_tool_transformation_emits_warning(self):
        """remove_tool_transformation should emit deprecation warning."""
        mcp = FastMCP("test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mcp.remove_tool_transformation("any_tool")

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "remove_tool_transformation is deprecated" in str(w[0].message)
            assert "no effect" in str(w[0].message)

    async def test_tool_transformations_constructor_emits_warning(self):
        """tool_transformations constructor param should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            FastMCP(
                "test",
                tool_transformations={"my_tool": ToolTransformConfig(name="renamed")},
            )

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "tool_transformations parameter is deprecated" in str(w[0].message)

    async def test_tool_transformations_constructor_still_works(self):
        """tool_transformations constructor param should still apply transforms."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            mcp = FastMCP(
                "test",
                tool_transformations={
                    "my_tool": ToolTransformConfig(name="renamed_tool")
                },
            )

        @mcp.tool
        def my_tool() -> str:
            return "result"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            assert "my_tool" not in tool_names
            assert "renamed_tool" in tool_names
