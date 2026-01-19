from fastmcp.tools.tool import Tool


class TestToolTitle:
    """Tests for tool title functionality."""

    def test_tool_with_title(self):
        """Test that tools can have titles and they appear in MCP conversion."""

        def calculate(x: int, y: int) -> int:
            """Calculate the sum of two numbers."""
            return x + y

        tool = Tool.from_function(
            calculate,
            name="calc",
            title="Advanced Calculator Tool",
            description="Custom description",
        )

        assert tool.name == "calc"
        assert tool.title == "Advanced Calculator Tool"
        assert tool.description == "Custom description"

        # Test MCP conversion includes title
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.name == "calc"
        assert (
            hasattr(mcp_tool, "title") and mcp_tool.title == "Advanced Calculator Tool"
        )

    def test_tool_without_title(self):
        """Test that tools without titles use name as display name."""

        def multiply(a: int, b: int) -> int:
            return a * b

        tool = Tool.from_function(multiply)

        assert tool.name == "multiply"
        assert tool.title is None

        # Test MCP conversion doesn't include title when None
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.name == "multiply"
        assert not hasattr(mcp_tool, "title") or mcp_tool.title is None

    def test_tool_title_priority(self):
        """Test that explicit title takes priority over annotations.title."""
        from mcp.types import ToolAnnotations

        def divide(x: int, y: int) -> float:
            """Divide two numbers."""
            return x / y

        # Test with both explicit title and annotations.title
        annotations = ToolAnnotations(title="Annotation Title")
        tool = Tool.from_function(
            divide,
            name="div",
            title="Explicit Title",
            annotations=annotations,
        )

        assert tool.title == "Explicit Title"
        assert tool.annotations is not None
        assert tool.annotations.title == "Annotation Title"

        # Explicit title should take priority
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.title == "Explicit Title"

    def test_tool_annotations_title_fallback(self):
        """Test that annotations.title is used when no explicit title is provided."""
        from mcp.types import ToolAnnotations

        def modulo(x: int, y: int) -> int:
            """Get modulo of two numbers."""
            return x % y

        # Test with only annotations.title (no explicit title)
        annotations = ToolAnnotations(title="Annotation Title")
        tool = Tool.from_function(
            modulo,
            name="mod",
            annotations=annotations,
        )

        assert tool.title is None
        assert tool.annotations is not None
        assert tool.annotations.title == "Annotation Title"

        # Should fall back to annotations.title
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.title == "Annotation Title"
