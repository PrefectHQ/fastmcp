"""Task submission for transformed tools (SEP-2663, tools only).

A transformed tool stays task-capable (#5780), so its arguments now reach the
task-submission path, which runs before ``TransformedTool.run`` — the only place
a transform enforces its argument contract. These tests pin the submission-time
gate: a call the synchronous path rejects must raise instead of being persisted
as a task that only fails once a worker picks it up.
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransform, ToolTransformConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    run_task,
    running_task_server,
    submit_task,
)


def _server() -> FastMCP:
    """A server whose only tool is task-enabled and renamed by a transform."""
    mcp = FastMCP("transformed-task-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def add(a: int, b: int = 1) -> int:
        return a + b

    mcp.add_transform(ToolTransform({"add": ToolTransformConfig(name="add_v2")}))
    return mcp


async def test_transformed_submission_rejects_missing_required():
    """A call missing a required argument raises instead of creating a task."""
    mcp = _server()
    async with running_task_server(mcp):
        with pytest.raises(ToolError):
            await call_tool_without_optin(mcp, "add_v2", {"b": 2})
        with pytest.raises(ValidationError, match="missing required argument"):
            await submit_task(mcp, "add_v2", {"b": 2})


async def test_transformed_submission_rejects_unknown_argument():
    """An argument the transform does not expose raises instead of queueing."""
    mcp = _server()
    async with running_task_server(mcp):
        with pytest.raises(ToolError):
            await call_tool_without_optin(mcp, "add_v2", {"a": 1, "c": 3})
        with pytest.raises(ValidationError, match="unexpected argument"):
            await submit_task(mcp, "add_v2", {"a": 1, "c": 3})


async def test_transformed_submission_accepts_omitted_default():
    """A schema default satisfies ``required`` exactly as it does for a plain tool."""
    mcp = _server()
    async with running_task_server(mcp):
        final = await run_task(mcp, "add_v2", {"a": 1})
    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 2}


async def test_transformed_submission_accepts_an_explicit_default():
    """Passing the defaulted argument explicitly still submits."""
    mcp = _server()
    async with running_task_server(mcp):
        final = await run_task(mcp, "add_v2", {"a": 1, "b": 4})
    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 5}


async def test_renamed_argument_submission_uses_the_transformed_name():
    """A renamed argument is accepted under its new name and rejected under the old."""

    async def scale(factor: int) -> int:
        return factor * 2

    parent = Tool.from_function(scale, name="scale", task=True)
    renamed = Tool.from_tool(
        parent,
        name="scale_v2",
        transform_args={"factor": ArgTransform(name="multiplier")},
    )

    mcp = FastMCP("renamed-arg-task-server")
    mcp.add_extension(TasksExtension())
    mcp.add_tool(renamed)

    async with running_task_server(mcp):
        with pytest.raises(ValidationError, match="unexpected argument"):
            await submit_task(mcp, "scale_v2", {"factor": 2})
        final = await run_task(mcp, "scale_v2", {"multiplier": 3})

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 6}
