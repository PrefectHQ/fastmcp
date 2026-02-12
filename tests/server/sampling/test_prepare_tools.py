"""Tests for prepare_tools helper function."""

import pytest

from fastmcp.server.sampling.run import prepare_tools
from fastmcp.server.sampling.sampling_tool import SamplingTool
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool_transform import ArgTransform, TransformedTool


class TestPrepareTools:
    """Tests for prepare_tools()."""

    def test_prepare_tools_with_none(self):
        """Test that None returns None."""
        result = prepare_tools(None)
        assert result is None

    def test_prepare_tools_with_sampling_tool(self):
        """Test that SamplingTool instances pass through."""

        def search(query: str) -> str:
            return f"Results: {query}"

        sampling_tool = SamplingTool.from_function(search)
        result = prepare_tools([sampling_tool])

        assert result is not None
        assert len(result) == 1
        assert result[0] is sampling_tool

    def test_prepare_tools_with_function(self):
        """Test that plain functions are converted."""

        def search(query: str) -> str:
            """Search function."""
            return f"Results: {query}"

        result = prepare_tools([search])

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], SamplingTool)
        assert result[0].name == "search"

    def test_prepare_tools_with_function_tool(self):
        """Test that FunctionTool instances are converted."""

        def search(query: str) -> str:
            """Search the web."""
            return f"Results: {query}"

        function_tool = FunctionTool.from_function(search)
        result = prepare_tools([function_tool])

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], SamplingTool)
        assert result[0].name == "search"
        assert result[0].description == "Search the web."

    def test_prepare_tools_with_transformed_tool(self):
        """Test that TransformedTool instances are converted."""

        def original(query: str) -> str:
            """Original tool."""
            return f"Results: {query}"

        function_tool = FunctionTool.from_function(original)
        transformed_tool = TransformedTool.from_tool(
            function_tool,
            name="search_v2",
            transform_args={"query": ArgTransform(name="q")},
        )

        result = prepare_tools([transformed_tool])

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], SamplingTool)
        assert result[0].name == "search_v2"
        assert "q" in result[0].parameters.get("properties", {})

    def test_prepare_tools_with_mixed_types(self):
        """Test that mixed tool types are all converted."""

        def plain_fn(x: int) -> int:
            return x * 2

        def fn_for_tool(y: int) -> int:
            return y * 3

        function_tool = FunctionTool.from_function(fn_for_tool)
        sampling_tool = SamplingTool.from_function(lambda z: z * 4, name="lambda_tool")

        result = prepare_tools([plain_fn, function_tool, sampling_tool])

        assert result is not None
        assert len(result) == 3
        assert all(isinstance(t, SamplingTool) for t in result)

    def test_prepare_tools_with_invalid_type(self):
        """Test that invalid types raise TypeError."""

        with pytest.raises(TypeError, match="Expected SamplingTool, FunctionTool"):
            prepare_tools(["not a tool"])  # type: ignore[arg-type]

    def test_prepare_tools_empty_list(self):
        """Test that empty list returns None."""
        result = prepare_tools([])
        assert result is None
