"""Tests for lifespan functionality in both low-level and FastMCP servers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage
from mcp.types import (
    ClientCapabilities,
    Implementation,
    InitializeRequestParams,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
)
from pydantic import TypeAdapter

from fastmcp import Context, FastMCP


@pytest.mark.anyio
async def test_lowlevel_server_lifespan():
    """Test that lifespan works in low-level server."""

    @asynccontextmanager
    async def test_lifespan(server: Server) -> AsyncIterator[dict[str, bool]]:
        """Test lifespan context that tracks startup/shutdown."""
        context = {"started": False, "shutdown": False}
        try:
            context["started"] = True
            yield context
        finally:
            context["shutdown"] = True

    server = Server("test", lifespan=test_lifespan)

    # Create memory streams for testing
    send_stream1, receive_stream1 = anyio.create_memory_object_stream(100)
    send_stream2, receive_stream2 = anyio.create_memory_object_stream(100)

    # Create a tool that accesses lifespan context
    @server.call_tool()
    async def check_lifespan(name: str, arguments: dict) -> list:
        ctx = server.request_context
        assert isinstance(ctx.lifespan_context, dict)
        assert ctx.lifespan_context["started"]
        assert not ctx.lifespan_context["shutdown"]
        return [{"type": "text", "text": "true"}]

    # Run server in background task
    async with (
        anyio.create_task_group() as tg,
        send_stream1,
        receive_stream1,
        send_stream2,
        receive_stream2,
    ):

        async def run_server():
            await server.run(
                receive_stream1,
                send_stream2,
                InitializationOptions(
                    server_name="test",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
                raise_exceptions=True,
            )

        tg.start_soon(run_server)

        # Initialize the server
        params = InitializeRequestParams(
            protocolVersion="2024-11-05",
            capabilities=ClientCapabilities(),
            clientInfo=Implementation(name="test-client", version="0.1.0"),
        )
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0",
                        id=1,
                        method="initialize",
                        params=TypeAdapter(InitializeRequestParams).dump_python(params),
                    )
                )
            )
        )
        response = await receive_stream2.receive()
        response = response.message

        # Send initialized notification
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCNotification(
                        jsonrpc="2.0",
                        method="notifications/initialized",
                    )
                )
            )
        )

        # Call the tool to verify lifespan context
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0",
                        id=2,
                        method="tools/call",
                        params={"name": "check_lifespan", "arguments": {}},
                    )
                )
            )
        )

        # Get response and verify
        response = await receive_stream2.receive()
        response = response.message
        assert response.root.result["content"][0]["text"] == "true"

        # Cancel server task
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_fastmcp_server_lifespan():
    """Test that lifespan works in FastMCP server."""

    @asynccontextmanager
    async def test_lifespan(server: FastMCP) -> AsyncIterator[dict]:
        """Test lifespan context that tracks startup/shutdown."""
        context = {"started": False, "shutdown": False}
        try:
            context["started"] = True
            yield context
        finally:
            context["shutdown"] = True

    server = FastMCP("test", lifespan=test_lifespan)

    # Create memory streams for testing
    send_stream1, receive_stream1 = anyio.create_memory_object_stream(100)
    send_stream2, receive_stream2 = anyio.create_memory_object_stream(100)

    # Add a tool that checks lifespan context
    @server.tool()
    def check_lifespan(ctx: Context) -> bool:
        """Tool that checks lifespan context."""
        assert isinstance(ctx.request_context.lifespan_context, dict)
        assert ctx.request_context.lifespan_context["started"]
        assert not ctx.request_context.lifespan_context["shutdown"]
        return True

    # Run server in background task
    async with (
        anyio.create_task_group() as tg,
        send_stream1,
        receive_stream1,
        send_stream2,
        receive_stream2,
    ):

        async def run_server():
            await server._mcp_server.run(
                receive_stream1,
                send_stream2,
                server._mcp_server.create_initialization_options(),
                raise_exceptions=True,
            )

        tg.start_soon(run_server)

        # Initialize the server
        params = InitializeRequestParams(
            protocolVersion="2024-11-05",
            capabilities=ClientCapabilities(),
            clientInfo=Implementation(name="test-client", version="0.1.0"),
        )
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0",
                        id=1,
                        method="initialize",
                        params=TypeAdapter(InitializeRequestParams).dump_python(params),
                    )
                )
            )
        )
        response = await receive_stream2.receive()
        response = response.message

        # Send initialized notification
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCNotification(
                        jsonrpc="2.0",
                        method="notifications/initialized",
                    )
                )
            )
        )

        # Call the tool to verify lifespan context
        await send_stream1.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0",
                        id=2,
                        method="tools/call",
                        params={"name": "check_lifespan", "arguments": {}},
                    )
                )
            )
        )

        # Get response and verify
        response = await receive_stream2.receive()
        response = response.message
        assert response.root.result["content"][0]["text"] == "true"

        # Cancel server task
        tg.cancel_scope.cancel()
