"""Tests for custom component subclasses with task support.

Verifies that custom Tool, Resource, and Prompt subclasses can use
background task execution by setting task_config.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.tasks import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent


class CustomTool(Tool):
    """A custom tool subclass with task support."""

    task_config: TaskConfig = TaskConfig(mode="optional")
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=f"Custom tool executed with {arguments}")


class CustomToolWithLogic(Tool):
    """A custom tool with actual async work."""

    task_config: TaskConfig = TaskConfig(mode="optional")
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"duration": {"type": "integer"}},
    }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        duration = arguments.get("duration", 0)
        await asyncio.sleep(duration * 0.01)  # Short sleep for testing
        return ToolResult(content=f"Completed after {duration} units")


class CustomToolForbidden(Tool):
    """A custom tool with task_config forbidden (default)."""

    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content="Sync only")


@pytest.fixture
def custom_tool_server():
    """Create a server with custom tool subclasses."""
    mcp = FastMCP("custom-tool-server")
    mcp.add_tool(CustomTool(name="custom_tool", description="A custom tool"))
    mcp.add_tool(
        CustomToolWithLogic(name="custom_logic", description="Custom tool with logic")
    )
    mcp.add_tool(
        CustomToolForbidden(name="custom_forbidden", description="No task support")
    )
    return mcp


async def test_custom_tool_sync_execution(custom_tool_server):
    """Custom tool executes synchronously when no task metadata."""
    async with Client(custom_tool_server) as client:
        result = await client.call_tool("custom_tool", {})
        assert "Custom tool executed" in str(result)


async def test_custom_tool_background_execution(custom_tool_server):
    """Custom tool executes as background task when task=True."""
    async with Client(custom_tool_server) as client:
        task = await client.call_tool("custom_tool", {}, task=True)

        assert task is not None
        assert not task.returned_immediately
        assert task.task_id is not None

        # Wait for result
        result = await task.result()
        assert "Custom tool executed" in str(result)


async def test_custom_tool_with_arguments(custom_tool_server):
    """Custom tool receives arguments correctly in background execution."""
    async with Client(custom_tool_server) as client:
        task = await client.call_tool("custom_logic", {"duration": 1}, task=True)

        assert task is not None
        result = await task.result()
        assert "Completed after 1 units" in str(result)


async def test_custom_tool_forbidden_sync_only(custom_tool_server):
    """Custom tool with forbidden mode executes sync only."""
    async with Client(custom_tool_server) as client:
        # Sync execution works
        result = await client.call_tool("custom_forbidden", {})
        assert "Sync only" in str(result)


async def test_custom_tool_forbidden_rejects_task(custom_tool_server):
    """Custom tool with forbidden mode returns error for task request."""
    async with Client(custom_tool_server) as client:
        task = await client.call_tool("custom_forbidden", {}, task=True)

        # Should return immediately with error
        assert task.returned_immediately


async def test_custom_tool_registers_with_docket():
    """Verify custom tool's register_with_docket is called during server startup."""
    from unittest.mock import MagicMock

    tool = CustomTool(name="test", description="test")
    mock_docket = MagicMock()

    tool.register_with_docket(mock_docket)

    # Should register self.run with docket using prefixed key
    mock_docket.register.assert_called_once()
    call_args = mock_docket.register.call_args
    assert call_args[1]["names"] == ["tool:test@"]


async def test_custom_tool_forbidden_does_not_register():
    """Verify custom tool with forbidden mode doesn't register with docket."""
    tool = CustomToolForbidden(name="test", description="test")
    mock_docket = MagicMock()

    tool.register_with_docket(mock_docket)

    # Should NOT register
    mock_docket.register.assert_not_called()


# ==============================================================================
# Base FastMCPComponent Tests
# ==============================================================================


class TestFastMCPComponentDocketMethods:
    """Tests for base FastMCPComponent docket integration."""

    def test_default_task_config_is_forbidden(self):
        """Base component defaults to task_config mode='forbidden'."""
        component = FastMCPComponent(name="test")
        assert component.task_config.mode == "forbidden"

    def test_register_with_docket_is_noop(self):
        """Base register_with_docket does nothing (subclasses override)."""
        component = FastMCPComponent(name="test")
        mock_docket = MagicMock()

        # Should not raise, just no-op
        component.register_with_docket(mock_docket)

        # Should not have called any docket methods
        mock_docket.register.assert_not_called()

    async def test_add_to_docket_raises_when_forbidden(self):
        """Base add_to_docket raises RuntimeError when mode is 'forbidden'."""
        component = FastMCPComponent(name="test")
        mock_docket = MagicMock()

        with pytest.raises(RuntimeError, match="task execution not supported"):
            await component.add_to_docket(mock_docket)

    async def test_add_to_docket_raises_not_implemented_when_allowed(self):
        """Base add_to_docket raises NotImplementedError when not forbidden."""
        component = FastMCPComponent(
            name="test", task_config=TaskConfig(mode="optional")
        )
        mock_docket = MagicMock()

        with pytest.raises(
            NotImplementedError, match="does not implement add_to_docket"
        ):
            await component.add_to_docket(mock_docket)
