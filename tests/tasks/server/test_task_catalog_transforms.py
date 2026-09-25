"""Task-enabled tools hidden by a catalog transform stay registered with Docket.

Search and CodeMode replace the tool listing with synthetic discovery tools, but
the tools they hide remain callable. Registration has to follow what is
callable, not what is listed.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from fastmcp_tasks.client import call_tool_task

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.transforms import Namespace
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from fastmcp.tools.base import Tool
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


async def test_task_tool_added_by_catalog_transform_is_registered():
    async def synthetic_slow(n: int) -> int:
        return n

    class AddsTaskTool(CatalogTransform):
        async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
            return [Tool.from_function(synthetic_slow, task=True)]

    mcp = make_server()
    mcp.add_transform(AddsTaskTool())

    assert sorted(c.name for c in await mcp.get_tasks()) == [
        "slow_thing",
        "synthetic_slow",
    ]


class TestNestedCallsRunInForeground:
    """A client's tasks opt-in covers the tool it called, not tools that tool calls."""

    async def test_search_proxy_returns_task_tool_result(self):
        mcp = make_server()
        mcp.add_transform(BM25SearchTransform())

        async with Client(mcp) as client:
            result = await client.call_tool(
                "call_tool", {"name": "slow_thing", "arguments": {"n": 2}}
            )

        assert result.structured_content == {"n": 2}

    async def test_code_mode_returns_task_tool_result(self):
        mcp = make_server()
        mcp.add_transform(CodeMode())

        async with Client(mcp) as client:
            result = await client.call_tool(
                "execute",
                {"code": "return await call_tool('slow_thing', {'n': 4})"},
            )

        assert "4" in result.content[0].text

    async def test_tool_calling_task_tool_gets_its_result(self):
        mcp = make_server()

        @mcp.tool
        async def outer(ctx: Context) -> dict:
            result = await ctx.fastmcp.call_tool("slow_thing", {"n": 5})
            return result.structured_content or {}

        async with Client(mcp) as client:
            result = await client.call_tool("outer", {})

        assert result.structured_content == {"n": 5}

    async def test_required_task_tool_refuses_nested_call(self):
        mcp = make_server()

        @mcp.tool(task=TaskConfig(mode="required"))
        async def must_task(n: int) -> dict:
            return {"n": n}

        mcp.add_transform(BM25SearchTransform())

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="only runs as a background task"):
                await client.call_tool(
                    "call_tool", {"name": "must_task", "arguments": {"n": 1}}
                )

    async def test_resource_and_prompt_get_task_tool_result(self):
        mcp = make_server()

        @mcp.resource("data://thing")
        async def thing(ctx: Context) -> dict:
            result = await ctx.fastmcp.call_tool("slow_thing", {"n": 6})
            return result.structured_content or {}

        @mcp.prompt
        async def ask(ctx: Context) -> str:
            result = await ctx.fastmcp.call_tool("slow_thing", {"n": 7})
            return str(result.structured_content)

        async with Client(mcp) as client:
            resource = await client.read_resource("data://thing")
            prompt = await client.get_prompt("ask")

        assert "6" in resource[0].text
        assert "7" in prompt.messages[0].content.text

    async def test_in_process_client_inside_tool_can_still_task(self):
        other = make_server()
        front = FastMCP("front")

        @front.tool
        async def relay() -> str:
            async with Client(other) as inner:
                task = await call_tool_task(inner, "slow_thing", {"n": 8})
                return task.task_id

        async with Client(front) as client:
            result = await client.call_tool("relay", {})

        assert result.data
