"""Lifespan teardown when an HTTP server process is stopped by a signal."""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from fastmcp.utilities.http import find_available_port

SERVER_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    import anyio

    from fastmcp import FastMCP
    from fastmcp.server.lifespan import lifespan

    events = Path(sys.argv[1])

    def record(event):
        with events.open("a") as f:
            f.write(event + "\\n")

    @lifespan
    async def app_lifespan(server):
        record("startup")
        try:
            yield {}
        finally:
            # Asynchronous cleanup, not only a synchronous marker.
            await anyio.sleep(0.05)
            record("shutdown")

    mcp = FastMCP("signal-shutdown", lifespan=app_lifespan)

    mcp.run(
        transport=sys.argv[2],
        host="127.0.0.1",
        port=int(sys.argv[3]),
        show_banner=False,
        log_level="error",
    )
    """
)


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _accepts_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.mark.subprocess_heavy
@pytest.mark.timeout(30)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_runs_lifespan_teardown(
    tmp_path: Path, transport: str, sig: signal.Signals
) -> None:
    events = tmp_path / "events.txt"
    port = find_available_port()
    process = subprocess.Popen(
        [sys.executable, "-c", SERVER_SCRIPT, str(events), transport, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        started = _wait_for(
            lambda: events.exists() and "startup" in events.read_text(), 15
        )
        assert started, "server did not start"
        # Accepting connections means Uvicorn has installed its signal handlers.
        assert _wait_for(lambda: _accepts_connections(port), 15)

        process.send_signal(sig)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert events.read_text().splitlines() == ["startup", "shutdown"]
