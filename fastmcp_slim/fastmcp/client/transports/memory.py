import contextlib
import importlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import anyio
from mcp import ClientSession
from mcp.server import Server
from mcp.server.mcpserver import MCPServer as SDKServer
from mcp.shared.memory import create_client_server_memory_streams
from typing_extensions import Unpack

from fastmcp import _install_hints
from fastmcp.client.transports.base import (
    ClientTransport,
    SessionKwargs,
    TransportOptions,
)

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


def _lowlevel_of(server: "FastMCP[Any] | SDKServer") -> Server:
    """Resolve the underlying lowlevel MCP `Server` for either server type.

    The SDK's own high-level `MCPServer` exposes its lowlevel server as
    `_lowlevel_server` and its own `run()` is synchronous, so we always drive
    the async lowlevel `Server.run` here. FastMCP servers expose the same
    lowlevel server as `_mcp_server`.
    """
    if isinstance(server, SDKServer):
        return server._lowlevel_server
    return server._mcp_server


class FastMCPTransport(ClientTransport):
    """In-memory transport for FastMCP servers.

    This transport connects directly to a FastMCP server instance in the same
    Python process. It works with both FastMCP servers and the SDK's own
    high-level `MCPServer` from the low-level MCP SDK. This is particularly
    useful for unit tests or scenarios where client and server run in the same
    runtime.
    """

    def __init__(self, mcp: "FastMCP[Any] | SDKServer", raise_exceptions: bool = False):
        """Initialize a FastMCPTransport from a FastMCP server instance."""

        # Accept both FastMCP 2.x and FastMCP 1.0 servers. Their underlying
        # lowlevel MCP ``Server`` lives on different attributes
        # (``_mcp_server`` vs ``_lowlevel_server``); ``_lowlevel_of`` resolves
        # it uniformly so we can drive the async ``Server.run`` for both.
        self.server = mcp
        self.raise_exceptions = raise_exceptions

    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        options = transport_options or TransportOptions()
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            client_read, client_write = client_streams
            server_read, server_write = server_streams

            # Capture exceptions to re-raise after task group cleanup.
            # anyio task groups can suppress exceptions when cancel_scope.cancel()
            # is called during cleanup, so we capture and re-raise manually.
            exception_to_raise: BaseException | None = None

            # IMPORTANT: The lifespan MUST be the outer context and the task
            # group MUST be the inner context. This ensures the task group
            # (containing the server's run() and all its pub/sub subscriptions)
            # is cancelled and fully drained BEFORE the lifespan tears down
            # the Docket Worker and closes Redis connections. Reversing this
            # order (e.g. via `async with (tg, lifespan):`) causes the Worker
            # shutdown to hang for 5 seconds per test because fakeredis
            # blocking operations hold references that prevent clean
            # cancellation.
            lowlevel = _lowlevel_of(self.server)
            async with _enter_server_lifespan(server=self.server):  # noqa: SIM117
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: lowlevel.run(
                            server_read,
                            server_write,
                            lowlevel.create_initialization_options(),
                            raise_exceptions=self.raise_exceptions,
                        )
                    )

                    try:
                        async with options.session_class(
                            read_stream=client_read,
                            write_stream=client_write,
                            **session_kwargs,
                        ) as client_session:
                            yield client_session
                    except BaseException as e:
                        exception_to_raise = e
                    finally:
                        tg.cancel_scope.cancel()

            # Re-raise after task group has exited cleanly
            if exception_to_raise is not None:
                raise exception_to_raise

    def __repr__(self) -> str:
        return f"<FastMCPTransport(server='{self.server.name}')>"


@contextlib.asynccontextmanager
async def _enter_server_lifespan(
    server: "FastMCP[Any] | SDKServer",
) -> AsyncIterator[None]:
    """Enters the server's lifespan context for FastMCP servers and does nothing for the SDK's own high-level servers."""
    FastMCP2: type[Any] | None
    try:
        FastMCP2 = importlib.import_module("fastmcp.server.server").FastMCP
    except ImportError:
        FastMCP2 = None

    if FastMCP2 is None and not isinstance(server, SDKServer):
        raise ImportError(_install_hints.full_package("In-memory FastMCP transports"))

    if FastMCP2 is not None and isinstance(server, FastMCP2):
        async with server._lifespan_manager():
            yield
    else:
        yield
