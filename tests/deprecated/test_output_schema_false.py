"""Test deprecated output_schema=False behavior (deprecated in 2.11.4)."""

import warnings

import pytest

from fastmcp import FastMCP
from fastmcp.tools import Tool


class TestDeprecatedOutputSchemaFalse:
    """Test that output_schema=False is deprecated but still works."""

    async def test_tool_decorator_output_schema_false_deprecated(self):
        """Test that @mcp.tool(output_schema=False) shows deprecation warning."""
        mcp = FastMCP()

        with pytest.warns(
            DeprecationWarning, match="output_schema=False is deprecated"
        ):

            @mcp.tool(output_schema=False)  # type: ignore[arg-type]
            def simple_tool() -> int:
                """A simple tool."""
                return 42

        # Verify the tool was created with None as output_schema
        tool = mcp._tool_manager._tools["simple_tool"]
        assert tool.output_schema is None

    async def test_tool_from_function_output_schema_false_deprecated(self):
        """Test that Tool.from_function(output_schema=False) shows deprecation warning."""

        def my_function() -> str:
            """A simple function."""
            return "hello"

        with pytest.warns(
            DeprecationWarning, match="output_schema=False is deprecated"
        ):
            tool = Tool.from_function(my_function, output_schema=False)  # type: ignore[arg-type]

        # Verify the tool was created with None as output_schema
        assert tool.output_schema is None

    async def test_tool_from_tool_output_schema_false_deprecated(self):
        """Test that Tool.from_tool(output_schema=False) shows deprecation warning."""

        # Create a parent tool
        def parent_function() -> dict[str, str]:
            """A parent function."""
            return {"status": "ok"}

        parent_tool = Tool.from_function(parent_function)

        with pytest.warns(
            DeprecationWarning, match="output_schema=False is deprecated"
        ):
            transformed_tool = Tool.from_tool(parent_tool, output_schema=False)  # type: ignore[arg-type]

        # Verify the tool was created with None as output_schema
        assert transformed_tool.output_schema is None

    async def test_output_schema_false_functionality_preserved(self):
        """Test that output_schema=False still works functionally like output_schema=None."""
        mcp = FastMCP()

        # Create two tools - one with False, one with None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            @mcp.tool(output_schema=False)  # type: ignore[arg-type]
            def tool_with_false() -> dict[str, str]:
                """Tool with output_schema=False."""
                return {"result": "false"}

            @mcp.tool(output_schema=None)
            def tool_with_none() -> dict[str, str]:
                """Tool with output_schema=None."""
                return {"result": "none"}

        # Both should have None as output_schema
        assert mcp._tool_manager._tools["tool_with_false"].output_schema is None
        assert mcp._tool_manager._tools["tool_with_none"].output_schema is None

        # Both should work the same way
        result_false = await mcp._tool_manager._tools["tool_with_false"].run({})
        result_none = await mcp._tool_manager._tools["tool_with_none"].run({})

        # Both should return structured content for dict-like objects
        assert result_false.structured_content == {"result": "false"}
        assert result_none.structured_content == {"result": "none"}

    async def test_output_schema_false_with_scalar_return(self):
        """Test that output_schema=False works with scalar returns (no structured content)."""
        mcp = FastMCP()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            @mcp.tool(output_schema=False)  # type: ignore[arg-type]
            def scalar_tool() -> int:
                """Tool returning a scalar."""
                return 42

        tool = mcp._tool_manager._tools["scalar_tool"]
        assert tool.output_schema is None

        result = await tool.run({})
        # Scalar values don't produce structured content
        assert result.structured_content is None
        assert len(result.content) == 1
        assert result.content[0].text == "42"  # type: ignore[attr-defined]

    async def test_transform_with_output_schema_false(self):
        """Test that transformation with output_schema=False still works."""

        # Create a parent tool
        def parent_function(x: int) -> dict[str, int]:
            """A parent function."""
            return {"value": x * 2}

        parent_tool = Tool.from_function(parent_function)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            # Transform with output_schema=False
            transformed = Tool.from_tool(
                parent_tool,
                name="doubled",
                output_schema=False,  # type: ignore[arg-type]
            )

        assert transformed.output_schema is None

        # Tool should still work
        result = await transformed.run({"x": 5})
        assert result.structured_content == {"value": 10}
