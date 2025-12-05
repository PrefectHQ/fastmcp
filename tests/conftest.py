import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from fastmcp.utilities.tests import temporary_settings


# Fakeredis doesn't properly implement blocking xread - it returns immediately
# instead of waiting. This causes Docket._monitor_strikes to busy-loop, which
# overwhelms pytest-xdist workers on Windows. Replace with a simple sleep loop.
# See: https://github.com/cunla/fakeredis-py/issues/274
async def _mock_monitor_strikes(self):
    while True:
        await asyncio.sleep(60)


_monitor_strikes_patch = patch(
    "docket.docket.Docket._monitor_strikes", _mock_monitor_strikes
)
_monitor_strikes_patch.start()


@pytest.fixture(autouse=True)
def fresh_fakeredis_server():
    """Give each test a fresh FakeServer instead of sharing one.

    Docket stores a shared FakeServer as a class attribute (_memory_server).
    This can cause issues when many tests run in parallel on Windows.
    Reset it before each test to ensure isolation.
    """
    from docket import Docket

    # Clear the shared server so each test gets a fresh one
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")

    yield

    # Clean up after test
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


def pytest_collection_modifyitems(items):
    """Automatically mark tests in integration_tests folder with 'integration' marker."""
    for item in items:
        # Check if the test is in the integration_tests folder
        if "integration_tests" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def import_rich_rule():
    # What a hack
    import rich.rule  # noqa: F401

    yield


@pytest.fixture(autouse=True)
def isolate_settings_home(tmp_path: Path):
    """Ensure each test uses an isolated settings.home directory.

    This prevents SQLite database locking issues on Windows when multiple
    tests share the same DiskStore directory in settings.home / "oauth-proxy".
    """
    test_home = tmp_path / "fastmcp-test-home"
    test_home.mkdir(exist_ok=True)

    with temporary_settings(home=test_home):
        yield


def get_fn_name(fn: Callable[..., Any]) -> str:
    return fn.__name__  # ty: ignore[unresolved-attribute]


@pytest.fixture
def worker_id(request):
    """Get the xdist worker ID, or 'master' if not using xdist."""
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


@pytest.fixture
def free_port():
    """Get a free port for the test to use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture
def free_port_factory(worker_id):
    """Factory to get free ports that tracks used ports per test session."""
    used_ports = set()

    def get_port():
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                port = s.getsockname()[1]
                if port not in used_ports:
                    used_ports.add(port)
                    return port

    return get_port
