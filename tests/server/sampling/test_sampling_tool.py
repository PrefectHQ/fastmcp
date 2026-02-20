"""Tests for SamplingTool."""

import pytest

from fastmcp.server.sampling import SamplingTool
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool_transform import ArgTransform, TransformedTool


class TestSamplingToolFromFunction:
    """Tests for SamplingTool.from_function()."""

    def test_from_simple_function(self):
        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        tool = SamplingTool.from_function(search)

        assert tool.name == "search"
        assert tool.description == "Search the web."
        assert "query" in tool.parameters.get("properties", {})
        assert tool.fn is search

    def test_from_function_with_overrides(self):
        def search(query: str) -> str:
            return f"Results for: {query}"

        tool = SamplingTool.from_function(
            search,
            name="web_search",
            description="Search the internet",
        )

        assert tool.name == "web_search"
        assert tool.description == "Search the internet"

    def test_from_lambda_requires_name(self):
        with pytest.raises(ValueError, match="must provide a name for lambda"):
            SamplingTool.from_function(lambda x: x)

    def test_from_lambda_with_name(self):
        tool = SamplingTool.from_function(lambda x: x * 2, name="double")

        assert tool.name == "double"

    def test_from_async_function(self):
        async def async_search(query: str) -> str:
            """Async search."""
            return f"Async results for: {query}"

        tool = SamplingTool.from_function(async_search)

        assert tool.name == "async_search"
        assert tool.description == "Async search."

    def test_multiple_parameters(self):
        def search(query: str, limit: int = 10, include_images: bool = False) -> str:
            """Search with options."""
            return f"Results for: {query}"

        tool = SamplingTool.from_function(search)
        props = tool.parameters.get("properties", {})

        assert "query" in props
        assert "limit" in props
        assert "include_images" in props


