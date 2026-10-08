"""Generator tool bodies follow the synchronous thread-dispatch policy."""

import asyncio
import functools
import json
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from mcp_types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError


def _sync_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapped


@pytest.mark.parametrize("run_in_thread", [True, False])
@pytest.mark.parametrize("inject_context", [True, False])
async def test_sync_generator_obeys_thread_dispatch(
    run_in_thread: bool, inject_context: bool
) -> None:
    mcp = FastMCP()
    loop_thread = threading.get_ident()

    if inject_context:

        @mcp.tool(run_in_thread=run_in_thread)
        def thread_ids(ctx: Context) -> Iterator[int]:
            assert ctx.fastmcp is mcp
            yield threading.get_ident()
            yield threading.get_ident()

    else:

        @mcp.tool(run_in_thread=run_in_thread)
        def thread_ids() -> Iterator[int]:
            yield threading.get_ident()
            yield threading.get_ident()

    async with Client(mcp) as client:
        result = await client.call_tool("thread_ids")

    assert isinstance(result.content[0], TextContent)
    ids = json.loads(result.content[0].text)
    assert len(ids) == 2
    assert all((tid != loop_thread) == run_in_thread for tid in ids)


@pytest.mark.parametrize("run_in_thread", [True, False])
async def test_sync_factory_generator_obeys_thread_dispatch(
    run_in_thread: bool,
) -> None:
    mcp = FastMCP()
    loop_thread = threading.get_ident()

    @mcp.tool(run_in_thread=run_in_thread)
    def thread_ids() -> Iterator[int]:
        return (threading.get_ident() for _ in range(2))

    async with Client(mcp) as client:
        result = await client.call_tool("thread_ids")

    assert isinstance(result.content[0], TextContent)
    ids = json.loads(result.content[0].text)
    assert len(ids) == 2
    assert all((tid != loop_thread) == run_in_thread for tid in ids)


@pytest.mark.parametrize("run_in_thread", [True, False])
async def test_async_generator_stays_on_event_loop(run_in_thread: bool) -> None:
    mcp = FastMCP()
    loop_thread = threading.get_ident()

    @mcp.tool(run_in_thread=run_in_thread)
    async def thread_ids() -> AsyncIterator[int]:
        yield threading.get_ident()

    async with Client(mcp) as client:
        result = await client.call_tool("thread_ids")

    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == [loop_thread]


@pytest.mark.parametrize("run_in_thread", [True, False])
async def test_async_factory_generator_stays_on_event_loop(run_in_thread: bool) -> None:
    mcp = FastMCP()
    loop_thread = threading.get_ident()

    @mcp.tool(run_in_thread=run_in_thread)
    async def thread_ids() -> Iterator[int]:
        return (threading.get_ident() for _ in range(2))

    async with Client(mcp) as client:
        result = await client.call_tool("thread_ids")

    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == [loop_thread, loop_thread]


@pytest.mark.parametrize("run_in_thread", [True, False])
async def test_generator_iteration_error_is_tool_error(run_in_thread: bool) -> None:
    mcp = FastMCP()

    @mcp.tool(run_in_thread=run_in_thread)
    def broken() -> Iterator[int]:
        yield 1
        raise ValueError("generator failed")

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="generator failed"):
            await client.call_tool("broken")


@pytest.mark.parametrize("inject_context", [True, False])
async def test_sync_wrapper_of_async_generator_factory_stays_on_loop(
    inject_context: bool,
) -> None:
    mcp = FastMCP()

    if inject_context:

        async def thread_ids(ctx: Context) -> Iterator[bool]:
            assert ctx.fastmcp is mcp
            return (asyncio.get_running_loop().is_running() for _ in range(2))

    else:

        async def thread_ids() -> Iterator[bool]:
            return (asyncio.get_running_loop().is_running() for _ in range(2))

    mcp.tool(_sync_wrapper(thread_ids))
    async with Client(mcp) as client:
        result = await client.call_tool("thread_ids")

    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == [True, True]


async def test_sync_generator_consumed_before_dependency_cleanup() -> None:
    mcp = FastMCP()
    state = {"open": False}

    @contextmanager
    def connection() -> Iterator[dict[str, bool]]:
        state["open"] = True
        try:
            yield state
        finally:
            state["open"] = False

    @mcp.tool
    def query(resource: dict[str, bool] = Depends(connection)) -> Iterator[bool]:
        yield resource["open"]

    async with Client(mcp) as client:
        result = await client.call_tool("query")

    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == [True]
    assert state["open"] is False
