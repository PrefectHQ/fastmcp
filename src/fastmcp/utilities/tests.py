from __future__ import annotations

import copy
import multiprocessing
import socket
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

import uvicorn

from fastmcp import settings
from fastmcp.utilities.http import find_available_port

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


@contextmanager
def temporary_settings(**kwargs: Any):
    """
    Temporarily override FastMCP setting values.

    Args:
        **kwargs: The settings to override, including nested settings.

    Example:
        Temporarily override a setting:
        ```python
        import fastmcp
        from fastmcp.utilities.tests import temporary_settings

        with temporary_settings(log_level='DEBUG'):
            assert fastmcp.settings.log_level == 'DEBUG'
        assert fastmcp.settings.log_level == 'INFO'
        ```
    """
    old_settings = copy.deepcopy(settings.model_dump())

    try:
        # apply the new settings
        for attr, value in kwargs.items():
            if not hasattr(settings, attr):
                raise AttributeError(f"Setting {attr} does not exist.")
            setattr(settings, attr, value)
        yield

    finally:
        # restore the old settings
        for attr in kwargs:
            if hasattr(settings, attr):
                setattr(settings, attr, old_settings[attr])


def _run_server(mcp_server: FastMCP, transport: Literal["sse"], port: int) -> None:
    # Some Starlette apps are not pickleable, so we need to create them here based on the indicated transport
    if transport == "sse":
        app = mcp_server.http_app(transport="sse")
    else:
        raise ValueError(f"Invalid transport: {transport}")
    uvicorn_server = uvicorn.Server(
        config=uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    uvicorn_server.run()


@contextmanager
def run_server_in_process(
    server_fn: Callable[..., None],
    *args,
    provide_host_and_port: bool = True,
    **kwargs,
) -> Generator[str, None, None]:
    """
    Context manager that runs a FastMCP server in a separate process and
    returns the server URL. When the context manager is exited, the server process is killed.

    Args:
        server_fn: The function that runs a FastMCP server. FastMCP servers are
            not pickleable, so we need a function that creates and runs one.
        *args: Arguments to pass to the server function.
        provide_host_and_port: Whether to provide the host and port to the server function as kwargs.
        **kwargs: Keyword arguments to pass to the server function.

    Returns:
        The server URL.
    """
    host = "127.0.0.1"
    port = find_available_port()

    if provide_host_and_port:
        kwargs |= {"host": host, "port": port}

    proc = multiprocessing.Process(
        target=server_fn, args=args, kwargs=kwargs, daemon=True
    )
    proc.start()

    # Wait for server to be running
    max_attempts = 10
    attempt = 0
    while attempt < max_attempts and proc.is_alive():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((host, port))
                break
        except ConnectionRefusedError:
            if attempt < 3:
                time.sleep(0.01)
            else:
                time.sleep(0.1)
            attempt += 1
    else:
        raise RuntimeError(f"Server failed to start after {max_attempts} attempts")

    yield f"http://{host}:{port}"

    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        # If it's still alive, then force kill it
        proc.kill()
        proc.join(timeout=2)
        if proc.is_alive():
            raise RuntimeError("Server process failed to terminate even after kill")
