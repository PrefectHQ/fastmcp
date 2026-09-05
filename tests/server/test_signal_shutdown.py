"""Process-level regression tests for graceful signal shutdown."""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fastmcp import FastMCP

pytestmark = [
    pytest.mark.subprocess_heavy,
    pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX signal semantics are required"
    ),
]

SERVER_MODULE = "tests.server.signal_shutdown_server"
PROCESS_TIMEOUT = 10.0


async def test_run_http_owns_lifespan_when_asgi_lifespan_is_disabled() -> None:
    """Keep the process-scoped lifecycle owner retained by PR #4446."""
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        events.append("startup")
        try:
            yield {}
        finally:
            events.append("shutdown")

    server = FastMCP("outer-lifespan-owner", lifespan=lifespan)
    with (
        patch("fastmcp.server.mixins.transport.uvicorn.Config"),
        patch("fastmcp.server.mixins.transport.uvicorn.Server") as server_class,
    ):
        server_class.return_value._serve = AsyncMock()
        server_class.return_value.capture_signals.return_value = nullcontext()
        await server.run_http_async(
            transport="sse",
            show_banner=False,
            uvicorn_config={"lifespan": "off"},
        )
        server_class.return_value._serve.assert_awaited_once_with(sockets=None)

    assert events == ["startup", "shutdown"]


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_http(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + PROCESS_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("HTTP server exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    raise AssertionError("HTTP server did not accept connections")


@pytest.mark.timeout(20)
@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize(
    "shutdown_signal", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"]
)
def test_signal_runs_lifespan_teardown_and_exits(
    tmp_path: Path, transport: str, shutdown_signal: signal.Signals
) -> None:
    """A normal container stop must release resources and finish promptly.

    The readiness checks are transport-level rather than lifespan-level. This
    prevents a false pass caused by signaling HTTP before Uvicorn installs its
    handlers. The teardown itself awaits before writing its marker, so this
    also verifies that asynchronous cleanup is allowed to finish.
    """
    events_path = tmp_path / "events.txt"
    port = _available_port()
    command = [
        sys.executable,
        "-m",
        SERVER_MODULE,
        "--events",
        str(events_path),
        "--port",
        str(port),
        "--transport",
        transport,
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _wait_for_http(port, process)

        process.send_signal(shutdown_signal)
        try:
            process.wait(timeout=PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"{transport} server did not exit after {shutdown_signal.name}; "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )

        stdout, stderr = process.communicate()
        events = events_path.read_text().splitlines()
        assert events == ["startup", "shutdown"], (
            f"transport={transport}, signal={shutdown_signal.name}, "
            f"returncode={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
