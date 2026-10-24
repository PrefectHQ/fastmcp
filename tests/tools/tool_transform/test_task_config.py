"""Transformed tools keep their background-task capability (SEP-2663, tools only).

Transforms are the only wrapping path that rebuilds a tool from a parent; the
mount path (`FastMCPProviderTool.wrap`) already carries `task_config` and
`execution` across. A rename or rewrap must not silently downgrade a task-enabled
tool to foreground-only.
"""

import pytest
from mcp_types import ToolExecution

from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.tasks import TaskConfig


@pytest.fixture
def task_tool():
    """A task-enabled tool: `task=True` yields mode `optional`."""

    async def slow(x: int) -> int:
        return x

    return Tool.from_function(slow, name="slow", task=True)


@pytest.fixture
def plain_tool():
    """A tool with no task support."""

    async def quick(x: int) -> int:
        return x

    return Tool.from_function(quick, name="quick")


def test_transform_inherits_task_config(task_tool):
    """A pure rename preserves the parent's task mode."""
    assert task_tool.task_config.mode == "optional"

    transformed = Tool.from_tool(task_tool, name="slow_v2")

    assert transformed.task_config.mode == "optional"
    assert transformed.task_config.supports_tasks()


def test_transformed_tool_advertises_execution_on_wire(task_tool):
    """The wire tool must advertise task execution so clients can opt in."""
    assert task_tool.to_mcp_tool().execution is not None

    transformed = Tool.from_tool(task_tool, name="slow_v2")

    execution = transformed.to_mcp_tool().execution
    assert execution is not None
    assert execution.task_support == "optional"


def test_transform_preserves_explicit_execution_without_task_support(plain_tool):
    """`execution` is threaded separately from `task_config`.

    `to_mcp_tool()` only derives `execution` from `task_config` when the tool has
    no explicit `execution`, so a parent that advertises execution on its own
    (e.g. a proxied tool) would lose it if only `task_config` were carried over.
    """
    parent = plain_tool.model_copy(
        update={
            "task_config": TaskConfig(mode="forbidden"),
            "execution": ToolExecution(task_support="required"),
        }
    )
    assert parent.to_mcp_tool().execution is not None

    transformed = Tool.from_tool(parent, name="quick_v2")

    assert not transformed.task_config.supports_tasks()
    assert transformed.execution == parent.execution
    assert transformed.to_mcp_tool().execution is not None


def test_transform_does_not_add_task_support(plain_tool):
    """A tool without task support stays forbidden after a transform."""
    transformed = Tool.from_tool(plain_tool, name="quick_v2")

    assert transformed.task_config.mode == "forbidden"
    assert transformed.to_mcp_tool().execution is None


async def test_server_transform_keeps_task_tool_registered(task_tool):
    """A server-level rename keeps the tool in `get_tasks()`."""
    mcp = FastMCP("test")
    mcp.add_transform(ToolTransform({"slow": ToolTransformConfig(name="slow_v2")}))
    mcp.add_tool(task_tool)

    assert [c.name for c in await mcp.get_tasks()] == ["slow_v2"]
