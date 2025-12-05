"""
Tests for SEP-1686 ttl parameter handling.

Per the spec, servers MUST return ttl in all tasks/get responses,
and results should be retained for ttl milliseconds after completion.
"""

import asyncio

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client


@pytest.fixture
async def keepalive_server():
    """Create a server for testing ttl behavior."""
    mcp = FastMCP("keepalive-test")

    @mcp.tool(task=True)
    async def quick_task(value: int) -> int:
        return value * 2

    @mcp.tool(task=True)
    async def slow_task() -> str:
        await asyncio.sleep(1)
        return "done"

    return mcp


async def test_keepalive_returned_in_submitted_state(keepalive_server: FastMCP):
    """ttl is returned in tasks/get even when task is submitted/working."""
    async with Client(keepalive_server) as client:
        # Submit task with explicit ttl
        task = await client.call_tool(
            "slow_task",
            {},
            task=True,
            ttl=30000,  # 30 seconds (client-requested)
        )

        # Check status immediately - should be submitted or working
        status = await task.status()
        assert status.status in ["working"]

        # ttl should be present per spec (MUST return in all responses)
        # TODO: Docket uses a global execution_ttl for all tasks, not per-task TTLs.
        # The spec allows servers to override client-requested TTL (line 431).
        # FastMCP returns the server's actual global TTL (60000ms default from Docket).
        # If Docket gains per-task TTL support, update this to verify client-requested TTL is respected.
        assert status.ttl == 60000  # Server's global TTL, not client-requested 30000


async def test_keepalive_returned_in_completed_state(keepalive_server: FastMCP):
    """ttl is returned in tasks/get after task completes."""
    async with Client(keepalive_server) as client:
        # Submit and complete task
        task = await client.call_tool(
            "quick_task",
            {"value": 5},
            task=True,
            ttl=45000,  # Client-requested TTL
        )
        await task.wait(timeout=2.0)

        # Check status - should be completed
        status = await task.status()
        assert status.status == "completed"

        # TODO: Docket uses global execution_ttl, not per-task TTLs.
        # Server returns its global TTL (60000ms), not the client-requested 45000ms.
        # This is spec-compliant - servers MAY override requested TTL (spec line 431).
        assert status.ttl == 60000  # Server's global TTL, not client-requested 45000


async def test_default_keepalive_when_not_specified(keepalive_server: FastMCP):
    """Default ttl is used when client doesn't specify."""
    async with Client(keepalive_server) as client:
        # Submit without explicit ttl
        task = await client.call_tool("quick_task", {"value": 3}, task=True)
        await task.wait(timeout=2.0)

        status = await task.status()
        # Should have default ttl (60000ms = 60 seconds)
        assert status.ttl == 60000
