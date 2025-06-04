"""Tests for deprecated Tool.from_function() method.

The Tool.from_function() method was deprecated in version 2.6.2 in favor of
FunctionTool.from_function().
"""

import warnings

import pytest

from fastmcp.tools.tool import FunctionTool, Tool


def test_tool_from_function_deprecation_warning():
    """Test that Tool.from_function() raises a deprecation warning."""

    def example_function(x: int) -> str:
        """Example function for testing."""
        return f"Result: {x}"

    with pytest.warns(
        UserWarning,
        match="Tool.from_function\\(\\) is deprecated. Use FunctionTool.from_function\\(\\) instead.",
    ):
        tool = Tool.from_function(example_function)

    # Verify the tool was created correctly despite the warning
    assert isinstance(tool, FunctionTool)
    assert tool.name == "example_function"
    assert tool.description == "Example function for testing."


def test_tool_from_function_produces_same_result_as_function_tool():
    """Test that Tool.from_function() produces the same result as FunctionTool.from_function()."""

    def example_function(x: int, y: str = "default") -> dict:
        """Example function with parameters."""
        return {"x": x, "y": y}

    # Create tool using the deprecated method (with warning suppressed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        deprecated_tool = Tool.from_function(example_function)

    # Create tool using the new method
    new_tool = FunctionTool.from_function(example_function)

    # They should be equivalent
    assert deprecated_tool == new_tool
    assert deprecated_tool.name == new_tool.name
    assert deprecated_tool.description == new_tool.description
    assert deprecated_tool.parameters == new_tool.parameters


def test_tool_from_function_with_overrides():
    """Test that Tool.from_function() works with parameter overrides."""

    def example_function() -> str:
        """Original description."""
        return "test"

    custom_name = "custom_tool_name"
    custom_description = "Custom description"
    custom_tags = {"test", "deprecated"}

    with pytest.warns(UserWarning, match="Tool.from_function\\(\\) is deprecated"):
        tool = Tool.from_function(
            example_function,
            name=custom_name,
            description=custom_description,
            tags=custom_tags,
        )

    assert tool.name == custom_name
    assert tool.description == custom_description
    assert tool.tags == custom_tags
