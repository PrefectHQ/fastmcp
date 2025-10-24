import socket
from collections.abc import Callable
from typing import Any

import pytest


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
