"""Tests for middleware support during initialization."""

from typing import Any

import mcp.types as mt
import pytest
from mcp import McpError
from mcp.types import ErrorData, TextContent

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class InitializationMiddleware(Middleware):
    """Middleware that captures initialization details.

    Note: Session state is NOT available during on_initialize because
    the MCP session has not been established yet. Use instance variables
    to store data that needs to persist across the session.
    """

    def __init__(self):
        super().__init__()
        self.initialized = False
        self.client_info = None
        self.session_data = {}

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, None],
    ) -> None:
        """Capture initialization details."""
        self.initialized = True

        # Extract client info from the initialize params
        if hasattr(context.message, "params") and hasattr(
            context.message.params, "clientInfo"
        ):
            self.client_info = context.message.params.clientInfo

        # Store in instance for cross-request access
        # (session state is not available during on_initialize)
        self.session_data["client_initialized"] = True
        if self.client_info:
            self.session_data["client_name"] = getattr(
                self.client_info, "name", "unknown"
            )

        return await call_next(context)


class ClientDetectionMiddleware(Middleware):
    """Middleware that detects specific clients and modifies behavior.

    This demonstrates storing data in the middleware instance itself
    for cross-request access, since context state is request-scoped.
    """

    def __init__(self):
        super().__init__()
        self.is_test_client = False
        self.tools_modified = False
        self.initialization_called = False

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, None],
    ) -> None:
        """Detect test client during initialization."""
        self.initialization_called = True

        # For testing purposes, always set it to true
        # Store in instance variable for cross-request access
        self.is_test_client = True

        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, list],
    ) -> list:
        """Modify tools based on client detection."""
        tools = await call_next(context)

        # Use the instance variable set during initialization
        if self.is_test_client:
            # Add a special annotation to tools for test clients
            for tool in tools:
                if not hasattr(tool, "annotations"):
                    tool.annotations = mt.ToolAnnotations()
                if tool.annotations is None:
                    tool.annotations = mt.ToolAnnotations()
                # Mark as read-only for test clients
                tool.annotations.readOnlyHint = True
            self.tools_modified = True

        return tools


async def test_simple_initialization_hook():
    """Test that the on_initialize hook is called."""
    server = FastMCP("TestServer")

    class SimpleInitMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.called = False

        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, None],
        ) -> None:
            self.called = True
            return await call_next(context)

    middleware = SimpleInitMiddleware()
    server.add_middleware(middleware)

    # Connect client
    async with Client(server):
        # Middleware should have been called
        assert middleware.called is True, "on_initialize was not called"


async def test_middleware_receives_initialization():
    """Test that middleware can intercept initialization requests."""
    server = FastMCP("TestServer")
    middleware = InitializationMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool(x: int) -> str:
        return f"Result: {x}"

    # Connect client
    async with Client(server) as client:
        # Middleware should have been called during initialization
        assert middleware.initialized is True

        # Test that the tool still works
        result = await client.call_tool("test_tool", {"x": 42})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Result: 42"


async def test_client_detection_middleware():
    """Test middleware that detects specific clients and modifies behavior."""
    server = FastMCP("TestServer")
    middleware = ClientDetectionMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def example_tool() -> str:
        return "example"

    # Connect with a client
    async with Client(server) as client:
        # Middleware should have been called during initialization
        assert middleware.initialization_called is True
        assert middleware.is_test_client is True

        # List tools to trigger modification
        tools = await client.list_tools()
        assert len(tools) == 1
        assert middleware.tools_modified is True

        # Check that the tool has the modified annotation
        tool = tools[0]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True


async def test_multiple_middleware_initialization():
    """Test that multiple middleware can handle initialization."""
    server = FastMCP("TestServer")

    init_mw = InitializationMiddleware()
    detect_mw = ClientDetectionMiddleware()

    server.add_middleware(init_mw)
    server.add_middleware(detect_mw)

    @server.tool
    def test_tool() -> str:
        return "test"

    async with Client(server) as client:
        # Both middleware should have processed initialization
        assert init_mw.initialized is True
        assert detect_mw.initialization_called is True
        assert detect_mw.is_test_client is True

        # List tools to check detection worked
        await client.list_tools()
        assert detect_mw.tools_modified is True


