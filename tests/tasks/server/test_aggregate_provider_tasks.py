"""AggregateProvider registers task components under the names they are called by."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.transforms import Namespace
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import run_task, running_task_server


def make_local() -> LocalProvider:
    local = LocalProvider()

    @local.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    @local.tool(task=TaskConfig(mode="forbidden"))
    async def sync_only(n: int) -> int:
        return n

    return local


async def test_get_tasks_without_transforms_is_unchanged():
    aggregate = AggregateProvider([make_local()])

    assert [c.key for c in await aggregate.get_tasks()] == ["tool:square@"]


async def test_get_tasks_applies_the_aggregates_own_transforms():
    aggregate = AggregateProvider([make_local()])
    aggregate.add_transform(Namespace("math"))
    mcp = FastMCP("demo", providers=[aggregate])

    tool = await mcp.get_tool("math_square")
    assert tool is not None
    assert [c.key for c in await mcp.get_tasks()] == [tool.key]
    assert tool.key == "tool:math_square@"


async def test_get_tasks_keeps_child_provider_transforms():
    local = make_local()
    local.add_transform(Namespace("inner"))
    aggregate = AggregateProvider([local])
    aggregate.add_transform(Namespace("outer"))

    assert [c.name for c in await aggregate.get_tasks()] == ["outer_inner_square"]


async def test_get_tasks_keeps_tools_hidden_by_a_catalog_transform():
    aggregate = AggregateProvider([make_local()])
    aggregate.add_transform(BM25SearchTransform())

    assert [c.name for c in await aggregate.get_tasks()] == ["square"]


async def test_namespaced_aggregate_tool_runs_as_a_background_task():
    aggregate = AggregateProvider([make_local()])
    aggregate.add_transform(Namespace("math"))
    mcp = FastMCP("demo", providers=[aggregate])
    mcp.add_extension(TasksExtension())

    async with running_task_server(mcp):
        final = await run_task(mcp, "math_square", {"n": 4})

    assert final.status == "completed"
