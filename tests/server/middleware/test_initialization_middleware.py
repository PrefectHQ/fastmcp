"""Tests for middleware support during initialization."""

from typing import Any

import mcp.types as mt

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class InitializationMiddleware(Middleware):
    """Middleware that captures initialization details."""

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
        """Capture initialization details and store session data."""
        self.initialized = True

        # Extract client info from the initialize params
        if hasattr(context.message, "params") and hasattr(
            context.message.params, "clientInfo"
        ):
            self.client_info = context.message.params.clientInfo

        # Store data in the context state for cross-request access
        if context.fastmcp_context:
            context.fastmcp_context.set_state("client_initialized", True)
            if self.client_info:
                context.fastmcp_context.set_state(
                    "client_name", getattr(self.client_info, "name", "unknown")
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
        assert result.content[0].text == "Result: 42"  # type: ignore[attr-defined]


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


async def test_initialization_middleware_with_state_sharing():
    """Test that state set during initialization is available in later requests."""
    server = FastMCP("TestServer")

    class StateTrackingMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.init_state = {}
            self.tool_state = {}

        async def on_initialize(
            self,
            context: MiddlewareContext[mt.InitializeRequest],
            call_next: CallNext[mt.InitializeRequest, None],
        ) -> None:
            # Store some state during initialization
            if context.fastmcp_context:
                context.fastmcp_context.set_state("init_timestamp", "2024-01-01")
                context.fastmcp_context.set_state("client_id", "test-123")
                self.init_state["timestamp"] = "2024-01-01"
                self.init_state["client_id"] = "test-123"

            return await call_next(context)

        async def on_call_tool(
            self,
            context: MiddlewareContext[mt.CallToolRequestParams],
            call_next: CallNext[mt.CallToolRequestParams, Any],
        ) -> Any:
            # Try to access state from initialization
            if context.fastmcp_context:
                timestamp = context.fastmcp_context.get_state("init_timestamp")
                client_id = context.fastmcp_context.get_state("client_id")
                self.tool_state["timestamp"] = timestamp
                self.tool_state["client_id"] = client_id

            return await call_next(context)

    middleware = StateTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def test_tool() -> str:
        return "success"

    async with Client(server) as client:
        # Initialization should have set state
        assert middleware.init_state["timestamp"] == "2024-01-01"
        assert middleware.init_state["client_id"] == "test-123"

        # Call a tool - state should be accessible
        result = await client.call_tool("test_tool", {})
        assert result.content[0].text == "success"  # type: ignore[attr-defined]

        # State should have been accessible during tool call
        # Note: State is request-scoped, so it won't persist across requests
        # This test shows the pattern, but actual cross-request state would need
        # external storage (Redis, DB, etc.)
        # The middleware.tool_state might be None if state doesn't persist


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
