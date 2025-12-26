"""
Tests for MCP SEP-1686 task protocol support through mounted servers.

Verifies that tasks work seamlessly when calling tools/prompts/resources
on mounted child servers through a parent server.
"""

import asyncio

import mcp.types as mt
import pytest
from docket import Docket

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.prompts.prompt import PromptResult
from fastmcp.resources.resource import ResourceResult
from fastmcp.server.dependencies import CurrentDocket, CurrentFastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.tasks import TaskConfig
from fastmcp.tools.tool import ToolResult


@pytest.fixture(autouse=True)
def reset_docket_memory_server():
    """Reset the shared Docket memory server between tests.

    Docket uses a class-level FakeServer instance for memory:// URLs which
    persists between tests, causing test isolation issues. This fixture
    clears that shared state before each test.
    """
    # Clear the shared FakeServer before each test
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    # Clean up after test as well
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


@pytest.fixture
def child_server():
    """Create a child server with task-enabled components."""
    mcp = FastMCP("child-server")

    @mcp.tool(task=True)
    async def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    @mcp.tool(task=True)
    async def slow_child_tool(duration: float = 0.1) -> str:
        """A child tool that takes time to execute."""
        await asyncio.sleep(duration)
        return "child completed"

    @mcp.tool(task=False)
    async def sync_child_tool(message: str) -> str:
        """Child tool that only supports synchronous execution."""
        return f"child sync: {message}"

    @mcp.prompt(task=True)
    async def child_prompt(topic: str) -> str:
        """A child prompt that can execute as a task."""
        return f"Here is information about {topic} from the child server."

    @mcp.resource("child://data.txt", task=True)
    async def child_resource() -> str:
        """A child resource that can be read as a task."""
        return "Data from child server"

    @mcp.resource("child://item/{item_id}.json", task=True)
    async def child_item_resource(item_id: str) -> str:
        """A child resource template that can execute as a task."""
        return f'{{"itemId": "{item_id}", "source": "child"}}'

    return mcp


@pytest.fixture
def parent_server(child_server):
    """Create a parent server with the child mounted."""
    parent = FastMCP("parent-server")

    @parent.tool(task=True)
    async def parent_tool(value: int) -> int:
        """A tool on the parent server."""
        return value * 10

    # Mount child with prefix
    parent.mount(child_server, namespace="child")

    return parent


@pytest.fixture
def parent_server_no_prefix(child_server):
    """Create a parent server with child mounted without prefix."""
    parent = FastMCP("parent-no-prefix")
    parent.mount(child_server)  # No prefix
    return parent


class TestMountedToolTasks:
    """Test task execution for mounted tools."""

    async def test_mounted_tool_task_returns_task_object(self, parent_server):
        """Mounted tool called with task=True returns a task object."""
        async with Client(parent_server) as client:
            # Tool name is prefixed: child_multiply
            task = await client.call_tool("child_multiply", {"a": 6, "b": 7}, task=True)

            assert task is not None
            assert hasattr(task, "task_id")
            assert isinstance(task.task_id, str)
            assert len(task.task_id) > 0

    async def test_mounted_tool_task_executes_in_background(self, parent_server):
        """Mounted tool task executes in background."""
        async with Client(parent_server) as client:
            task = await client.call_tool("child_multiply", {"a": 3, "b": 4}, task=True)

            # Should execute in background
            assert not task.returned_immediately

    async def test_mounted_tool_task_returns_correct_result(
        self, parent_server: FastMCP
    ):
        """Mounted tool task returns correct result."""
        async with Client(parent_server) as client:
            task = await client.call_tool("child_multiply", {"a": 8, "b": 9}, task=True)

            result = await task.result()
            assert result.data == 72

    async def test_mounted_tool_task_status(self, parent_server):
        """Can poll task status for mounted tool."""
        async with Client(parent_server) as client:
            task = await client.call_tool(
                "child_slow_child_tool", {"duration": 0.5}, task=True
            )

            # Check status while running
            status = await task.status()
            assert status.status in ["working", "completed"]

            # Wait for completion
            await task.wait(timeout=2.0)

            # Check status after completion
            status = await task.status()
            assert status.status == "completed"

    async def test_mounted_tool_task_cancellation(self, parent_server):
        """Can cancel a mounted tool task."""
        async with Client(parent_server) as client:
            task = await client.call_tool(
                "child_slow_child_tool", {"duration": 10.0}, task=True
            )

            # Let it start
            await asyncio.sleep(0.1)

            # Cancel the task
            await task.cancel()

            # Check status
            status = await task.status()
            assert status.status == "cancelled"

    async def test_graceful_degradation_sync_mounted_tool(self, parent_server):
        """Sync-only mounted tool returns error with task=True."""
        async with Client(parent_server) as client:
            task = await client.call_tool(
                "child_sync_child_tool", {"message": "hello"}, task=True
            )

            # Should return immediately with an error
            assert task.returned_immediately

            result = await task.result()
            assert result.is_error

    async def test_parent_and_mounted_tools_both_work(self, parent_server):
        """Both parent and mounted tools work as tasks."""
        async with Client(parent_server) as client:
            # Parent tool
            parent_task = await client.call_tool("parent_tool", {"value": 5}, task=True)
            # Mounted tool
            child_task = await client.call_tool(
                "child_multiply", {"a": 2, "b": 3}, task=True
            )

            parent_result = await parent_task.result()
            child_result = await child_task.result()

            assert parent_result.data == 50
            assert child_result.data == 6


