from typing import Annotated

import pytest
from mcp_types import ToolAnnotations
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool


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
        """Test that tools without an explicit title derive one from the name.

        Some MCP clients (e.g. ChatGPT) drop tools with no `title` rather
        than falling back to `name` as the spec allows, so FastMCP always
        emits a derived title on the wire instead of relying on that
        fallback.
        """

        def multiply(a: int, b: int) -> int:
            return a * b

        tool = Tool.from_function(multiply)

        assert tool.name == "multiply"
        assert tool.title is None

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.name == "multiply"
        assert mcp_tool.title == "Multiply"

    def test_derived_title_follows_name_override(self):
        """The derived title should reflect a `name` override, not the original name."""

        def multiply(a: int, b: int) -> int:
            return a * b

        tool = Tool.from_function(multiply, name="multiply_tool")

        mcp_tool = tool.to_mcp_tool(name="renamed_tool")
        assert mcp_tool.name == "renamed_tool"
        assert mcp_tool.title == "Renamed Tool"

    @pytest.mark.parametrize(
        "annotations",
        [ToolAnnotations(title="Custom"), {"title": "Custom"}],
        ids=["object", "dict"],
    )
    def test_annotations_override_beats_derived_title(self, annotations):
        """An `annotations` override still outranks the name-derived title."""

        def multiply(a: int, b: int) -> int:
            return a * b

        tool = Tool.from_function(multiply)

        mcp_tool = tool.to_mcp_tool(annotations=annotations)
        assert mcp_tool.title == "Custom"

    def test_tool_title_priority(self):
        """Test that explicit title takes priority over annotations.title."""

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


class TestParameterTitles:
    """Explicit Field(title=...) on a parameter reaches the input schema."""

    def test_explicit_title_is_kept(self):
        def get_bill(
            account: Annotated[
                str, Field(title="Account Number", description="The account.")
            ],
        ) -> str:
            return "ok"

        tool = Tool.from_function(get_bill)

        assert tool.parameters["properties"]["account"] == {
            "description": "The account.",
            "title": "Account Number",
            "type": "string",
        }

    def test_title_from_field_default_is_kept(self):
        def search(limit: int = Field(10, title="Max Results")) -> str:
            return "ok"

        tool = Tool.from_function(search)

        assert tool.parameters["properties"]["limit"]["title"] == "Max Results"

    def test_title_on_optional_parameter_is_kept(self):
        def search(
            limit: Annotated[int | None, Field(title="Max Results")] = None,
        ) -> str:
            return "ok"

        tool = Tool.from_function(search)

        assert tool.parameters["properties"]["limit"]["title"] == "Max Results"

    def test_derived_titles_are_still_pruned(self):
        def get_bill(
            account: str,
            account_id: Annotated[int, Field(description="The id.")],
            title: str = "untitled",
        ) -> str:
            return "ok"

        tool = Tool.from_function(get_bill)

        for schema in tool.parameters["properties"].values():
            assert "title" not in schema
        assert "title" not in tool.parameters

    async def test_explicit_title_reaches_client(self):
        mcp = FastMCP()

        @mcp.tool
        def get_bill(
            account: Annotated[str, Field(title="Account Number")],
        ) -> str:
            return "ok"

        async with Client(mcp) as client:
            [tool] = await client.list_tools()

        assert tool.input_schema["properties"]["account"]["title"] == "Account Number"
