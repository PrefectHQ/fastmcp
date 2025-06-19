from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware import Middleware, MiddlewareContext


@dataclass
class Recording:
    # the hook is the name of the hook that was called, e.g. "on_list_tools"
    hook: str
    context: MiddlewareContext
    result: mcp.types.ServerResult | None


class RecordingMiddleware(Middleware):
    """A middleware that automatically records all method calls."""

    def __init__(self, name: str | None = None):
        super().__init__()
        self.calls: list[Recording] = []
        self.name = name

    def __getattribute__(self, name: str) -> Callable:
        """Dynamically create recording methods for any on_* method."""
        if name.startswith("on_"):

            async def record_and_call(
                context: MiddlewareContext, call_next: Callable
            ) -> Any:
                result = await call_next(context)

                self.calls.append(Recording(hook=name, context=context, result=result))

                return result

            return record_and_call

        return super().__getattribute__(name)

    def get_calls(
        self, method: str | None = None, hook: str | None = None
    ) -> list[Recording]:
        """
        Get all recorded calls for a specific method or hook.
        Args:
            method: The method to filter by (e.g. "tools/list")
            hook: The hook to filter by (e.g. "on_list_tools")
        Returns:
            A list of recorded calls.
        """
        calls = []
        for recording in self.calls:
            if method and hook:
                if recording.context.method == method and recording.hook == hook:
                    calls.append(recording)
            elif method:
                if recording.context.method == method:
                    calls.append(recording)
            elif hook:
                if recording.hook == hook:
                    calls.append(recording)
            else:
                calls.append(recording)
        return calls

    def assert_called(
        self, hook: str | None = None, method: str | None = None, times: int = 1
    ) -> bool:
        """Assert that a hook was called a specific number of times."""
        calls = self.get_calls(hook=hook, method=method)
        actual_times = len(calls)
        identifier = dict(hook=hook, method=method)
        assert actual_times == times, (
            f"Expected {times} calls for {identifier}, "
            f"but was called {actual_times} times"
        )
        return True

    def assert_not_called(self, hook: str | None = None, method: str | None = None):
        """Assert that a hook was not called."""
        calls = self.get_calls(hook=hook, method=method)
        assert len(calls) == 0, f"Expected {hook!r} to not be called"
        return True

    def reset(self):
        """Clear all recorded calls."""
        self.calls.clear()


@pytest.fixture
def recording_middleware():
    """Fixture that provides a recording middleware instance."""
    middleware = RecordingMiddleware(name="recording_middleware")
    yield middleware


@pytest.fixture
def mcp_server(recording_middleware):
    mcp = FastMCP()

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    @mcp.resource("resource://test")
    def test_resource() -> str:
        return "test resource"

    @mcp.resource("resource://test-template/{x}")
    def test_resource_with_path(x: int) -> str:
        return f"test resource with {x}"

    @mcp.prompt
    def test_prompt(x: str) -> str:
        return f"test prompt with {x}"

    @mcp.tool
    async def progress_tool(context: Context) -> None:
        await context.report_progress(progress=1, total=10, message="test")

    @mcp.tool
    async def log_tool(context: Context) -> None:
        await context.info(message="test log")

    @mcp.tool
    async def sample_tool(context: Context) -> None:
        await context.sample("hello")

    mcp.add_middleware(recording_middleware)

    # Register progress handler
    @mcp._mcp_server.progress_notification()
    async def handle_progress(
        progress_token: str | int,
        progress: float,
        total: float | None,
        message: str | None,
    ):
        print("HI")

    return mcp


