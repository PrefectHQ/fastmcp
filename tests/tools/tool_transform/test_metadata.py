from typing import Annotated, Any

import pytest
from pydantic import Field

from fastmcp.tools import Tool
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
)


def get_property(tool: Tool, name: str) -> dict[str, Any]:
    return tool.parameters["properties"][name]


@pytest.fixture
def add_tool() -> FunctionTool:
    def add(
        old_x: Annotated[int, Field(description="old_x description")], old_y: int = 10
    ) -> int:
        print("running!")
        return old_x + old_y

    return Tool.from_function(add)


@pytest.fixture
def sample_tool():
    """Sample tool for testing transformations."""

    def sample_func(x: int) -> str:
        return f"Result: {x}"

    return Tool.from_function(
        sample_func,
        name="sample_tool",
        title="Original Tool Title",
        description="Original description",
    )


@pytest.fixture
def sample_tool_no_title():
    """Sample tool without title for testing."""

    def sample_func(x: int) -> str:
        return f"Result: {x}"

    return Tool.from_function(sample_func, name="no_title_tool")


def test_transform_inherits_title(sample_tool):
    """Test that transformed tools inherit title when none specified."""
    transformed = Tool.from_tool(sample_tool)
    assert transformed.title == "Original Tool Title"


def test_transform_overrides_title(sample_tool):
    """Test that transformed tools can override title."""
    transformed = Tool.from_tool(sample_tool, title="New Tool Title")
    assert transformed.title == "New Tool Title"


def test_transform_sets_title_to_none(sample_tool):
    """Test that transformed tools can explicitly set title to None."""
    transformed = Tool.from_tool(sample_tool, title=None)
    assert transformed.title is None


def test_transform_inherits_none_title(sample_tool_no_title):
    """Test that transformed tools inherit None title."""
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.title is None


def test_transform_adds_title_to_none(sample_tool_no_title):
    """Test that transformed tools can add title when parent has None."""
    transformed = Tool.from_tool(sample_tool_no_title, title="Added Title")
    assert transformed.title == "Added Title"


def test_transform_inherits_description(sample_tool):
    """Test that transformed tools inherit description when none specified."""
    transformed = Tool.from_tool(sample_tool)
    assert transformed.description == "Original description"


def test_transform_overrides_description(sample_tool):
    """Test that transformed tools can override description."""
    transformed = Tool.from_tool(sample_tool, description="New description")
    assert transformed.description == "New description"


def test_transform_sets_description_to_none(sample_tool):
    """Test that transformed tools can explicitly set description to None."""
    transformed = Tool.from_tool(sample_tool, description=None)
    assert transformed.description is None


def test_transform_inherits_none_description(sample_tool_no_title):
    """Test that transformed tools inherit None description."""
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.description is None


def test_transform_adds_description_to_none(sample_tool_no_title):
    """Test that transformed tools can add description when parent has None."""
    transformed = Tool.from_tool(sample_tool_no_title, description="Added description")
    assert transformed.description == "Added description"


# Meta transformation tests
def test_transform_inherits_meta(sample_tool):
    """Test that transformed tools inherit meta when none specified."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool)
    assert transformed.meta == {"original": True, "version": "1.0"}


def test_transform_overrides_meta(sample_tool):
    """Test that transformed tools can override meta."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool, meta={"custom": True, "priority": "high"})
    assert transformed.meta == {"custom": True, "priority": "high"}


def test_transform_sets_meta_to_none(sample_tool):
    """Test that transformed tools can explicitly set meta to None."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool, meta=None)
    assert transformed.meta is None


def test_transform_inherits_none_meta(sample_tool_no_title):
    """Test that transformed tools inherit None meta."""
    sample_tool_no_title.meta = None
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.meta is None


def test_transform_adds_meta_to_none(sample_tool_no_title):
    """Test that transformed tools can add meta when parent has None."""
    sample_tool_no_title.meta = None
    transformed = Tool.from_tool(sample_tool_no_title, meta={"added": True})
    assert transformed.meta == {"added": True}


def test_tool_transform_config_inherits_meta(sample_tool):
    """Test that ToolTransformConfig inherits meta when unset."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(name="config_tool")
    transformed = config.apply(sample_tool)
    assert transformed.meta == {"original": True, "version": "1.0"}


def test_tool_transform_config_overrides_meta(sample_tool):
    """Test that ToolTransformConfig can override meta."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(
        name="config_tool", meta={"config": True, "priority": "high"}
    )
    transformed = config.apply(sample_tool)
    assert transformed.meta == {"config": True, "priority": "high"}


def test_tool_transform_config_removes_meta(sample_tool):
    """Test that ToolTransformConfig can remove meta with None."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(name="config_tool", meta=None)
    transformed = config.apply(sample_tool)
    assert transformed.meta is None
