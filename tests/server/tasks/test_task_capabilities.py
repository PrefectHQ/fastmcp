"""
Tests for SEP-1686 task capabilities declaration.

Verifies that the server correctly advertises task support based on settings.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.utilities.tests import temporary_settings


async def test_capabilities_include_tasks_when_enabled():
    """Server capabilities include tasks when enable_tasks=True."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=True,
    ):
        mcp = FastMCP("capability-test")

        @mcp.tool()
        async def test_tool() -> str:
            return "test"

        async with Client(mcp) as client:
            # Get server initialization result which includes capabilities
            init_result = client.initialize_result

            # Verify tasks capability is present
            assert init_result.capabilities.experimental is not None
            assert "tasks" in init_result.capabilities.experimental
            tasks_cap = init_result.capabilities.experimental["tasks"]
            assert tasks_cap == {
                "tools": True,
                "prompts": True,
                "resources": True,
            }


async def test_capabilities_exclude_tasks_when_disabled():
    """Server capabilities do NOT include tasks when enable_tasks=False."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=False,
    ):
        mcp = FastMCP("capability-test")

        @mcp.tool()
        def test_tool() -> str:
            return "test"

        async with Client(mcp) as client:
            # Get server initialization result
            init_result = client.initialize_result

            # Verify tasks capability is NOT present
            if init_result.capabilities.experimental:
                assert "tasks" not in init_result.capabilities.experimental


async def test_capabilities_exclude_tasks_when_docket_disabled():
    """Server capabilities do NOT include tasks when enable_docket=False."""
    with temporary_settings(
        enable_docket=False,
        enable_tasks=False,
    ):
        mcp = FastMCP("capability-test")

        @mcp.tool()
        def test_tool() -> str:
            return "test"

        async with Client(mcp) as client:
            # Get server initialization result
            init_result = client.initialize_result

            # Verify tasks capability is NOT present
            if init_result.capabilities.experimental:
                assert "tasks" not in init_result.capabilities.experimental


async def test_enable_tasks_requires_enable_docket():
    """Setting enable_tasks=True without enable_docket=True raises error at server startup."""
    with temporary_settings(
        enable_docket=False,
        enable_tasks=True,
    ):
        mcp = FastMCP("config-test")

        @mcp.tool()
        async def test_tool() -> str:
            return "test"

        # Should fail when trying to start server (during lifespan)
        with pytest.raises(RuntimeError, match="requires.*enable_docket.*enable_tasks"):
            async with Client(mcp):
                pass  # Should never reach here


async def test_client_advertises_task_capability_when_enabled():
    """Client advertises experimental.tasks capability when enable_tasks=True."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=True,
    ):
        mcp = FastMCP("client-cap-test")

        @mcp.tool()
        async def test_tool() -> str:
            return "test"

        async with Client(mcp) as client:
            # Client should have connected successfully with task capabilities
            assert client.initialize_result is not None


async def test_client_does_not_advertise_tasks_when_disabled():
    """Client does NOT use custom session when enable_tasks=False."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=False,
    ):
        mcp = FastMCP("no-tasks-client-test")

        @mcp.tool()
        def test_tool() -> str:
            return "test"

        async with Client(mcp) as client:
            # Session should be standard ClientSession, not our custom one

            # The session should be a standard ClientSession
            assert type(client.session).__name__ == "ClientSession"
