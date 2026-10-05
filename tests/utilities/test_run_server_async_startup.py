"""Regression tests for run_server_async startup failure handling."""

from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP
from fastmcp.utilities.tests import run_server_async


@asynccontextmanager
async def failing_lifespan(_server):
    raise RuntimeError("database unavailable")
    yield  # pragma: no cover


async def test_run_server_async_raises_when_lifespan_fails():
    mcp = FastMCP("demo", lifespan=failing_lifespan)

    with pytest.raises(RuntimeError, match="database unavailable"):
        async with run_server_async(mcp):
            pass
