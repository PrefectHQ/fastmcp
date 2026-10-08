"""Regression tests for run_server_async startup failure handling."""

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

import pytest

import fastmcp.utilities.tests as helpers
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


async def test_cancelled_startup_cleans_server(monkeypatch):
    mcp = FastMCP("startup-cancellation")
    entered = asyncio.Event()
    stopped = asyncio.Event()
    server_task = None

    async def run(**kwargs: Any) -> None:
        nonlocal server_task
        server_task = asyncio.current_task()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(mcp, "run_http_async", run)

    async def enter() -> None:
        async with run_server_async(mcp):
            pass

    task = asyncio.create_task(enter())
    await entered.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
    finally:
        if server_task is not None and not server_task.done():
            server_task.cancel()
            with suppress(asyncio.CancelledError):
                await server_task


async def test_port_wait_failure_cleans_server(monkeypatch):
    mcp = FastMCP("port-failure")
    stopped = asyncio.Event()
    server_task = None

    async def run(**kwargs: Any) -> None:
        nonlocal server_task
        server_task = asyncio.current_task()
        mcp._started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def fail_port(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("port unavailable")

    monkeypatch.setattr(mcp, "run_http_async", run)
    monkeypatch.setattr(helpers, "_wait_for_port", fail_port)
    try:
        with pytest.raises(RuntimeError, match="port unavailable"):
            async with run_server_async(mcp):
                pass
        assert stopped.is_set()
    finally:
        if server_task is not None and not server_task.done():
            server_task.cancel()
            with suppress(asyncio.CancelledError):
                await server_task
