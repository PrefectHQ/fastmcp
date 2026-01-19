"""Tests for deprecated tool serializer functionality.

These tests verify that serializer parameters still work but are deprecated.
All serializer-related tests should be moved here.
"""

import warnings

import pytest
from inline_snapshot import snapshot
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.contrib.mcp_mixin import mcp_tool
from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import Tool, _convert_to_content
from fastmcp.tools.tool_transform import TransformedTool
from fastmcp.utilities.tests import temporary_settings

# Reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestToolSerializerDeprecated:
    """Tests for deprecated serializer functionality."""

    async def test_tool_serializer(self):
        """Test that a tool's serializer is used to serialize the result."""

        def custom_serializer(data) -> str:
            return f"Custom serializer: {data}"

        def process_list(items: list[int]) -> int:
            return sum(items)

        tool = Tool.from_function(process_list, serializer=custom_serializer)

        result = await tool.run(arguments={"items": [1, 2, 3, 4, 5]})
        # Custom serializer affects unstructured content
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Custom serializer: 15"
        # Structured output should have the raw value
        assert result.structured_content == {"result": 15}

    def test_custom_serializer(self):
        """Test that a custom serializer is used for non-MCP types."""

        def custom_serializer(data):
            return f"Serialized: {data}"

        result = _convert_to_content({"a": 1}, serializer=custom_serializer)

        assert result == snapshot(
            [TextContent(type="text", text="Serialized: {'a': 1}")]
        )

    def test_custom_serializer_error_fallback(self, caplog):
        """Test that if a custom serializer fails, it falls back to the default."""

        def custom_serializer_that_fails(data):
            raise ValueError("Serialization failed")

        result = _convert_to_content({"a": 1}, serializer=custom_serializer_that_fails)

        assert isinstance(result, list)
        assert result == snapshot([TextContent(type="text", text='{"a":1}')])

        assert "Error serializing tool result" in caplog.text


class TestSerializerDeprecationWarnings:
    """Tests that deprecation warnings are raised when serializer is used."""

    def test_tool_from_function_serializer_warning(self):
        """Test that Tool.from_function warns when serializer is provided."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        def my_tool(x: int) -> int:
            return x * 2

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):
                Tool.from_function(my_tool, serializer=custom_serializer)

    def test_tool_from_function_serializer_no_warning_when_disabled(self):
        """Test that no warning is raised when deprecation_warnings is False."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        def my_tool(x: int) -> int:
            return x * 2

        with temporary_settings(deprecation_warnings=False):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                # Should not raise
                Tool.from_function(my_tool, serializer=custom_serializer)

    def test_local_provider_tool_serializer_warning(self):
        """Test that LocalProvider.tool warns when serializer is provided."""
        provider = LocalProvider()

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        def my_tool(x: int) -> int:
            return x * 2

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):
                provider.tool(my_tool, serializer=custom_serializer)

    def test_local_provider_tool_decorator_serializer_warning(self):
        """Test that LocalProvider.tool decorator warns when serializer is provided."""
        provider = LocalProvider()

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):

                @provider.tool(serializer=custom_serializer)
                def my_tool(x: int) -> int:
                    return x * 2

    def test_fastmcp_tool_serializer_warning(self):
        """Test that FastMCP.tool warns when serializer is provided via LocalProvider."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        def my_tool(x: int) -> int:
            return x * 2

        # FastMCP.tool doesn't accept serializer directly, it goes through LocalProvider
        # So we test LocalProvider.tool which is what FastMCP uses internally
        provider = LocalProvider()
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):
                provider.tool(my_tool, serializer=custom_serializer)

    def test_fastmcp_tool_serializer_parameter_warning(self):
        """Test that FastMCP tool_serializer parameter warns."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="tool_serializer.*deprecated"):
                FastMCP("TestServer", tool_serializer=custom_serializer)

    def test_transformed_tool_from_tool_serializer_warning(self):
        """Test that TransformedTool.from_tool warns when serializer is provided."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        def my_tool(x: int) -> int:
            return x * 2

        parent_tool = Tool.from_function(my_tool)

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):
                TransformedTool.from_tool(parent_tool, serializer=custom_serializer)

    def test_mcp_mixin_tool_serializer_warning(self):
        """Test that mcp_tool decorator warns when serializer is provided."""

        def custom_serializer(data) -> str:
            return f"Custom: {data}"

        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning, match="serializer.*deprecated"):

                @mcp_tool(serializer=custom_serializer)
                def my_tool(x: int) -> int:
                    return x * 2