class TestMiddlewareHooks:
    async def test_call_tool(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="tools/call", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_call_tool", times=1)

    async def test_read_resource(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.read_resource("resource://test")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

    async def test_read_resource_template(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.read_resource("resource://test-template/1")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

    async def test_get_prompt(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.get_prompt("test_prompt", {"x": "test"})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="prompts/get", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_get_prompt", times=1)

    async def test_list_tools(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.list_tools()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="tools/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_list_tools", times=1)

    async def test_list_resources(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.list_resources()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_list_resources", times=1)

    async def test_list_resource_templates(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.list_resource_templates()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(
            method="resources/templates/list", times=3
        )
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(
            hook="on_list_resource_templates", times=1
        )

    async def test_list_prompts(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        async with Client(mcp_server) as client:
            await client.list_prompts()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="prompts/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_list_prompts", times=1)


class TestNestedMiddlewareHooks:
    @pytest.fixture
    @staticmethod
    def nested_middleware():
        return RecordingMiddleware(name="nested_middleware")

    @pytest.fixture
    def nested_mcp_server(self, nested_middleware: RecordingMiddleware):
        mcp = FastMCP(name="Nested MCP")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        @mcp.resource("resource://test")
        def test_resource() -> str:
            return "test resource"

        @mcp.resource("resource://test-template/{x}")
        def test_resource_with_path(x: int) -> str:
            return f"test resource with {x}"

        @mcp.prompt
        def test_prompt(x: str) -> str:
            return f"test prompt with {x}"

        @mcp.tool
        async def progress_tool(context: Context) -> None:
            await context.report_progress(progress=1, total=10, message="test")

        @mcp.tool
        async def log_tool(context: Context) -> None:
            await context.info(message="test log")

        @mcp.tool
        async def sample_tool(context: Context) -> None:
            await context.sample("hello")

        mcp.add_middleware(nested_middleware)

        return mcp

    async def test_call_tool_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="tools/call", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_call_tool", times=1)

        assert nested_middleware.assert_called(times=0)

    async def test_call_tool_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.call_tool("nested_add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="tools/call", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_call_tool", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="tools/call", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_call_tool", times=1)

    async def test_read_resource_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://test")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

        assert nested_middleware.assert_called(times=0)

    async def test_read_resource_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://nested/test")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="resources/read", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_read_resource", times=1)

    async def test_read_resource_template_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://test-template/1")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

        assert nested_middleware.assert_called(times=0)

    async def test_read_resource_template_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://nested/test-template/1")

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/read", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_read_resource", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="resources/read", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_read_resource", times=1)

    async def test_get_prompt_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.get_prompt("test_prompt", {"x": "test"})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="prompts/get", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_get_prompt", times=1)

        assert nested_middleware.assert_called(times=0)

    async def test_get_prompt_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.get_prompt("nested_test_prompt", {"x": "test"})

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="prompts/get", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_get_prompt", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="prompts/get", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_get_prompt", times=1)

    async def test_list_tools_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.list_tools()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="tools/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_list_tools", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="tools/list", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_list_tools", times=1)

    async def test_list_resources_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.list_resources()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(method="resources/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(hook="on_list_resources", times=1)

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(method="resources/list", times=3)
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(hook="on_list_resources", times=1)

    async def test_list_resource_templates_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, prefix="nested")

        async with Client(mcp_server) as client:
            await client.list_resource_templates()

        assert recording_middleware.assert_called(times=3)
        assert recording_middleware.assert_called(
            method="resources/templates/list", times=3
        )
        assert recording_middleware.assert_called(hook="on_message", times=1)
        assert recording_middleware.assert_called(hook="on_request", times=1)
        assert recording_middleware.assert_called(
            hook="on_list_resource_templates", times=1
        )

        assert nested_middleware.assert_called(times=3)
        assert nested_middleware.assert_called(
            method="resources/templates/list", times=3
        )
        assert nested_middleware.assert_called(hook="on_message", times=1)
        assert nested_middleware.assert_called(hook="on_request", times=1)
        assert nested_middleware.assert_called(
            hook="on_list_resource_templates", times=1
        )


class TestProxyServer:
    async def test_call_tool(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        # proxy server will have its tools listed as well as called in order to
        # run the `should_enable_component` hook prior to the call.
        proxy_server = FastMCP.as_proxy(mcp_server, name="Proxy Server")
        async with Client(proxy_server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(times=6)
        assert recording_middleware.assert_called(method="tools/call", times=3)
        assert recording_middleware.assert_called(method="tools/list", times=3)
        assert recording_middleware.assert_called(hook="on_message", times=2)
        assert recording_middleware.assert_called(hook="on_request", times=2)
        assert recording_middleware.assert_called(hook="on_call_tool", times=1)
        assert recording_middleware.assert_called(hook="on_list_tools", times=1)