class TestMountedToolTasksNoPrefix:
    """Test task execution for mounted tools without prefix."""

    async def test_mounted_tool_without_prefix_task_works(
        self, parent_server_no_prefix
    ):
        """Mounted tool without prefix works as task."""
        async with Client(parent_server_no_prefix) as client:
            # No prefix, so tool keeps original name
            task = await client.call_tool("multiply", {"a": 5, "b": 6}, task=True)

            assert not task.returned_immediately

            result = await task.result()
            assert result.data == 30


class TestMountedPromptTasks:
    """Test task execution for mounted prompts."""

    async def test_mounted_prompt_task_returns_task_object(self, parent_server):
        """Mounted prompt called with task=True returns a task object."""
        async with Client(parent_server) as client:
            # Prompt name is prefixed: child_child_prompt
            task = await client.get_prompt(
                "child_child_prompt", {"topic": "FastMCP"}, task=True
            )

            assert task is not None
            assert hasattr(task, "task_id")
            assert isinstance(task.task_id, str)

    async def test_mounted_prompt_task_executes_in_background(self, parent_server):
        """Mounted prompt task executes in background."""
        async with Client(parent_server) as client:
            task = await client.get_prompt(
                "child_child_prompt", {"topic": "testing"}, task=True
            )

            assert not task.returned_immediately

    async def test_mounted_prompt_task_returns_correct_result(
        self, parent_server: FastMCP
    ):
        """Mounted prompt task returns correct result."""
        async with Client(parent_server) as client:
            task = await client.get_prompt(
                "child_child_prompt", {"topic": "MCP protocol"}, task=True
            )

            result = await task.result()
            assert "MCP protocol" in result.messages[0].content.text
            assert "child server" in result.messages[0].content.text


class TestMountedResourceTasks:
    """Test task execution for mounted resources."""

    async def test_mounted_resource_task_returns_task_object(self, parent_server):
        """Mounted resource read with task=True returns a task object."""
        async with Client(parent_server) as client:
            # Resource URI is prefixed: child://child/data.txt
            task = await client.read_resource("child://child/data.txt", task=True)

            assert task is not None
            assert hasattr(task, "task_id")
            assert isinstance(task.task_id, str)

    async def test_mounted_resource_task_executes_in_background(self, parent_server):
        """Mounted resource task executes in background."""
        async with Client(parent_server) as client:
            task = await client.read_resource("child://child/data.txt", task=True)

            assert not task.returned_immediately

    async def test_mounted_resource_task_returns_correct_result(self, parent_server):
        """Mounted resource task returns correct result."""
        async with Client(parent_server) as client:
            task = await client.read_resource("child://child/data.txt", task=True)

            result = await task.result()
            assert len(result) > 0
            assert "Data from child server" in result[0].text

    async def test_mounted_resource_template_task(self, parent_server):
        """Mounted resource template with task=True works."""
        async with Client(parent_server) as client:
            task = await client.read_resource("child://child/item/99.json", task=True)

            assert not task.returned_immediately

            result = await task.result()
            assert '"itemId": "99"' in result[0].text
            assert '"source": "child"' in result[0].text


