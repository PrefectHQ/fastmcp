from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult


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
        self,
        hook: str | None = None,
        method: str | None = None,
        times: int | None = None,
        at_least: int | None = None,
    ) -> bool:
        """Assert that a hook was called a specific number of times."""

        if times is not None and at_least is not None:
            raise ValueError("Cannot specify both times and at_least")
        elif times is None and at_least is None:
            times = 1

        calls = self.get_calls(hook=hook, method=method)
        actual_times = len(calls)
        identifier = dict(hook=hook, method=method)

        if times is not None:
            assert actual_times == times, (
                f"Expected {times} calls for {identifier}, "
                f"but was called {actual_times} times"
            )
        elif at_least is not None:
            assert actual_times >= at_least, (
                f"Expected at least {at_least} calls for {identifier}, "
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

    @mcp.tool(tags={"add-tool"})
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
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="tools/call", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_call_tool", at_least=1)

        assert nested_middleware.assert_called(method="tools/call", times=0)

    async def test_call_tool_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.call_tool("nested_add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="tools/call", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_call_tool", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="tools/call", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_call_tool", at_least=1)

    async def test_read_resource_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://test")

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="resources/read", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_read_resource", at_least=1)

        assert nested_middleware.assert_called(times=0)

    async def test_read_resource_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://nested/test")

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="resources/read", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_read_resource", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="resources/read", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_read_resource", at_least=1)

    async def test_read_resource_template_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://test-template/1")

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="resources/read", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_read_resource", at_least=1)

        assert nested_middleware.assert_called(times=0)

    async def test_read_resource_template_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.read_resource("resource://nested/test-template/1")

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="resources/read", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_read_resource", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="resources/read", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_read_resource", at_least=1)

    async def test_get_prompt_on_parent_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.get_prompt("test_prompt", {"x": "test"})

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="prompts/get", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_get_prompt", at_least=1)

        assert nested_middleware.assert_called(times=0)

    async def test_get_prompt_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.get_prompt("nested_test_prompt", {"x": "test"})

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="prompts/get", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_get_prompt", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="prompts/get", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_get_prompt", at_least=1)

    async def test_list_tools_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.list_tools()

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="tools/list", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_list_tools", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="tools/list", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_list_tools", at_least=1)

    async def test_list_resources_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.list_resources()

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(method="resources/list", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(hook="on_list_resources", at_least=1)

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(method="resources/list", at_least=3)
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(hook="on_list_resources", at_least=1)

    async def test_list_resource_templates_on_nested_server(
        self,
        mcp_server: FastMCP,
        nested_mcp_server: FastMCP,
        recording_middleware: RecordingMiddleware,
        nested_middleware: RecordingMiddleware,
    ):
        mcp_server.mount(nested_mcp_server, namespace="nested")

        async with Client(mcp_server) as client:
            await client.list_resource_templates()

        assert recording_middleware.assert_called(at_least=3)
        assert recording_middleware.assert_called(
            method="resources/templates/list", at_least=3
        )
        assert recording_middleware.assert_called(hook="on_message", at_least=1)
        assert recording_middleware.assert_called(hook="on_request", at_least=1)
        assert recording_middleware.assert_called(
            hook="on_list_resource_templates", at_least=1
        )

        assert nested_middleware.assert_called(at_least=3)
        assert nested_middleware.assert_called(
            method="resources/templates/list", at_least=3
        )
        assert nested_middleware.assert_called(hook="on_message", at_least=1)
        assert nested_middleware.assert_called(hook="on_request", at_least=1)
        assert nested_middleware.assert_called(
            hook="on_list_resource_templates", at_least=1
        )


class TestProxyServer:
    async def test_call_tool(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        # proxy server will have its tools listed as well as called in order to
        # apply transforms and filters prior to the call.
        proxy_server = FastMCP.as_proxy(mcp_server, name="Proxy Server")
        async with Client(proxy_server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        assert recording_middleware.assert_called(at_least=6)
        assert recording_middleware.assert_called(method="tools/call", at_least=3)
        assert recording_middleware.assert_called(method="tools/list", at_least=3)
        assert recording_middleware.assert_called(hook="on_message", at_least=2)
        assert recording_middleware.assert_called(hook="on_request", at_least=2)
        assert recording_middleware.assert_called(hook="on_call_tool", at_least=1)
        assert recording_middleware.assert_called(hook="on_list_tools", at_least=1)

    async def test_proxied_tags_are_visible_to_middleware(
        self, mcp_server: FastMCP, recording_middleware: RecordingMiddleware
    ):
        """Tests that tags on remote FastMCP servers are visible to middleware
        via proxy. See https://github.com/PrefectHQ/fastmcp/issues/1300"""
        proxy_server = FastMCP.as_proxy(mcp_server, name="Proxy Server")

        TAGS = []

        class TagMiddleware(Middleware):
            async def on_list_tools(self, context: MiddlewareContext, call_next):
                nonlocal TAGS
                result = await call_next(context)
                for tool in result:
                    TAGS.append(tool.tags)
                return result

        proxy_server.add_middleware(TagMiddleware())

        async with Client(proxy_server) as client:
            await client.list_tools()

        assert TAGS == [{"add-tool"}, set(), set(), set()]


class TestToolCallDenial:
    """Test denying tool calls in middleware using ToolError."""

    async def test_deny_tool_call_with_tool_error(self):
        """Test that middleware can deny tool calls by raising ToolError."""

        class AuthMiddleware(Middleware):
            async def on_call_tool(
                self,
                context: MiddlewareContext[mcp.types.CallToolRequestParams],
                call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
            ) -> ToolResult:
                tool_name = context.message.name
                if tool_name.lower() == "restricted_tool":
                    raise ToolError("Access denied: tool is disabled")
                return await call_next(context)

        server = FastMCP("TestServer")

        @server.tool
        def allowed_tool(x: int) -> int:
            """This tool is allowed."""
            return x * 2

        @server.tool
        def restricted_tool(x: int) -> int:
            """This tool should be denied by middleware."""
            return x * 3

        server.add_middleware(AuthMiddleware())

        async with Client(server) as client:
            # Allowed tool should work normally
            result = await client.call_tool("allowed_tool", {"x": 5})
            assert result.structured_content is not None
            assert result.structured_content["result"] == 10

            # Restricted tool should raise ToolError
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("restricted_tool", {"x": 5})

            # Verify the error message is preserved
            assert "Access denied: tool is disabled" in str(exc_info.value)

    async def test_middleware_can_selectively_deny_tools(self):
        """Test that middleware can deny specific tools while allowing others."""

        denied_tools = set()

        class SelectiveAuthMiddleware(Middleware):
            async def on_call_tool(
                self,
                context: MiddlewareContext[mcp.types.CallToolRequestParams],
                call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
            ) -> ToolResult:
                tool_name = context.message.name

                # Deny tools that start with "admin_"
                if tool_name.startswith("admin_"):
                    denied_tools.add(tool_name)
                    raise ToolError(
                        f"Access denied: {tool_name} requires admin privileges"
                    )

                return await call_next(context)

        server = FastMCP("TestServer")

        @server.tool
        def public_tool(x: int) -> int:
            """Public tool available to all."""
            return x + 1

        @server.tool
        def admin_delete(item_id: str) -> str:
            """Admin tool that should be denied."""
            return f"Deleted {item_id}"

        @server.tool
        def admin_config(setting: str, value: str) -> str:
            """Another admin tool that should be denied."""
            return f"Set {setting} to {value}"

        server.add_middleware(SelectiveAuthMiddleware())

        async with Client(server) as client:
            # Public tool should work
            result = await client.call_tool("public_tool", {"x": 10})
            assert result.structured_content is not None
            assert result.structured_content["result"] == 11

            # Admin tools should be denied
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("admin_delete", {"item_id": "test123"})
            assert "requires admin privileges" in str(exc_info.value)

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "admin_config", {"setting": "debug", "value": "true"}
                )
            assert "requires admin privileges" in str(exc_info.value)

        # Verify both admin tools were denied
        assert denied_tools == {"admin_delete", "admin_config"}


class TestMiddlewareRequestState:
    """Non-serializable state set in middleware must be visible to tools/resources.

    Regression test for https://github.com/PrefectHQ/fastmcp/issues/3228.
    """

    async def test_non_serializable_state_from_middleware_visible_in_tool(self):
        server = FastMCP("test")

        sentinel = object()

        class StateMiddleware(Middleware):
            async def on_call_tool(
                self, context: MiddlewareContext, call_next: CallNext
            ) -> Any:
                assert context.fastmcp_context is not None
                await context.fastmcp_context.set_state(
                    "obj", sentinel, serializable=False
                )
                return await call_next(context)

        server.add_middleware(StateMiddleware())

        @server.tool()
        async def read_it(ctx: Context) -> str:
            val = await ctx.get_state("obj")
            return "found" if val is sentinel else "missing"

        async with Client(server) as client:
            result = await client.call_tool("read_it")
            assert result.content[0].text == "found"

    async def test_non_serializable_state_from_middleware_visible_in_resource(self):
        server = FastMCP("test")

        sentinel = object()

        class StateMiddleware(Middleware):
            async def on_read_resource(
                self, context: MiddlewareContext, call_next: CallNext
            ) -> Any:
                assert context.fastmcp_context is not None
                await context.fastmcp_context.set_state(
                    "obj", sentinel, serializable=False
                )
                return await call_next(context)

        server.add_middleware(StateMiddleware())

        @server.resource("test://data")
        async def read_it(ctx: Context) -> str:
            val = await ctx.get_state("obj")
            return "found" if val is sentinel else "missing"

        async with Client(server) as client:
            result = await client.read_resource("test://data")
            assert result[0].text == "found"