class TestSamplingToolRun:
    """Tests for SamplingTool.run()."""

    async def test_run_sync_function(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = SamplingTool.from_function(add)
        result = await tool.run({"a": 2, "b": 3})
        assert result == 5

    async def test_run_async_function(self):
        async def async_add(a: int, b: int) -> int:
            """Add two numbers asynchronously."""
            return a + b

        tool = SamplingTool.from_function(async_add)
        result = await tool.run({"a": 2, "b": 3})
        assert result == 5

    async def test_run_with_no_arguments(self):
        def get_value() -> str:
            """Return a fixed value."""
            return "hello"

        tool = SamplingTool.from_function(get_value)
        result = await tool.run()
        assert result == "hello"

    async def test_run_with_none_arguments(self):
        def get_value() -> str:
            """Return a fixed value."""
            return "hello"

        tool = SamplingTool.from_function(get_value)
        result = await tool.run(None)
        assert result == "hello"


class TestSamplingToolSDKConversion:
    """Tests for SamplingTool._to_sdk_tool() internal method."""

    def test_to_sdk_tool(self):
        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        tool = SamplingTool.from_function(search)
        sdk_tool = tool._to_sdk_tool()

        assert sdk_tool.name == "search"
        assert sdk_tool.description == "Search the web."
        assert "query" in sdk_tool.inputSchema.get("properties", {})


class TestSamplingToolFromCallableTool:
    """Tests for SamplingTool.from_callable_tool()."""

    def test_from_function_tool(self):
        """Test converting a FunctionTool to SamplingTool."""

        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        function_tool = FunctionTool.from_function(search)
        sampling_tool = SamplingTool.from_callable_tool(function_tool)

        assert sampling_tool.name == "search"
        assert sampling_tool.description == "Search the web."
        assert "query" in sampling_tool.parameters.get("properties", {})
        # fn is now a wrapper that calls tool.run() for proper result processing
        assert callable(sampling_tool.fn)

    def test_from_function_tool_with_overrides(self):
        """Test converting FunctionTool with name/description overrides."""

        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        function_tool = FunctionTool.from_function(search)
        sampling_tool = SamplingTool.from_callable_tool(
            function_tool,
            name="web_search",
            description="Search the internet",
        )

        assert sampling_tool.name == "web_search"
        assert sampling_tool.description == "Search the internet"

    def test_from_transformed_tool(self):
        """Test converting a TransformedTool to SamplingTool."""

        def original(query: str, limit: int) -> str:
            """Original tool."""
            return f"Results for: {query} (limit: {limit})"

        function_tool = FunctionTool.from_function(original)
        transformed_tool = TransformedTool.from_tool(
            function_tool,
            name="search_transformed",
            transform_args={"query": ArgTransform(name="q")},
        )

        sampling_tool = SamplingTool.from_callable_tool(transformed_tool)

        assert sampling_tool.name == "search_transformed"
        assert sampling_tool.description == "Original tool."
        # The transformed tool should have 'q' instead of 'query'
        assert "q" in sampling_tool.parameters.get("properties", {})
        assert "limit" in sampling_tool.parameters.get("properties", {})

    async def test_from_function_tool_execution(self):
        """Test that converted FunctionTool executes correctly."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        function_tool = FunctionTool.from_function(add)
        sampling_tool = SamplingTool.from_callable_tool(function_tool)

        result = await sampling_tool.run({"a": 2, "b": 3})
        assert result == 5

    async def test_from_transformed_tool_execution(self):
        """Test that converted TransformedTool executes correctly."""

        def multiply(x: int, y: int) -> int:
            """Multiply two numbers."""
            return x * y

        function_tool = FunctionTool.from_function(multiply)
        transformed_tool = TransformedTool.from_tool(
            function_tool,
            transform_args={"x": ArgTransform(name="a"), "y": ArgTransform(name="b")},
        )

        sampling_tool = SamplingTool.from_callable_tool(transformed_tool)

        # Use the transformed parameter names
        result = await sampling_tool.run({"a": 3, "b": 4})
        # Result should be unwrapped from ToolResult
        assert result == 12

    def test_from_invalid_tool_type(self):
        """Test that from_callable_tool rejects non-tool objects."""

        class NotATool:
            pass

        with pytest.raises(
            TypeError,
            match="Expected FunctionTool or TransformedTool",
        ):
            SamplingTool.from_callable_tool(NotATool())  # type: ignore[arg-type]

    def test_from_plain_function_fails(self):
        """Test that plain functions are rejected by from_callable_tool."""

        def my_function():
            pass

        with pytest.raises(TypeError, match="Expected FunctionTool or TransformedTool"):
            SamplingTool.from_callable_tool(my_function)  # type: ignore[arg-type]

    async def test_from_function_tool_with_output_schema(self):
        """Test that FunctionTool with output_schema is handled correctly."""

        def search(query: str) -> dict:
            """Search for something."""
            return {"results": ["item1", "item2"], "count": 2}

        # Create FunctionTool with x-fastmcp-wrap-result
        function_tool = FunctionTool.from_function(
            search,
            output_schema={
                "type": "object",
                "properties": {
                    "results": {"type": "array"},
                    "count": {"type": "integer"},
                },
                "x-fastmcp-wrap-result": True,
            },
        )

        sampling_tool = SamplingTool.from_callable_tool(function_tool)

        # Run the tool - should unwrap the {"result": {...}} wrapper
        result = await sampling_tool.run({"query": "test"})

        # Should get the unwrapped dict, not ToolResult
        assert isinstance(result, dict)
        assert result == {"results": ["item1", "item2"], "count": 2}

    async def test_from_function_tool_without_wrap_result(self):
        """Test that FunctionTool without x-fastmcp-wrap-result is handled correctly."""

        def get_data() -> dict:
            """Get some data."""
            return {"status": "ok", "value": 42}

        # Create FunctionTool with output_schema but no wrap-result flag
        function_tool = FunctionTool.from_function(
            get_data,
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "value": {"type": "integer"},
                },
            },
        )

        sampling_tool = SamplingTool.from_callable_tool(function_tool)

        # Run the tool - should return structured_content directly
        result = await sampling_tool.run({})

        assert isinstance(result, dict)
        assert result == {"status": "ok", "value": 42}