class TestMountedTaskDependencies:
    """Test that dependencies work correctly in mounted task execution."""

    async def test_mounted_task_receives_docket_dependency(self):
        """Mounted tool task receives CurrentDocket dependency."""
        child = FastMCP("dep-child")
        received_docket = []

        @child.tool(task=True)
        async def tool_with_docket(docket: CurrentDocket = CurrentDocket()) -> str:  # type: ignore[invalid-type-form]
            received_docket.append(docket)
            return f"docket available: {docket is not None}"

        parent = FastMCP("dep-parent")
        parent.mount(child, namespace="child")

        async with Client(parent) as client:
            task = await client.call_tool("child_tool_with_docket", {}, task=True)
            result = await task.result()

            assert "docket available: True" in str(result)
            assert len(received_docket) == 1
            assert received_docket[0] is not None

    async def test_mounted_task_receives_server_dependency(self):
        """Mounted tool task receives CurrentFastMCP dependency."""
        child = FastMCP("server-dep-child")
        received_server = []

        @child.tool(task=True)
        async def tool_with_server(server: CurrentFastMCP = CurrentFastMCP()) -> str:  # type: ignore[invalid-type-form]
            received_server.append(server)
            return f"server name: {server.name}"

        parent = FastMCP("server-dep-parent")
        parent.mount(child, namespace="child")

        async with Client(parent) as client:
            task = await client.call_tool("child_tool_with_server", {}, task=True)
            await task.result()

            # The server should be the child server since that's where the tool is defined
            assert len(received_server) == 1
            # Note: It might be parent or child depending on implementation
            assert received_server[0] is not None


class TestMultipleMounts:
    """Test tasks with multiple mounted servers."""

    async def test_tasks_work_with_multiple_mounts(self):
        """Tasks work correctly with multiple mounted servers."""
        child1 = FastMCP("child1")
        child2 = FastMCP("child2")

        @child1.tool(task=True)
        async def add(a: int, b: int) -> int:
            return a + b

        @child2.tool(task=True)
        async def subtract(a: int, b: int) -> int:
            return a - b

        parent = FastMCP("multi-parent")
        parent.mount(child1, namespace="math1")
        parent.mount(child2, namespace="math2")

        async with Client(parent) as client:
            task1 = await client.call_tool("math1_add", {"a": 10, "b": 5}, task=True)
            task2 = await client.call_tool(
                "math2_subtract", {"a": 10, "b": 5}, task=True
            )

            result1 = await task1.result()
            result2 = await task2.result()

            assert result1.data == 15
            assert result2.data == 5


