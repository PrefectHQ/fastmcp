"""Shutdown regression for end-and-reenter task input.

The whole point of end-and-reenter is that a task waiting on client input holds
no worker: the guard leg's Docket execution completed and the worker is free.
This test proves it — a task parked in ``input_required`` that is never answered
must not delay server shutdown. Under the old block-and-resume model the worker
sat on a Redis wait for the input TTL and wedged teardown; here the lifespan
exits promptly.
"""

from __future__ import annotations

import asyncio

import mcp_types

from fastmcp import Context, FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import running_task_server, submit_task, wait_for_task


def _elicit_request(message: str) -> mcp_types.ElicitRequest:
    return mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        )
    )


async def test_parked_task_does_not_delay_shutdown():
    """Exiting the lifespan with a task in input_required (never answered) must
    return promptly — no worker is parked awaiting input."""
    mcp = FastMCP("parked-shutdown")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def greet(ctx: Context) -> str | mcp_types.InputRequiredResult:
        if ctx.input_responses is None:
            return mcp_types.InputRequiredResult(
                result_type="input_required",
                input_requests={"name": _elicit_request("Your name?")},
                request_state=None,
            )
        return "done"

    loop = asyncio.get_event_loop()
    manager = running_task_server(mcp)
    await manager.__aenter__()
    try:
        created = await submit_task(mcp, "greet", {})
        parked = await wait_for_task(
            mcp, created.task_id, target_states=frozenset({"input_required"})
        )
        assert parked.status == "input_required"
    finally:
        # Never answer; time how long teardown takes.
        started = loop.time()
        await manager.__aexit__(None, None, None)
        elapsed = loop.time() - started

    assert elapsed < 3.0, f"lifespan took {elapsed:.2f}s to exit with a parked task"
