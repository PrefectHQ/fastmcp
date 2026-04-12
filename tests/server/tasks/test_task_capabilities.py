"""
Tests for SEP-1686 task capabilities declaration.

Verifies that the server correctly advertises task support.
Task protocol is now always enabled.
"""

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.tasks import get_task_capabilities


async def test_capabilities_include_tasks():
    """Server capabilities always include tasks in first-class field (SEP-1686)."""
    mcp = FastMCP("capability-test")

    @mcp.tool()
    async def test_tool() -> str:
        return "test"

    async with Client(mcp) as client:
        # Get server initialization result which includes capabilities
        init_result = client.initialize_result

        # Verify tasks capability is present as a first-class field (not experimental)
        assert init_result.capabilities.tasks is not None
        assert init_result.capabilities.tasks == get_task_capabilities()
        # Verify it's NOT in experimental
        assert "tasks" not in (init_result.capabilities.experimental or {})


async def test_client_uses_task_capable_session():
    """Client uses task-capable initialization."""
    mcp = FastMCP("client-cap-test")

    @mcp.tool()
    async def test_tool() -> str:
        return "test"

    async with Client(mcp) as client:
        # Client should have connected successfully with task capabilities
        assert client.initialize_result is not None
        # Session should be a ClientSession (task-capable init uses standard session)
        assert type(client.session).__name__ == "ClientSession"


def test_capabilities_hidden_when_pydocket_too_old(monkeypatch):
    """Capability advertisement and handler registration must agree.

    If ``is_docket_available()`` returns False (e.g. an old transitive
    pydocket), the server skips registering task handlers — so it must
    also stop advertising task capabilities, or clients would discover
    task support and then hit "method not found" at runtime.
    """
    import importlib.metadata

    from fastmcp.server import dependencies

    original_version = importlib.metadata.version

    def fake_version(name: str) -> str:
        if name == "pydocket":
            return "0.16.6"
        return original_version(name)

    monkeypatch.setattr(dependencies, "_DOCKET_AVAILABLE", None)
    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert get_task_capabilities() is None
