"""Run the MCP conformance test suite against a FastMCP server.

The suite is pinned rather than tracking `@latest`: upstream adds scenarios for
draft SEPs, so an unpinned run turns CI red on somebody else's release rather
than on a change of ours. Bumping `CONFORMANCE_VERSION` is how new scenarios
arrive, and the diff shows what they cost.

`--suite all` includes draft and pending scenarios, which is deliberate — most
of what FastMCP implements ahead of a spec release lives there. Anything that
does not pass is listed in `expected-failures.yml` with a reason.

Requires Node.js and npx to be available on PATH.
Mark: pytest -m conformance
"""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

CONFORMANCE_DIR = Path(__file__).parent
EXPECTED_FAILURES = CONFORMANCE_DIR / "expected-failures.yml"
HOST = "127.0.0.1"

#: Pinned version of `@modelcontextprotocol/conformance`. Bump deliberately.
CONFORMANCE_VERSION = "0.2.0-alpha.10"


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def _require_npx():
    if shutil.which("npx") is None:
        pytest.skip("npx not found on PATH — install Node.js to run conformance tests")


@pytest.fixture(scope="module")
def conformance_server(_require_npx):
    """Start the conformance test server in a background thread."""
    from tests.conformance.server import MCP_PATH, build_app

    port = _get_free_port()
    config = uvicorn.Config(build_app(), host=HOST, port=port, log_level="warning")
    uv_server = uvicorn.Server(config)

    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()

    # Wait for server to accept connections
    url = f"http://{HOST}:{port}{MCP_PATH}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=1):
                break
        except OSError:
            time.sleep(0.01)
    else:
        pytest.fail("Conformance server did not start in time")

    yield url

    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.conformance
@pytest.mark.timeout(180)
def test_mcp_conformance(conformance_server):
    """Run the full MCP conformance test suite against the server."""
    cmd = [
        "npx",
        "--yes",
        f"@modelcontextprotocol/conformance@{CONFORMANCE_VERSION}",
        "server",
        "--url",
        conformance_server,
        "--suite",
        "all",
    ]

    if EXPECTED_FAILURES.exists():
        cmd.extend(["--expected-failures", str(EXPECTED_FAILURES)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=150)

    # Print output for visibility in test results
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    assert result.returncode == 0, (
        f"Conformance tests failed (exit code {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
