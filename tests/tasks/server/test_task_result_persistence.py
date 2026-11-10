"""A completed task's result is converted in the worker and stored with it.

`tasks/get` must keep returning the result after the producing tool is removed,
and a return value that fails to serialize must respect the server's masking
policy rather than surface its exception text.
"""

from pydantic import BaseModel, field_serializer

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import get_task, run_task, running_task_server


async def test_completed_result_survives_tool_removal():
    mcp = FastMCP("removal-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def removable_tool() -> str:
        return "done"

    async with running_task_server(mcp):
        final = await run_task(mcp, "removable_tool", {})
        assert final.status == "completed"

        mcp.local_provider.remove_tool("removable_tool")
        after = await get_task(mcp, final.task_id)

    assert after.status == "completed"
    assert after.result is not None
    assert after.result["content"][0]["text"] == "done"


class Unserializable(BaseModel):
    value: int

    @field_serializer("value")
    def _fail(self, value: int) -> int:
        raise ValueError("synthetic serialization failure")


async def test_result_conversion_failure_is_masked():
    mcp = FastMCP("mask-server", mask_error_details=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bad_result() -> Unserializable:
        return Unserializable(value=1)

    async with running_task_server(mcp):
        final = await run_task(mcp, "bad_result", {})

    assert final.status == "completed"
    assert final.error is None
    assert final.status_message is None
    assert final.result is not None
    assert final.result["isError"] is True
    text = final.result["content"][0]["text"]
    assert "synthetic serialization failure" not in text
    assert "Error calling tool 'bad_result'" in text
