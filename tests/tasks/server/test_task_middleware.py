"""Task-augmented calls flow through ToolResult-inspecting middleware safely.

A `tools/call` the tasks extension turns into a background task returns a
`CreateTaskResult` up through the middleware chain. Middleware that post-process
a `ToolResult` (response caching, response limiting) must pass that
acknowledgement through untouched rather than crash after the task is enqueued.
"""

from __future__ import annotations

from fastmcp_tasks.models import CreateTaskResult

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.tool_injection import ToolInjectionMiddleware
from fastmcp.tools.base import Tool
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import running_task_server, submit_task, wait_for_task


async def test_tasked_call_survives_result_inspecting_middleware():
    mcp = FastMCP("tasks-mw")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(ResponseCachingMiddleware())
    mcp.add_middleware(ResponseLimitingMiddleware(max_size=1_000_000))

    @mcp.tool(task=True)
    async def crunch(n: int) -> int:
        return n * n

    async with running_task_server(mcp):
        created = await submit_task(mcp, "crunch", {"n": 9})
        assert isinstance(created, CreateTaskResult)
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 81}


async def square(n: int) -> int:
    return n * n


async def test_injected_task_tool_runs_as_task():
    mcp = FastMCP("tasks-injected")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(ToolInjectionMiddleware([Tool.from_function(square, task=True)]))

    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 7})
        assert isinstance(created, CreateTaskResult)
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 49}


async def test_background_task_runs_the_injected_tool_when_its_name_is_shared():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-shared-name")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(
        ToolInjectionMiddleware(
            [Tool.from_function(injected_report, name="report", task=True)]
        )
    )

    @mcp.tool(task=True)
    async def report(n: int) -> str:
        return f"registered {n}"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "report", {"n": 3})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "injected 3"}


async def test_background_task_runs_the_injected_tool_when_signatures_differ():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-shared-name-signatures")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(
        ToolInjectionMiddleware(
            [Tool.from_function(injected_report, name="report", task=True)]
        )
    )

    @mcp.tool(task=True)
    async def report(label: str, flag: bool) -> str:
        return f"registered {label} {flag}"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "report", {"n": 5})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "injected 5"}


async def test_background_task_runs_the_injected_tool_when_registered_tool_is_not_task_capable():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-shared-name-forbidden")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(
        ToolInjectionMiddleware(
            [Tool.from_function(injected_report, name="report", task=True)]
        )
    )

    @mcp.tool(task=False)
    async def report(n: int) -> str:
        return f"registered {n}"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "report", {"n": 2})
        final = await wait_for_task(mcp, created.task_id)

    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "injected 2"}


async def test_task_discovery_lists_one_tool_for_a_shared_name():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-shared-name-discovery")
    mcp.add_extension(TasksExtension())
    injected = Tool.from_function(injected_report, name="report", task=True)
    mcp.add_middleware(ToolInjectionMiddleware([injected]))

    @mcp.tool(task=True)
    async def report(n: int) -> str:
        return f"registered {n}"

    @mcp.tool(task=True)
    async def other(n: int) -> str:
        return f"other {n}"

    components = await mcp.get_tasks()
    reports = [c for c in components if c.name == "report"]

    assert reports == [injected]
    assert {c.name for c in components} == {"report", "other"}


async def test_mounted_tool_shadowed_by_an_injected_tool_is_not_registered_as_a_task():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    child = FastMCP("child")

    @child.tool(task=True)
    async def report(n: int) -> str:
        return f"mounted {n}"

    @child.tool(task=True)
    async def summary(n: int) -> str:
        return f"summary {n}"

    mcp = FastMCP("tasks-shared-name-mounted")
    mcp.add_extension(TasksExtension())
    mcp.mount(child, namespace="child")
    injected = Tool.from_function(injected_report, name="child_report", task=True)
    mcp.add_middleware(ToolInjectionMiddleware([injected]))

    components = await mcp.get_tasks()
    assert [c for c in components if c.name == "child_report"] == [injected]
    assert "child_summary" in {c.name for c in components}

    async with running_task_server(mcp):
        shadowed = await submit_task(mcp, "child_report", {"n": 4})
        unshadowed = await submit_task(mcp, "child_summary", {"n": 4})
        shadowed_final = await wait_for_task(mcp, shadowed.task_id)
        unshadowed_final = await wait_for_task(mcp, unshadowed.task_id)

    assert shadowed_final.result is not None
    assert shadowed_final.result["structuredContent"] == {"result": "injected 4"}
    assert unshadowed_final.result is not None
    assert unshadowed_final.result["structuredContent"] == {"result": "summary 4"}


async def test_task_tools_with_distinct_names_all_run_as_background_tasks():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-distinct-names")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(
        ToolInjectionMiddleware(
            [Tool.from_function(injected_report, name="injected_only", task=True)]
        )
    )

    @mcp.tool(task=True)
    async def registered_only(n: int) -> str:
        return f"registered {n}"

    async with running_task_server(mcp):
        first = await submit_task(mcp, "injected_only", {"n": 1})
        second = await submit_task(mcp, "registered_only", {"n": 2})
        first_final = await wait_for_task(mcp, first.task_id)
        second_final = await wait_for_task(mcp, second.task_id)

    assert first_final.result is not None
    assert first_final.result["structuredContent"] == {"result": "injected 1"}
    assert second_final.result is not None
    assert second_final.result["structuredContent"] == {"result": "registered 2"}


async def test_task_discovery_skips_a_registered_tool_shadowed_by_a_synchronous_injected_tool():
    async def injected_report(n: int) -> str:
        return f"injected {n}"

    mcp = FastMCP("tasks-shared-name-synchronous")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(
        ToolInjectionMiddleware(
            [Tool.from_function(injected_report, name="report", task=False)]
        )
    )

    @mcp.tool(task=True)
    async def report(n: int) -> str:
        return f"registered {n}"

    components = await mcp.get_tasks()

    assert [c.name for c in components] == []