class TestMountedFunctionNameCollisions:
    """Test task execution when mounted servers have identically-named functions."""

    async def test_multiple_mounts_with_same_function_names(self):
        """Two mounted servers with identically-named functions don't collide."""
        child1 = FastMCP("child1")
        child2 = FastMCP("child2")

        @child1.tool(task=True)
        async def process(value: int) -> int:
            return value * 2  # Double

        @child2.tool(task=True)
        async def process(value: int) -> int:  # noqa: F811
            return value * 3  # Triple

        parent = FastMCP("parent")
        parent.mount(child1, namespace="c1")
        parent.mount(child2, namespace="c2")

        async with Client(parent) as client:
            # Both should execute their own implementation
            task1 = await client.call_tool("c1_process", {"value": 10}, task=True)
            task2 = await client.call_tool("c2_process", {"value": 10}, task=True)

            result1 = await task1.result()
            result2 = await task2.result()

            assert result1.data == 20  # child1's process (doubles)
            assert result2.data == 30  # child2's process (triples)

    async def test_no_prefix_mount_collision(self):
        """No-prefix mounts with same tool name - last mount wins."""
        child1 = FastMCP("child1")
        child2 = FastMCP("child2")

        @child1.tool(task=True)
        async def process(value: int) -> int:
            return value * 2

        @child2.tool(task=True)
        async def process(value: int) -> int:  # noqa: F811
            return value * 3

        parent = FastMCP("parent")
        parent.mount(child1)  # No prefix
        parent.mount(child2)  # No prefix - overwrites child1's "process"

        async with Client(parent) as client:
            # Last mount wins - child2's process should execute
            task = await client.call_tool("process", {"value": 10}, task=True)
            result = await task.result()
            assert result.data == 30  # child2's process (triples)

    async def test_nested_mount_prefix_accumulation(self):
        """Nested mounts accumulate prefixes correctly for tasks."""
        grandchild = FastMCP("gc")
        child = FastMCP("child")
        parent = FastMCP("parent")

        @grandchild.tool(task=True)
        async def deep_tool() -> str:
            return "deep"

        child.mount(grandchild, namespace="gc")
        parent.mount(child, namespace="child")

        async with Client(parent) as client:
            # Tool should be accessible and execute correctly
            task = await client.call_tool("child_gc_deep_tool", {}, task=True)
            result = await task.result()
            assert result.data == "deep"


class TestMountedTaskList:
    """Test task listing with mounted servers."""

    async def test_list_tasks_includes_mounted_tasks(self, parent_server):
        """Task list includes tasks from mounted server tools."""
        async with Client(parent_server) as client:
            # Create tasks on both parent and mounted tools
            parent_task = await client.call_tool("parent_tool", {"value": 1}, task=True)
            child_task = await client.call_tool(
                "child_multiply", {"a": 2, "b": 2}, task=True
            )

            # Wait for completion
            await parent_task.wait(timeout=2.0)
            await child_task.wait(timeout=2.0)

            # List all tasks - returns dict with "tasks" key
            tasks_response = await client.list_tasks()

            task_ids = [t["taskId"] for t in tasks_response["tasks"]]
            assert parent_task.task_id in task_ids
            assert child_task.task_id in task_ids


class TestMountedTaskConfigModes:
    """Test TaskConfig mode enforcement for mounted tools."""

    @pytest.fixture
    def child_with_modes(self):
        """Create a child server with tools in all three TaskConfig modes."""
        mcp = FastMCP("child-modes", tasks=False)

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def optional_tool() -> str:
            """Tool that supports both sync and task execution."""
            return "optional result"

        @mcp.tool(task=TaskConfig(mode="required"))
        async def required_tool() -> str:
            """Tool that requires task execution."""
            return "required result"

        @mcp.tool(task=TaskConfig(mode="forbidden"))
        async def forbidden_tool() -> str:
            """Tool that forbids task execution."""
            return "forbidden result"

        return mcp

    @pytest.fixture
    def parent_with_modes(self, child_with_modes):
        """Create a parent server with the child mounted."""
        parent = FastMCP("parent-modes")
        parent.mount(child_with_modes, namespace="child")
        return parent

    async def test_optional_mode_sync_through_mount(self, parent_with_modes):
        """Optional mode tool works without task through mount."""
        async with Client(parent_with_modes) as client:
            result = await client.call_tool("child_optional_tool", {})
            assert "optional result" in str(result)

    async def test_optional_mode_task_through_mount(self, parent_with_modes):
        """Optional mode tool works with task through mount."""
        async with Client(parent_with_modes) as client:
            task = await client.call_tool("child_optional_tool", {}, task=True)
            assert task is not None
            result = await task.result()
            assert result.data == "optional result"

    async def test_required_mode_with_task_through_mount(self, parent_with_modes):
        """Required mode tool succeeds with task through mount."""
        async with Client(parent_with_modes) as client:
            task = await client.call_tool("child_required_tool", {}, task=True)
            assert task is not None
            result = await task.result()
            assert result.data == "required result"

    async def test_required_mode_without_task_through_mount(self, parent_with_modes):
        """Required mode tool errors without task through mount."""
        from fastmcp.exceptions import ToolError

        async with Client(parent_with_modes) as client:
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("child_required_tool", {})

            assert "requires task-augmented execution" in str(exc_info.value)

    async def test_forbidden_mode_sync_through_mount(self, parent_with_modes):
        """Forbidden mode tool works without task through mount."""
        async with Client(parent_with_modes) as client:
            result = await client.call_tool("child_forbidden_tool", {})
            assert "forbidden result" in str(result)

    async def test_forbidden_mode_with_task_through_mount(self, parent_with_modes):
        """Forbidden mode tool degrades gracefully with task through mount."""
        async with Client(parent_with_modes) as client:
            task = await client.call_tool("child_forbidden_tool", {}, task=True)

            # Should return immediately (graceful degradation)
            assert task.returned_immediately

            result = await task.result()
            # Result is available but may indicate error or sync execution
            assert result is not None