async def test_session_state_persists_across_tool_calls():
    """Test that session-scoped state persists across multiple tool calls.

    Session state is only available after the session is established,
    so it can't be set during on_initialize. This test shows state set
    during one tool call is accessible in subsequent tool calls.
    """
    server = FastMCP("TestServer")

    class StateTrackingMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.call_count = 0
            self.state_values = []

        async def on_call_tool(
            self,
            context: MiddlewareContext[mt.CallToolRequestParams],
            call_next: CallNext[mt.CallToolRequestParams, Any],
        ) -> Any:
            self.call_count += 1

            if context.fastmcp_context:
                # Read existing state
                counter = await context.fastmcp_context.get_state("call_counter")
                self.state_values.append(counter)

                # Increment and save
                new_counter = (counter or 0) + 1
                await context.fastmcp_context.set_state("call_counter", new_counter)

            return await call_next(context)

    middleware = StateTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool() -> str:
        return "success"

    async with Client(server) as client:
        # First call - state should be None initially
        result = await client.call_tool("test_tool", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "success"

        # Second call - state should show previous value (1)
        result = await client.call_tool("test_tool", {})
        assert isinstance(result.content[0], TextContent)

        # Third call - state should show previous value (2)
        result = await client.call_tool("test_tool", {})
        assert isinstance(result.content[0], TextContent)

        # Verify state persisted across calls within the session
        assert middleware.call_count == 3
        # First call saw None, second saw 1, third saw 2
        assert middleware.state_values == [None, 1, 2]


async def test_middleware_can_access_initialize_result():
    """Test that middleware can access the InitializeResult from call_next().

    This verifies that the initialize response is returned through the middleware
    chain, not just sent directly via the responder (fixes #2504).
    """
    server = FastMCP("TestServer")

    class ResponseCapturingMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.initialize_result: mt.InitializeResult | None = None

        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
        ) -> mt.InitializeResult | None:
            # Call next and capture the result
            result = await call_next(context)
            self.initialize_result = result
            return result

    middleware = ResponseCapturingMiddleware()
    server.add_middleware(middleware)

    async with Client(server):
        # Middleware should have captured the InitializeResult
        assert middleware.initialize_result is not None
        assert isinstance(middleware.initialize_result, mt.InitializeResult)

        # Verify the result contains expected server info
        assert middleware.initialize_result.serverInfo.name == "TestServer"
        assert middleware.initialize_result.protocolVersion is not None
        assert middleware.initialize_result.capabilities is not None


async def test_middleware_mcp_error_during_initialization():
    """Test that McpError raised in middleware during initialization is sent to client."""
    server = FastMCP("TestServer")

    class ErrorThrowingMiddleware(Middleware):
        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, None],
        ) -> None:
            raise McpError(
                ErrorData(
                    code=mt.INVALID_PARAMS, message="Invalid initialization parameters"
                )
            )

    server.add_middleware(ErrorThrowingMiddleware())

    with pytest.raises(McpError) as exc_info:
        async with Client(server):
            pass

    assert exc_info.value.error.message == "Invalid initialization parameters"
    assert exc_info.value.error.code == mt.INVALID_PARAMS


async def test_middleware_mcp_error_before_call_next():
    """Test McpError raised before calling next middleware."""
    server = FastMCP("TestServer")

    class EarlyErrorMiddleware(Middleware):
        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, None],
        ) -> None:
            raise McpError(
                ErrorData(code=mt.INVALID_REQUEST, message="Request validation failed")
            )

    server.add_middleware(EarlyErrorMiddleware())

    with pytest.raises(McpError) as exc_info:
        async with Client(server):
            pass

    assert exc_info.value.error.message == "Request validation failed"
    assert exc_info.value.error.code == mt.INVALID_REQUEST


async def test_middleware_mcp_error_after_call_next():
    """Test that McpError raised after call_next doesn't break the connection.

    When an error is raised after call_next, the responder has already completed,
    so the error is caught but not sent to the responder (checked via _completed flag).
    """
    server = FastMCP("TestServer")

    class PostProcessingErrorMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.error_raised = False

        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
        ) -> mt.InitializeResult | None:
            await call_next(context)
            self.error_raised = True
            raise McpError(
                ErrorData(code=mt.INTERNAL_ERROR, message="Post-processing failed")
            )

    middleware = PostProcessingErrorMiddleware()
    server.add_middleware(middleware)

    # Error is logged but not re-raised to prevent duplicate response
    async with Client(server):
        pass

    assert middleware.error_raised is True


async def test_state_isolation_between_streamable_http_clients():
    """Test that different HTTP clients have isolated session state.

    Each client should have its own session ID and isolated state.
    """
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.server.context import Context
    from fastmcp.utilities.tests import run_server_async

    server = FastMCP("TestServer")

    @server.tool
    async def store_and_read(value: str, ctx: Context) -> dict:
        """Store a value and return session info."""
        existing = await ctx.get_state("client_value")
        await ctx.set_state("client_value", value)
        return {
            "existing": existing,
            "stored": value,
            "session_id": ctx.session_id,
        }

    async with run_server_async(server, transport="streamable-http") as url:
        import json

        # Client 1 stores its value
        transport1 = StreamableHttpTransport(url=url)
        async with Client(transport=transport1) as client1:
            result1 = await client1.call_tool(
                "store_and_read", {"value": "client1-value"}
            )
            data1 = json.loads(result1.content[0].text)
            assert data1["existing"] is None
            assert data1["stored"] == "client1-value"
            session_id_1 = data1["session_id"]

        # Client 2 should have completely isolated state
        transport2 = StreamableHttpTransport(url=url)
        async with Client(transport=transport2) as client2:
            result2 = await client2.call_tool(
                "store_and_read", {"value": "client2-value"}
            )
            data2 = json.loads(result2.content[0].text)
            # Should NOT see client1's value
            assert data2["existing"] is None
            assert data2["stored"] == "client2-value"
            session_id_2 = data2["session_id"]

        # Session IDs should be different
        assert session_id_1 != session_id_2
