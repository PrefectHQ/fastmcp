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


def _server(*, strict: bool = False) -> FastMCP:
    """A server whose only tool is task-enabled and renamed by a transform."""
    mcp = FastMCP("transformed-task-server", strict_input_validation=strict)
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


@pytest.mark.parametrize("value, strict", [("not-an-integer", False), ("1", True)])
async def test_transformed_submission_rejects_invalid_type(value: str, strict: bool):
    mcp = _server(strict=strict)
    async with running_task_server(mcp):
        with pytest.raises((ToolError, ValidationError)):
            await call_tool_without_optin(mcp, "add_v2", {"a": value})
        with pytest.raises(ValidationError):
            await submit_task(mcp, "add_v2", {"a": value})


async def test_transformed_submission_preserves_lax_coercion():
    mcp = _server()
    async with running_task_server(mcp):
        final = await run_task(mcp, "add_v2", {"a": "3"})
    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 4}


async def test_transformed_submission_preserves_parent_input_validators():
    from typing import Annotated

    from fastmcp_tasks.components import coerce_task_arguments
    from pydantic import BeforeValidator

    def normalize(value):
        return 0 if value == "zero" else value

    async def accept(value):
        return value

    # Install the actual runtime annotation so the local normalizer is resolved.
    accept.__annotations__["value"] = Annotated[int, BeforeValidator(normalize)]
    accept.__annotations__["return"] = int
    tool = Tool.from_tool(Tool.from_function(accept, task=True), name="normalized")
    arguments = coerce_task_arguments(tool, {"value": "zero"})
    result = await tool.run(arguments)
    assert result.structured_content == {"result": 0}


async def test_transformed_submission_preserves_set_coercion():
    from fastmcp_tasks.components import coerce_task_arguments

    async def count(values: set[int]) -> int:
        return len(values)

    tool = Tool.from_tool(Tool.from_function(count, task=True), name="count_v2")
    arguments = coerce_task_arguments(tool, {"values": [1, 1]})
    result = await tool.run(arguments)
    assert result.structured_content == {"result": 1}


async def test_submission_leaves_transform_factories_to_execution():
    from fastmcp_tasks.components import coerce_task_arguments

    calls = []

    def default_value() -> int:
        calls.append(1)
        return 7

    async def echo(value: int) -> int:
        return value

    tool = Tool.from_tool(
        Tool.from_function(echo, task=True),
        name="echo_v2",
        transform_args={
            "value": ArgTransform(hide=True, default_factory=default_value)
        },
    )
    before = len(calls)
    arguments = coerce_task_arguments(tool, {})
    assert len(calls) == before
    result = await tool.run(arguments)
    assert len(calls) == before + 1
    assert result.structured_content == {"result": 7}


async def test_submission_leaves_parent_default_factory_to_execution():
    from typing import Annotated

    from fastmcp_tasks.components import coerce_task_arguments
    from pydantic import Field

    calls = []

    def default_value() -> int:
        calls.append(1)
        return 7

    async def echo(value):
        return value

    echo.__annotations__["value"] = Annotated[int, Field(default_factory=default_value)]
    echo.__annotations__["return"] = int
    tool = Tool.from_tool(Tool.from_function(echo, task=True), name="echo_default")
    before = len(calls)
    arguments = coerce_task_arguments(tool, {})
    assert len(calls) == before
    result = await tool.run(arguments)
    assert len(calls) == before + 1
    assert result.structured_content == {"result": 7}


@pytest.mark.parametrize("use_factory", [False, True])
async def test_hidden_alias_is_validated_without_running_factory(use_factory: bool):
    from typing import Annotated

    from fastmcp_tasks.components import coerce_task_arguments
    from pydantic import Field

    calls = []

    def value_factory() -> int:
        calls.append(1)
        return 7

    async def echo(value):
        return value

    echo.__annotations__["value"] = Annotated[int, Field(alias="input")]
    echo.__annotations__["return"] = int
    transform = (
        ArgTransform(hide=True, default_factory=value_factory)
        if use_factory
        else ArgTransform(hide=True, default=7)
    )
    tool = Tool.from_tool(
        Tool.from_function(echo, task=True), transform_args={"input": transform}
    )
    arguments = coerce_task_arguments(tool, {})
    assert calls == []
    result = await tool.run(arguments)
    assert len(calls) == int(use_factory)
    assert result.structured_content == {"result": 7}


@pytest.mark.parametrize("hidden", [False, True])
async def test_aliased_field_default_factory_only_runs_during_execution(hidden: bool):
    from fastmcp_tasks.components import coerce_task_arguments
    from pydantic import Field

    calls = []

    def value_factory() -> int:
        calls.append(1)
        return 7

    async def echo(
        value: int = Field(default_factory=value_factory, alias="input"),
    ) -> int:
        return value

    transform_args = (
        {"input": ArgTransform(hide=True, default_factory=value_factory)}
        if hidden
        else {}
    )
    tool = Tool.from_tool(
        Tool.from_function(echo, task=True), transform_args=transform_args
    )
    arguments = coerce_task_arguments(tool, {})
    assert calls == []
    result = await tool.run(arguments)
    assert calls == [1]
    assert result.structured_content == {"result": 7}