# -----------------------------------------------------------------------------
# Middleware classes for tracing tests
# -----------------------------------------------------------------------------


class ToolTracingMiddleware(Middleware):
    """Middleware that traces tool calls."""

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


class ResourceTracingMiddleware(Middleware):
    """Middleware that traces resource reads."""

    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


class PromptTracingMiddleware(Middleware):
    """Middleware that traces prompt gets."""

    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


class TestMiddlewareWithMountedTasks:
    """Test that middleware runs at all levels when executing background tasks.

    For background tasks, middleware runs during task submission (wrapping the MCP
    request handling that queues to Docket). The actual function execution happens
    later in the Docket worker, after the middleware chain completes.
    """

    async def test_tool_middleware_runs_with_background_task(self):
        """Middleware runs at parent, child, and grandchild levels for tool tasks."""
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
        parent.mount(child, namespace="c")
        parent.add_middleware(ToolTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            task = await client.call_tool("c_gc_compute", {"x": 5}, task=True)
            result = await task.result()
            assert result.data == 10

        # Middleware runs during task submission (before/after queuing to Docket)
        # Function executes later in Docket worker
        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:after",
            "child:after",
            "parent:after",
            "grandchild:tool",  # Executes in Docket after middleware completes
        ]

    async def test_resource_middleware_runs_with_background_task(self):
        """Middleware runs at parent, child, and grandchild levels for resource tasks."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.resource("data://value", task=True)
        async def get_data() -> str:
            calls.append("grandchild:resource")
            return "result"

        grandchild.add_middleware(ResourceTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ResourceTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(ResourceTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            task = await client.read_resource("data://c/gc/value", task=True)
            result = await task.result()
            assert result[0].text == "result"

        # Middleware runs during task submission, function in Docket
        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:after",
            "child:after",
            "parent:after",
            "grandchild:resource",
        ]

    async def test_prompt_middleware_runs_with_background_task(self):
        """Middleware runs at parent, child, and grandchild levels for prompt tasks."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.prompt(task=True)
        async def greet(name: str) -> str:
            calls.append("grandchild:prompt")
            return f"Hello, {name}!"

        grandchild.add_middleware(PromptTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(PromptTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(PromptTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            task = await client.get_prompt("c_gc_greet", {"name": "World"}, task=True)
            result = await task.result()
            assert result.messages[0].content.text == "Hello, World!"

        # Middleware runs during task submission, function in Docket
        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:after",
            "child:after",
            "parent:after",
            "grandchild:prompt",
        ]

    async def test_resource_template_middleware_runs_with_background_task(self):
        """Middleware runs at all levels for resource template tasks."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.resource("item://{id}", task=True)
        async def get_item(id: str) -> str:
            calls.append("grandchild:template")
            return f"item-{id}"

        grandchild.add_middleware(ResourceTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ResourceTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(ResourceTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            task = await client.read_resource("item://c/gc/42", task=True)
            result = await task.result()
            assert result[0].text == "item-42"

        # Middleware runs during task submission, function in Docket
        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:after",
            "child:after",
            "parent:after",
            "grandchild:template",
        ]


class TestMountedTasksWithTaskMetaParameter:
    """Test mounted components called directly with task_meta parameter.

    These tests verify the programmatic API where server.call_tool() or
    server.read_resource() is called with an explicit task_meta parameter,
    as opposed to using the Client with task=True.

    Direct server calls require a running server context, so we use an outer
    tool that makes the direct call internally.
    """

    async def test_mounted_tool_with_task_meta_creates_task(self):
        """Mounted tool called with task_meta returns CreateTaskResult."""
        from fastmcp.server.tasks.config import TaskMeta

        child = FastMCP("Child")

        @child.tool(task=True)
        async def add(a: int, b: int) -> int:
            return a + b

        parent = FastMCP("Parent")
        parent.mount(child, namespace="child")

        @parent.tool
        async def outer() -> str:
            # Direct call with task_meta from within server context
            result = await parent.call_tool(
                "child_add", {"a": 2, "b": 3}, task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_mounted_resource_with_task_meta_creates_task(self):
        """Mounted resource called with task_meta returns CreateTaskResult."""
        from fastmcp.server.tasks.config import TaskMeta

        child = FastMCP("Child")

        @child.resource("data://info", task=True)
        async def get_info() -> str:
            return "child info"

        parent = FastMCP("Parent")
        parent.mount(child, namespace="child")

        @parent.tool
        async def outer() -> str:
            result = await parent.read_resource(
                "data://child/info", task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_mounted_template_with_task_meta_creates_task(self):
        """Mounted resource template with task_meta returns CreateTaskResult."""
        from fastmcp.server.tasks.config import TaskMeta

        child = FastMCP("Child")

        @child.resource("item://{id}", task=True)
        async def get_item(id: str) -> str:
            return f"item-{id}"

        parent = FastMCP("Parent")
        parent.mount(child, namespace="child")

        @parent.tool
        async def outer() -> str:
            result = await parent.read_resource(
                "item://child/42", task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_deeply_nested_tool_with_task_meta(self):
        """Three-level nested tool works with task_meta."""
        from fastmcp.server.tasks.config import TaskMeta

        grandchild = FastMCP("Grandchild")

        @grandchild.tool(task=True)
        async def compute(n: int) -> int:
            return n * 3

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")

        @parent.tool
        async def outer() -> str:
            result = await parent.call_tool(
                "c_gc_compute", {"n": 7}, task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_deeply_nested_template_with_task_meta(self):
        """Three-level nested template works with task_meta."""
        from fastmcp.server.tasks.config import TaskMeta

        grandchild = FastMCP("Grandchild")

        @grandchild.resource("doc://{name}", task=True)
        async def get_doc(name: str) -> str:
            return f"doc: {name}"

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")

        @parent.tool
        async def outer() -> str:
            result = await parent.read_resource(
                "doc://c/gc/readme", task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_mounted_prompt_with_task_meta_creates_task(self):
        """Mounted prompt called with task_meta returns CreateTaskResult."""
        from fastmcp.server.tasks.config import TaskMeta

        child = FastMCP("Child")

        @child.prompt(task=True)
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        parent = FastMCP("Parent")
        parent.mount(child, namespace="child")

        @parent.tool
        async def outer() -> str:
            result = await parent.render_prompt(
                "child_greet", {"name": "World"}, task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)

    async def test_deeply_nested_prompt_with_task_meta(self):
        """Three-level nested prompt works with task_meta."""
        from fastmcp.server.tasks.config import TaskMeta

        grandchild = FastMCP("Grandchild")

        @grandchild.prompt(task=True)
        async def describe(topic: str) -> str:
            return f"Information about {topic}"

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")

        @parent.tool
        async def outer() -> str:
            result = await parent.render_prompt(
                "c_gc_describe", {"topic": "FastMCP"}, task_meta=TaskMeta(ttl=300)
            )
            return f"task:{result.task.taskId}"

        async with Client(parent) as client:
            result = await client.call_tool("outer", {})
            assert "task:" in str(result)
