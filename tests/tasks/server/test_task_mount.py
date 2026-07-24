"""SEP-2663 task execution through mounted servers (tools-only).

Verifies that background tasks work when a tool lives on a mounted child server:
the parent (which registers the tasks extension and owns the Docket) runs the
tool as a task, the worker resolves back to the child server, dependencies
resolve, and mode enforcement / metadata survive mounting. SEP-2663 is
tools-only, so the SEP-1686 prompt/resource mount cases are gone.

Two architectural notes vs. SEP-1686:
- The `tools/call` interceptor composes at the *registering* (parent/root)
  server's dispatch and short-circuits before delegating into a mounted child,
  so for a tasked call only the root's middleware wraps submission (the tool
  body runs later in the worker). Child/grandchild middleware do not wrap a
  tasked submission.
- Worker server resolution is single-level: a tool reached through nested mounts
  resolves to the outermost mounted child (the mount point the call arrived
  through), which still reaches deeper components via its own mounts.
"""

from __future__ import annotations

import asyncio
from typing import cast

import mcp_types as mt
import pytest
from docket import Docket
from fastmcp_tasks.dependencies import CurrentDocket
from mcp_types import Tool as MCPTool
from mcp_types import ToolExecution

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentFastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ClientFactoryT, ProxyTool
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    running_task_server,
    submit_task,
    wait_for_task,
)


