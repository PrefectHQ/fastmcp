"""Tests for custom Tool subclasses with task support.

Verifies that custom Tool subclasses can use background task execution by
setting task_config. SEP-2663 is tools-only, so the removed resource/prompt
subclass cases are gone.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp_tasks.components import (
    add_component_to_docket,
    register_component_with_docket,
)
from fastmcp_tasks.models import CreateTaskResult

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    call_tool_without_optin,
    run_task,
    running_task_server,
)


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


class CustomToolRaisesToolError(Tool):
    """A custom tool whose `run` raises a `ToolError`."""

    task_config: TaskConfig = TaskConfig(mode="optional")
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise ToolError("kaboom")


class CustomToolRaisesValueError(Tool):
    """A custom tool whose `run` raises a non-FastMCP exception."""

    task_config: TaskConfig = TaskConfig(mode="optional")
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise ValueError("secret internal detail")


@pytest.fixture
def custom_tool_server() -> FastMCP:
    """A server with custom tool subclasses."""
    mcp = FastMCP("custom-tool-server")
    mcp.add_extension(TasksExtension())
    mcp.add_tool(CustomTool(name="custom_tool", description="A custom tool"))
    mcp.add_tool(
        CustomToolWithLogic(name="custom_logic", description="Custom tool with logic")
    )
    mcp.add_tool(
        CustomToolForbidden(name="custom_forbidden", description="No task support")
    )
    return mcp


async def test_custom_tool_sync_execution(custom_tool_server):
    """Custom tool executes synchronously without a tasks opt-in."""
    async with running_task_server(custom_tool_server):
        result = await call_tool_without_optin(custom_tool_server, "custom_tool", {})
        assert "Custom tool executed" in result.content[0].text


async def test_custom_tool_background_execution(custom_tool_server):
    """Custom tool executes as a background task when opted in."""
    async with running_task_server(custom_tool_server):
        final = await run_task(custom_tool_server, "custom_tool", {})

    assert final.status == "completed"
    assert final.result is not None
    assert "Custom tool executed" in final.result["content"][0]["text"]


async def test_custom_tool_with_arguments(custom_tool_server):
    """Custom tool receives arguments correctly in background execution."""
    async with running_task_server(custom_tool_server):
        final = await run_task(custom_tool_server, "custom_logic", {"duration": 1})

    assert final.status == "completed"
    assert final.result is not None
    assert "Completed after 1 units" in final.result["content"][0]["text"]


async def test_custom_tool_forbidden_sync_only(custom_tool_server):
    """Custom tool with forbidden mode executes synchronously."""
    async with running_task_server(custom_tool_server):
        result = await call_tool_without_optin(
            custom_tool_server, "custom_forbidden", {}
        )
        assert "Sync only" in result.content[0].text


async def test_custom_tool_forbidden_rejects_task(custom_tool_server):
    """A forbidden tool runs synchronously even when the client opts in."""
    async with running_task_server(custom_tool_server):
        with auth_scope(None), _opted_in_request("custom_forbidden", {}, None):
            result = await custom_tool_server.call_tool("custom_forbidden", {})
        assert not isinstance(result, CreateTaskResult)
        assert "Sync only" in result.content[0].text


async def test_custom_tool_raising_tool_error_completes_with_is_error():
    """A custom Tool that raises `ToolError` is a completed, is_error task.

    Same contract as a raising `FunctionTool`: a raised tool error is a
    completed task carrying an `isError` result (never a `failed` task), and a
    `ToolError` reaches the client verbatim — matching the synchronous path.
    """
    mcp = FastMCP("custom-raise-server")
    mcp.add_extension(TasksExtension())
    mcp.add_tool(CustomToolRaisesToolError(name="boom", description="raises"))

    async with running_task_server(mcp):
        final = await run_task(mcp, "boom", {})

    assert final.status == "completed"
    assert final.error is None
    assert final.result is not None
    assert final.result["isError"] is True
    assert "kaboom" in final.result["content"][0]["text"]


async def test_custom_tool_raising_generic_error_is_masked():
    """A custom Tool's non-FastMCP exception is masked, like the sync path.

    A base `Tool` subclass must route through the same error conversion as a
    `FunctionTool`, so `mask_error_details=True` hides the raw exception text
    rather than leaking it through Docket's `FAILED` outcome.
    """
    mcp = FastMCP("custom-mask-server", mask_error_details=True)
    mcp.add_extension(TasksExtension())
    mcp.add_tool(CustomToolRaisesValueError(name="leak", description="raises"))

    async with running_task_server(mcp):
        final = await run_task(mcp, "leak", {})

    assert final.status == "completed"
    assert final.error is None
    assert final.result is not None
    assert final.result["isError"] is True
    text = final.result["content"][0]["text"]
    assert "secret internal detail" not in text
    assert "Error calling tool 'leak'" in text


async def test_custom_tool_registers_with_docket():
    """A task-capable custom tool registers its `run` entry point with Docket."""
    tool = CustomTool(name="test", description="test")
    mock_docket = MagicMock()

    register_component_with_docket(tool, mock_docket)

    mock_docket.register.assert_called_once()
    call_args = mock_docket.register.call_args
    assert call_args[1]["names"] == ["tool:test@"]


async def test_custom_tool_forbidden_does_not_register():
    """A forbidden custom tool does not register with Docket."""
    tool = CustomToolForbidden(name="test", description="test")
    mock_docket = MagicMock()

    register_component_with_docket(tool, mock_docket)

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
        """Registering a forbidden base component is a no-op."""
        component = FastMCPComponent(name="test")
        mock_docket = MagicMock()

        register_component_with_docket(component, mock_docket)

        mock_docket.register.assert_not_called()

    async def test_add_to_docket_raises_when_forbidden(self):
        """add_component_to_docket raises RuntimeError when mode is 'forbidden'."""
        component = FastMCPComponent(name="test")
        mock_docket = MagicMock()

        with pytest.raises(RuntimeError, match="task execution not supported"):
            await add_component_to_docket(component, mock_docket, None)

    async def test_add_to_docket_raises_not_implemented_when_allowed(self):
        """add_component_to_docket raises NotImplementedError for an unknown type."""
        component = FastMCPComponent(
            name="test", task_config=TaskConfig(mode="optional")
        )
        mock_docket = MagicMock()

        with pytest.raises(
            NotImplementedError, match="does not implement add_to_docket"
        ):
            await add_component_to_docket(component, mock_docket, None)
