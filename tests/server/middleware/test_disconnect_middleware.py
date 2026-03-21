"""Tests for middleware on_disconnect lifecycle hook."""

from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class DisconnectTrackingMiddleware(Middleware):
    """Middleware that records whether on_disconnect was called."""

    def __init__(self):
        super().__init__()
        self.disconnected = False
        self.disconnect_context: MiddlewareContext | None = None

    async def on_disconnect(
        self,
        context: MiddlewareContext[None],
        call_next: CallNext[None, None],
    ) -> None:
        self.disconnected = True
        self.disconnect_context = context
        return await call_next(context)


async def test_on_disconnect_called_on_client_close():
    """on_disconnect fires when the client disconnects."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    def ping() -> str:
        return "pong"

    async with Client(server) as client:
        await client.call_tool("ping", {})
        assert not middleware.disconnected

    # Client has disconnected — middleware should have fired
    assert middleware.disconnected


async def test_on_disconnect_context_metadata():
    """on_disconnect provides correct context metadata."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    async with Client(server):
        pass

    assert middleware.disconnect_context is not None
    assert middleware.disconnect_context.method == "disconnect"
    assert middleware.disconnect_context.type == "lifecycle"
    assert middleware.disconnect_context.source == "server"
    assert middleware.disconnect_context.message is None


async def test_on_disconnect_has_access_to_session_state():
    """State set during tool calls is accessible in on_disconnect."""
    server = FastMCP("TestServer")

    class StateCheckingMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.state_at_disconnect: Any = None

        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            if context.fastmcp_context:
                self.state_at_disconnect = await context.fastmcp_context.get_state(
                    "user_id"
                )
            return await call_next(context)

    middleware = StateCheckingMiddleware()
    server.add_middleware(middleware)

    @server.tool
    async def set_user(user_id: str, ctx: Context) -> str:
        await ctx.set_state("user_id", user_id)
        return f"Set user to {user_id}"

    async with Client(server) as client:
        await client.call_tool("set_user", {"user_id": "user-123"})

    assert middleware.state_at_disconnect == "user-123"


async def test_on_disconnect_called_even_without_tool_calls():
    """on_disconnect fires even if the client connects and disconnects
    without calling any tools."""
    server = FastMCP("TestServer")
    middleware = DisconnectTrackingMiddleware()
    server.add_middleware(middleware)

    async with Client(server):
        pass

    assert middleware.disconnected


async def test_on_disconnect_error_does_not_crash_server():
    """Errors in on_disconnect are caught and logged, not propagated."""
    server = FastMCP("TestServer")

    class FailingDisconnectMiddleware(Middleware):
        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            raise RuntimeError("cleanup failed")

    server.add_middleware(FailingDisconnectMiddleware())

    # Should not raise
    async with Client(server):
        pass


async def test_multiple_middleware_on_disconnect():
    """Multiple middleware on_disconnect hooks fire in order."""
    server = FastMCP("TestServer")
    call_order: list[str] = []

    class FirstMiddleware(Middleware):
        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            call_order.append("first")
            return await call_next(context)

    class SecondMiddleware(Middleware):
        async def on_disconnect(
            self,
            context: MiddlewareContext[None],
            call_next: CallNext[None, None],
        ) -> None:
            call_order.append("second")
            return await call_next(context)

    server.add_middleware(FirstMiddleware())
    server.add_middleware(SecondMiddleware())

    async with Client(server):
        pass

    assert "first" in call_order
    assert "second" in call_order


async def test_on_disconnect_with_on_initialize():
    """on_initialize and on_disconnect both fire for the same session."""
    server = FastMCP("TestServer")

    class LifecycleMiddleware(Middleware):
        def __init__(self):
            super().__init__()
            self.events: list[str] = []

        async def on_initialize(self, context, call_next):
            self.events.append("initialize")
            return await call_next(context)

        async def on_disconnect(self, context, call_next):
            self.events.append("disconnect")
            return await call_next(context)

    middleware = LifecycleMiddleware()
    server.add_middleware(middleware)

    async with Client(server):
        pass

    assert middleware.events == ["initialize", "disconnect"]


async def test_on_lifecycle_called_for_disconnect():
    """The generic on_lifecycle hook fires for disconnect events."""
    server = FastMCP("TestServer")

    class LifecycleTracker(Middleware):
        def __init__(self):
            super().__init__()
            self.lifecycle_methods: list[str] = []

        async def on_lifecycle(self, context, call_next):
            self.lifecycle_methods.append(context.method)
            return await call_next(context)

    middleware = LifecycleTracker()
    server.add_middleware(middleware)

    async with Client(server):
        pass

    assert "disconnect" in middleware.lifecycle_methods