@pytest.fixture(autouse=True)
def reset_docket_memory_server():
    """Reset the shared memory:// Docket server between tests for isolation."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


@pytest.fixture
def child_server() -> FastMCP:
    mcp = FastMCP("child-server")

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        return a * b

    @mcp.tool(task=False)
    async def sync_child_tool(message: str) -> str:
        return f"child sync: {message}"

    return mcp


@pytest.fixture
def parent_server(child_server: FastMCP) -> FastMCP:
    parent = FastMCP("parent-server")
    parent.add_extension(TasksExtension())

    @parent.tool(task=True)
    async def parent_tool(value: int) -> int:
        return value * 10

    parent.mount(child_server, namespace="child")
    return parent


class TestMountedToolTasks:
    async def test_mounted_tool_task_returns_correct_result(self, parent_server):
        async with running_task_server(parent_server):
            created = await submit_task(
                parent_server, "child_multiply", {"a": 8, "b": 9}
            )
            assert created.status == "working"
            final = await wait_for_task(parent_server, created.task_id)
            assert final.status == "completed"
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == 72

    async def test_mounted_and_parent_tasks_both_work(self, parent_server):
        async with running_task_server(parent_server):
            parent_created = await submit_task(
                parent_server, "parent_tool", {"value": 5}
            )
            child_created = await submit_task(
                parent_server, "child_multiply", {"a": 2, "b": 3}
            )
            parent_final = await wait_for_task(parent_server, parent_created.task_id)
            child_final = await wait_for_task(parent_server, child_created.task_id)
            assert parent_final.result is not None
            assert parent_final.result["structuredContent"]["result"] == 50
            assert child_final.result is not None
            assert child_final.result["structuredContent"]["result"] == 6

    async def test_sync_only_mounted_tool_runs_synchronously(self, parent_server):
        """A task=False mounted tool runs sync even when the client opts in."""
        async with running_task_server(parent_server):
            # Opting in on a forbidden tool must not task it.
            from tests.tasks.task_helpers import _opted_in_request

            with _opted_in_request("child_sync_child_tool", {"message": "hi"}, None):
                result = await parent_server.call_tool(
                    "child_sync_child_tool", {"message": "hi"}
                )
            assert not hasattr(result, "task_id")
            assert "child sync: hi" in result.content[0].text


class TestRemoteWorkerServerResolution:
    """A separate worker process re-resolves the owning child from the root.

    The in-process submission map is unreachable across processes, so the worker
    recovers the mounted child server from the snapshotted tool name instead of
    falling back to the root (which would break child-specific state/config).
    """

    async def test_resolve_owning_server_recovers_mounted_child(self, parent_server):
        import weakref

        from fastmcp_tasks.context import (
            TaskContextSnapshot,
            _resolve_owning_server,
        )

        from fastmcp.server.dependencies import _current_server

        child = await parent_server.get_tool("child_multiply")

        token = _current_server.set(weakref.ref(parent_server))
        try:
            snapshot = TaskContextSnapshot(owning_tool_name="child_multiply")
            resolved = await _resolve_owning_server(snapshot)
            assert resolved is child._server

            # A parent-owned (unmounted) tool resolves to None so the caller
            # falls back to the root, and a missing name is likewise None.
            assert (
                await _resolve_owning_server(
                    TaskContextSnapshot(owning_tool_name="parent_tool")
                )
                is None
            )
            assert (
                await _resolve_owning_server(
                    TaskContextSnapshot(owning_tool_name="does_not_exist")
                )
                is None
            )
            assert await _resolve_owning_server(TaskContextSnapshot()) is None
        finally:
            _current_server.reset(token)

    async def test_resolve_owning_server_respects_version(self):
        """Two versions of a mounted tool name resolve to their own child server."""
        import weakref

        from fastmcp_tasks.context import (
            TaskContextSnapshot,
            _resolve_owning_server,
        )

        from fastmcp.server.dependencies import _current_server

        child_v1 = FastMCP("child-v1")

        @child_v1.tool(name="calc", version="1.0", task=True)
        async def calc_v1() -> str:
            return "v1"

        child_v2 = FastMCP("child-v2")

        @child_v2.tool(name="calc", version="2.0", task=True)
        async def calc_v2() -> str:
            return "v2"

        parent = FastMCP("parent-versions")
        parent.add_extension(TasksExtension())
        parent.mount(child_v1)
        parent.mount(child_v2)

        token = _current_server.set(weakref.ref(parent))
        try:
            resolved_v1 = await _resolve_owning_server(
                TaskContextSnapshot(owning_tool_name="calc", owning_tool_version="1.0")
            )
            resolved_v2 = await _resolve_owning_server(
                TaskContextSnapshot(owning_tool_name="calc", owning_tool_version="2.0")
            )
            assert resolved_v1 is child_v1
            assert resolved_v2 is child_v2
        finally:
            _current_server.reset(token)


class TestMountedToolTasksNoPrefix:
    async def test_mounted_tool_without_prefix_works(self, child_server):
        parent = FastMCP("parent-no-prefix")
        parent.add_extension(TasksExtension())
        parent.mount(child_server)  # no prefix
        async with running_task_server(parent):
            final = await wait_for_task(
                parent,
                (await submit_task(parent, "multiply", {"a": 5, "b": 6})).task_id,
            )
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == 30


class TestMountedTaskDependencies:
    async def test_mounted_task_receives_docket_dependency(self):
        child = FastMCP("dep-child")

        @child.tool(task=True)
        async def tool_with_docket(docket: Docket = CurrentDocket()) -> str:
            return f"docket available: {docket is not None}"

        parent = FastMCP("dep-parent")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="child")

        async with running_task_server(parent):
            final = await wait_for_task(
                parent,
                (await submit_task(parent, "child_tool_with_docket", {})).task_id,
            )
            assert final.result is not None
            assert "docket available: True" in final.result["content"][0]["text"]


class TestMountedTaskServerContext:
    async def test_current_fastmcp_resolves_to_child_server(self):
        child = FastMCP("child")

        @child.tool(task=True)
        async def whoami(server: FastMCP = CurrentFastMCP()) -> str:
            return f"server name: {server.name}"

        parent = FastMCP("parent")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="child")

        async with running_task_server(parent):
            final = await wait_for_task(
                parent, (await submit_task(parent, "child_whoami", {})).task_id
            )
            assert final.result is not None
            assert "server name: child" in final.result["content"][0]["text"]

    async def test_context_fastmcp_resolves_to_child_server(self):
        child = FastMCP("child")

        @child.tool(task=True)
        async def whoami_ctx(ctx: Context) -> str:
            return f"context server: {ctx.fastmcp.name}"

        parent = FastMCP("parent")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="child")

        async with running_task_server(parent):
            final = await wait_for_task(
                parent, (await submit_task(parent, "child_whoami_ctx", {})).task_id
            )
            assert final.result is not None
            assert "context server: child" in final.result["content"][0]["text"]


class TestMultipleMounts:
    async def test_tasks_work_with_multiple_mounts(self):
        child1 = FastMCP("child1")
        child2 = FastMCP("child2")

        @child1.tool(task=True)
        async def add(a: int, b: int) -> int:
            return a + b

        @child2.tool(task=True)
        async def subtract(a: int, b: int) -> int:
            return a - b

        parent = FastMCP("multi-parent")
        parent.add_extension(TasksExtension())
        parent.mount(child1, namespace="math1")
        parent.mount(child2, namespace="math2")

        async with running_task_server(parent):
            r1 = await wait_for_task(
                parent,
                (await submit_task(parent, "math1_add", {"a": 10, "b": 5})).task_id,
            )
            r2 = await wait_for_task(
                parent,
                (
                    await submit_task(parent, "math2_subtract", {"a": 10, "b": 5})
                ).task_id,
            )
            assert r1.result is not None
            assert r1.result["structuredContent"]["result"] == 15
            assert r2.result is not None
            assert r2.result["structuredContent"]["result"] == 5

    async def test_same_function_names_do_not_collide(self):
        child1 = FastMCP("child1")
        child2 = FastMCP("child2")

        @child1.tool(task=True)
        async def process(value: int) -> int:
            return value * 2

        @child2.tool(task=True)
        async def process(value: int) -> int:  # noqa: F811
            return value * 3

        parent = FastMCP("parent")
        parent.add_extension(TasksExtension())
        parent.mount(child1, namespace="c1")
        parent.mount(child2, namespace="c2")

        async with running_task_server(parent):
            r1 = await wait_for_task(
                parent,
                (await submit_task(parent, "c1_process", {"value": 10})).task_id,
            )
            r2 = await wait_for_task(
                parent,
                (await submit_task(parent, "c2_process", {"value": 10})).task_id,
            )
            assert r1.result is not None
            assert r1.result["structuredContent"]["result"] == 20
            assert r2.result is not None
            assert r2.result["structuredContent"]["result"] == 30

    async def test_nested_mount_prefix_accumulation(self):
        grandchild = FastMCP("gc")
        child = FastMCP("child")
        parent = FastMCP("parent")
        parent.add_extension(TasksExtension())

        @grandchild.tool(task=True)
        async def deep_tool() -> str:
            return "deep"

        child.mount(grandchild, namespace="gc")
        parent.mount(child, namespace="child")

        async with running_task_server(parent):
            final = await wait_for_task(
                parent,
                (await submit_task(parent, "child_gc_deep_tool", {})).task_id,
            )
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == "deep"


class TestMountedTaskMetadata:
    async def test_mounted_tool_list_preserves_task_support_metadata(self):
        child = FastMCP("child")

        @child.tool(task=True)
        async def foo() -> dict[str, bool]:
            return {"ok": True}

        parent = FastMCP("parent")
        parent.mount(child)

        child_tool = next(t for t in await child.list_tools() if t.name == "foo")
        parent_tool = next(t for t in await parent.list_tools() if t.name == "foo")

        child_mcp = child_tool.to_mcp_tool(name=child_tool.name)
        parent_mcp = parent_tool.to_mcp_tool(name=parent_tool.name)
        assert child_mcp.execution is not None
        assert parent_mcp.execution is not None
        assert child_mcp.execution.task_support == "optional"
        assert parent_mcp.execution.task_support == "optional"

    async def test_proxy_tool_preserves_execution_metadata(self):
        mcp_tool = MCPTool(
            name="remote_task_tool",
            description="A remote tool that supports tasks",
            input_schema={"type": "object", "properties": {}},
            execution=ToolExecution(task_support="optional"),
        )
        proxy = ProxyTool.from_mcp_tool(cast(ClientFactoryT, lambda: None), mcp_tool)
        result = proxy.to_mcp_tool(name=proxy.name)
        assert result.execution is not None
        assert result.execution.task_support == "optional"


class TestMountedTaskConfigModes:
    @pytest.fixture
    def parent_with_modes(self) -> FastMCP:
        child = FastMCP("child-modes")

        @child.tool(task=TaskConfig(mode="optional"))
        async def optional_tool() -> str:
            return "optional result"

        @child.tool(task=TaskConfig(mode="required"))
        async def required_tool() -> str:
            return "required result"

        @child.tool(task=TaskConfig(mode="forbidden"))
        async def forbidden_tool() -> str:
            return "forbidden result"

        parent = FastMCP("parent-modes")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="child")
        return parent

    async def test_optional_mode_sync_through_mount(self, parent_with_modes):
        async with running_task_server(parent_with_modes):
            result = await call_tool_without_optin(
                parent_with_modes, "child_optional_tool", {}
            )
            assert "optional result" in result.content[0].text

    async def test_optional_mode_task_through_mount(self, parent_with_modes):
        async with running_task_server(parent_with_modes):
            final = await wait_for_task(
                parent_with_modes,
                (
                    await submit_task(parent_with_modes, "child_optional_tool", {})
                ).task_id,
            )
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == "optional result"

    async def test_required_mode_with_task_through_mount(self, parent_with_modes):
        async with running_task_server(parent_with_modes):
            final = await wait_for_task(
                parent_with_modes,
                (
                    await submit_task(parent_with_modes, "child_required_tool", {})
                ).task_id,
            )
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == "required result"

    async def test_required_mode_without_task_through_mount(self, parent_with_modes):
        from fastmcp_tasks.models import MISSING_REQUIRED_CLIENT_CAPABILITY
        from mcp.shared.exceptions import MCPError

        async with running_task_server(parent_with_modes):
            with pytest.raises(MCPError) as exc_info:
                await call_tool_without_optin(
                    parent_with_modes, "child_required_tool", {}
                )
            assert exc_info.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY

    async def test_forbidden_mode_sync_through_mount(self, parent_with_modes):
        async with running_task_server(parent_with_modes):
            result = await call_tool_without_optin(
                parent_with_modes, "child_forbidden_tool", {}
            )
            assert "forbidden result" in result.content[0].text


class ToolTracingMiddleware(Middleware):
    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


class TestMiddlewareWithMountedTasks:
    async def test_root_middleware_wraps_task_submission(self):
        """For a tasked call, the root's middleware wraps submission.

        The interceptor composes at the registering (parent) server and
        short-circuits before delegating into the mounted child, so child
        middleware does not wrap a tasked submission; the tool body runs later
        in the worker.
        """
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.tool(task=True)
        async def compute(x: int) -> int:
            calls.append("grandchild:tool")
            return x * 2

        grandchild.add_middleware(ToolTracingMiddleware("grandchild", calls))
        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ToolTracingMiddleware("child", calls))
        parent = FastMCP("Parent")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="c")
        parent.add_middleware(ToolTracingMiddleware("parent", calls))

        async with running_task_server(parent):
            created = await submit_task(parent, "c_gc_compute", {"x": 5})
            final = await wait_for_task(parent, created.task_id)
            assert final.result is not None
            assert final.result["structuredContent"]["result"] == 10

        assert calls == ["parent:before", "parent:after", "grandchild:tool"]


class TestMountedDocketOwnership:
    async def test_mounted_child_does_not_own_docket(self, parent_server, child_server):
        """The parent owns the Docket; the mounted child does not."""
        async with running_task_server(parent_server):
            assert parent_server.docket is not None
            assert child_server.docket is None


class TestSlowMountedTaskCancellation:
    async def test_cancel_mounted_task(self):
        child = FastMCP("child")
        release = asyncio.Event()

        @child.tool(task=True)
        async def slow() -> str:
            await release.wait()
            return "done"

        parent = FastMCP("parent")
        parent.add_extension(TasksExtension())
        parent.mount(child, namespace="child")

        from tests.tasks.task_helpers import cancel_task

        async with running_task_server(parent):
            created = await submit_task(parent, "child_slow", {})
            await cancel_task(parent, created.task_id)
            release.set()
            final = await wait_for_task(
                parent,
                created.task_id,
                target_states=frozenset({"cancelled", "completed"}),
            )
            assert final.status in {"cancelled", "completed"}
