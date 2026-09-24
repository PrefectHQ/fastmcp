"""Task-enabled tools hidden by a catalog transform stay registered with Docket.

Search and CodeMode replace the tool listing with synthetic discovery tools, but
the tools they hide remain callable. Registration has to follow what is
callable, not what is listed.
"""

from __future__ import annotations

import pytest

from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.transforms import Namespace
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import run_task, running_task_server


def make_server() -> FastMCP:
    mcp = FastMCP("catalog-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=TaskConfig(mode="optional"))
    async def slow_thing(n: int) -> dict:
        return {"n": n}

    return mcp


@pytest.mark.parametrize(
    "transform",
    [BM25SearchTransform(), RegexSearchTransform(), CodeMode()],
    ids=["bm25", "regex", "code_mode"],
)
async def test_server_level_catalog_transform_keeps_hidden_task_tool(transform):
    mcp = make_server()
    mcp.add_transform(transform)

    assert [c.name for c in await mcp.get_tasks()] == ["slow_thing"]

    async with running_task_server(mcp):
        final = await run_task(mcp, "slow_thing", {"n": 2})
        assert final.status == "completed"

    async with Client(mcp) as client:
        result = await client.call_tool("slow_thing", {"n": 1})
    assert result.data == {"n": 1}


async def test_provider_level_search_keeps_hidden_task_tool():
    mcp = make_server()
    mcp.providers[0].add_transform(BM25SearchTransform())

    assert [c.name for c in await mcp.get_tasks()] == ["slow_thing"]


async def test_mounted_child_search_keeps_hidden_task_tool():
    child = FastMCP("child")

    @child.tool(task=True)
    async def slow_thing(n: int) -> int:
        return n

    child.add_transform(BM25SearchTransform())
    parent = FastMCP("parent")
    parent.add_extension(TasksExtension())
    parent.mount(child, namespace="child")

    assert [c.name for c in await parent.get_tasks()] == ["child_slow_thing"]

    async with running_task_server(parent):
        final = await run_task(parent, "child_slow_thing", {"n": 3})
        assert final.status == "completed"


async def test_renaming_transforms_still_apply_alongside_search():
    mcp = make_server()
    mcp.add_transform(Namespace("api"))
    mcp.add_transform(BM25SearchTransform())

    assert [c.name for c in await mcp.get_tasks()] == ["api_slow_thing"]


async def test_search_listing_still_hides_task_tool():
    mcp = make_server()
    mcp.add_transform(BM25SearchTransform())

    names = [t.name for t in await mcp.list_tools()]
    assert "slow_thing" not in names
