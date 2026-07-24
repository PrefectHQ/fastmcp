"""The transparent client task flow over a real in-memory connection.

A real `Client(server, mode="auto")` calls a `task=True` tool; the server runs it
as a task and answers `tools/call` with a `CreateTaskResult`; the client's
auto-registered tasks extension resolves it by polling `tasks/get` to completion.
The caller of `call_tool` sees only the tool's real result — never that the call
was tasked. This is the whole point of the client half.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import mcp_types
import pytest
from mcp.shared.exceptions import MCPError

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp_tasks import TasksExtension, call_tool_task


@pytest.fixture
def task_server() -> FastMCP:
    mcp = FastMCP("transparent-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        await asyncio.sleep(0.01)
        return a * b

    @mcp.tool(task=True)
    async def boom() -> str:
        raise ValueError("kaboom")

    @mcp.tool(task=True)
    async def slow() -> str:
        await asyncio.sleep(5)
        return "done"

    return mcp


async def test_call_tool_timeout_bounds_total_task_drive(task_server: FastMCP):
    """A per-call timeout bounds the whole tasked drive, not just one poll.

    The tool runs far longer than the timeout while each individual poll answers
    instantly; the transparent path must still abort once total execution passes
    the deadline, matching the synchronous `tools/call` timeout contract.
    """
    async with Client(task_server, mode="auto") as client:
        with pytest.raises((TimeoutError, MCPError)):
            await client.call_tool("slow", {}, timeout=0.3)


async def test_call_tool_transparently_completes_a_task(task_server: FastMCP):
    """call_tool returns the tool's real result; the caller never sees a task."""
    async with Client(task_server, mode="auto") as client:
        result = await client.call_tool("multiply", {"a": 6, "b": 7})

    assert result.data == 42


async def test_call_tool_mcp_returns_completed_result(task_server: FastMCP):
    """call_tool_mcp resolves the tasked call into an ordinary CallToolResult."""
    async with Client(task_server, mode="auto") as client:
        result = await client.call_tool_mcp("multiply", {"a": 3, "b": 4})

    assert result.structured_content == {"result": 12}
    assert not result.is_error


async def test_failed_task_raises_tool_error(task_server: FastMCP):
    """A task whose tool raises surfaces as a ToolError through call_tool."""
    async with Client(task_server, mode="auto") as client:
        with pytest.raises(ToolError, match="kaboom"):
            await client.call_tool("boom", {})


async def test_call_tool_task_forwards_requested_version():
    """`call_tool_task(..., version=...)` tasks the requested version, not the highest."""
    mcp = FastMCP("versioned-task-client")
    mcp.add_extension(TasksExtension())

    @mcp.tool(name="pick", version="1.0", task=True)
    async def pick_v1() -> str:
        return "v1"

    @mcp.tool(name="pick", version="2.0", task=True)
    async def pick_v2() -> str:
        return "v2"

    async with Client(mcp, mode="auto") as client:
        task = await call_tool_task(client, "pick", version="1.0")
        result = await task.result()

    assert result.data == "v1"


async def test_raw_create_task_result_is_exposed(task_server: FastMCP):
    """The raw claimed CreateTaskResult is reachable via the session/handle path."""
    async with Client(task_server, mode="auto") as client:
        task = await call_tool_task(client, "multiply", {"a": 2, "b": 5})
        # The raw claimed shape is exposed on the handle.
        assert task.create_result.result_type == "task"
        assert task.create_result.status == "working"
        assert isinstance(task.task_id, str) and task.task_id

        result = await task.result()
        assert result.data == 10


async def test_legacy_client_never_tasks(task_server: FastMCP):
    """A legacy-era client never negotiates the capability, so nothing is tasked.

    The optional-mode tool simply runs synchronously and returns its result
    directly (no CreateTaskResult on the wire).
    """
    async with Client(task_server, mode="legacy") as client:
        result = await client.call_tool("multiply", {"a": 8, "b": 9})

    assert result.data == 72


# --- In-task input over the wire -------------------------------------------


@dataclass
class DinnerPrefs:
    cuisine: str
    vegetarian: bool


def _elicit_request(message: str) -> mcp_types.ElicitRequest:
    return mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {
                    "cuisine": {"type": "string"},
                    "vegetarian": {"type": "boolean"},
                },
                "required": ["cuisine", "vegetarian"],
            },
        )
    )


@pytest.fixture
def guard_server() -> FastMCP:
    mcp = FastMCP("guard-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def plan_dinner(
        ctx: Context,
    ) -> str | mcp_types.InputRequiredResult:
        responses = ctx.input_responses
        if responses is None:
            return mcp_types.InputRequiredResult(
                result_type="input_required",
                input_requests={"prefs": _elicit_request("What's for dinner?")},
            )
        answer = responses["prefs"]
        assert isinstance(answer, mcp_types.ElicitResult)
        assert answer.content is not None
        veg = "vegetarian " if answer.content["vegetarian"] else ""
        return f"Tonight: a {veg}{answer.content['cuisine']} dinner!"

    return mcp


async def test_in_task_input_answered_transparently(guard_server: FastMCP):
    """A guard task that asks for input is answered via the elicitation handler."""

    async def handle_elicitation(message, response_type, params, context):
        return DinnerPrefs(cuisine="Thai", vegetarian=True)

    client = Client(guard_server, mode="auto", elicitation_handler=handle_elicitation)
    async with client:
        result = await client.call_tool("plan_dinner", {})

    assert result.data == "Tonight: a vegetarian Thai dinner!"


async def test_in_task_input_without_handler_errors(guard_server: FastMCP):
    """A guard task with no elicitation handler surfaces a clear error."""
    async with Client(guard_server, mode="auto") as client:
        with pytest.raises(ToolError, match="no elicitation handler"):
            await client.call_tool("plan_dinner", {})


async def test_call_tool_timeout_bounds_a_stalled_elicitation(guard_server: FastMCP):
    """A stalled elicitation handler cannot outlast the call's timeout.

    The deadline covers the whole drive, elicitation callbacks included: a
    handler that hangs must abort the tasked call once `timeout=N` elapses,
    matching the synchronous path rather than blocking forever inside the
    callback.
    """

    async def slow_elicitation(message, response_type, params, context):
        await asyncio.sleep(5)
        return DinnerPrefs(cuisine="Thai", vegetarian=True)

    client = Client(guard_server, mode="auto", elicitation_handler=slow_elicitation)
    async with client:
        with pytest.raises((TimeoutError, ToolError, MCPError)):
            await client.call_tool("plan_dinner", {}, timeout=0.3)


async def test_in_task_input_answered_by_handler_set_after_construction(
    guard_server: FastMCP,
):
    """An elicitation handler set via set_elicitation_callback reaches in-task input.

    The tasks client extension is built at construction; set_elicitation_callback
    must rebuild it so a later-configured handler still answers a task's input.
    """

    async def handle_elicitation(message, response_type, params, context):
        return DinnerPrefs(cuisine="Thai", vegetarian=True)

    client = Client(guard_server, mode="auto")
    client.set_elicitation_callback(handle_elicitation)
    async with client:
        result = await client.call_tool("plan_dinner", {})

    assert result.data == "Tonight: a vegetarian Thai dinner!"
