"""Tests for lifespan functionality in both low-level and FastMCP servers."""

import os
import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
import uvicorn
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
from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp import Context, FastMCP
from fastmcp.utilities.tests import run_server_in_process


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


def run_server_with_incorrect_lifespan_setup(
    host: str, port: int, server_log_file_path: str
) -> None:
    os.makedirs(os.path.dirname(server_log_file_path), exist_ok=True)

    CUSTOM_LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(asctime)s [%(name)s] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(asctime)s [%(name)s] %(client_addr)s - "%(request_line)s" %(status_code)s',
                "datefmt": "%Y-%m-%d %H:%M:%S",
                "use_colors": False,
            },
        },
        "handlers": {
            "file_default": {
                "formatter": "default",
                "class": "logging.FileHandler",
                "filename": server_log_file_path,
                "mode": "w",
            },
            "file_access": {
                "formatter": "access",
                "class": "logging.FileHandler",
                "filename": server_log_file_path,
                "mode": "a",
            },
        },
        "loggers": {
            "uvicorn": {  # Catches uvicorn root logs
                "handlers": ["file_default"],
                "level": "DEBUG",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["file_default"],
                "level": "DEBUG",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["file_access"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["file_default"],
            "level": "DEBUG",
        },
    }

    try:
        mcp = FastMCP()

        @mcp.tool("ping_tool", "A simple ping tool for the test server")
        def ping_tool() -> str:
            return "pong"

        mcp_asgi_app = mcp.http_app(transport="streamable-http")

        parent_app = Starlette(
            routes=[Mount("/mounted_mcp", app=mcp_asgi_app)],
        )

        uvicorn.run(
            parent_app,
            host=host,
            port=port,
            log_config=CUSTOM_LOGGING_CONFIG,
            log_level=None,
        )
        sys.exit(0)
    except Exception as e_outer:
        with open(server_log_file_path, "a") as f_fallback:
            f_fallback.write(
                "--- FALLBACK EXCEPTION IN SERVER RUNNER (PRE-UVICORN) ---\n"
            )
            f_fallback.write(f"{type(e_outer).__name__}: {e_outer}\n")
            f_fallback.write(traceback.format_exc())
        sys.exit(1)


async def test_missing_lifespan_logs_informative_error(tmp_path: Path):
    server_log_file = tmp_path / "server.log"

    with run_server_in_process(
        run_server_with_incorrect_lifespan_setup, str(server_log_file)
    ) as server_url:
        full_mcp_path = server_url + "/mounted_mcp/mcp/"

        client_triggered_error = False
        response_status = -1
        response_body = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    full_mcp_path,
                    json={"id": 1, "method": "list_tools", "jsonrpc": "2.0"},
                )
            response_status = response.status_code
            response_body = response.text
            if response.status_code == 500:
                client_triggered_error = True
            else:
                print(
                    f"Client received unexpected status code: {response.status_code} "
                    f"Response: {response_body[:500]}"
                )
        except httpx.RequestError as e:
            print(f"Client request failed with RequestError: {e}")
            client_triggered_error = True

        assert client_triggered_error, (
            f"Client request did not result in a 500 error or a request error. "
            f"Status: {response_status}, Body: {response_body[:500]}"
        )

    assert server_log_file.exists(), (
        f"Server log file was not created at {server_log_file}"
    )
    log_content = server_log_file.read_text()

    print(f"--- Captured Server Log Content ({server_log_file}) ---")
    print(log_content)
    print("--- End Server Log Content ---")

    # Core assertions for the enhanced error message
    assert (
        "FastMCP's StreamableHTTPSessionManager task group was not initialized"
        in log_content
    )
    assert "lifespan=mcp_app.lifespan" in log_content
    assert "gofastmcp.com/deployment/asgi" in log_content
    assert "Original error: Task group is not initialized" in log_content

    # Check for Uvicorn's own error logging wrapper for the request
    assert "ERROR" in log_content  # General check for ERROR level logs
    assert "Exception in ASGI application" in log_content

    # Sanity checks for server operation and logging setup
    assert "Uvicorn running on" in log_content
    assert (
        "--- FALLBACK EXCEPTION IN SERVER RUNNER (PRE-UVICORN) ---" not in log_content
    )
