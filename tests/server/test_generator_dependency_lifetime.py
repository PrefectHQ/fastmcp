"""Generator results must finish before context-manager dependencies exit."""

import asyncio
import io
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ValidationError
from fastmcp.tools.function_tool import FunctionTool, _ToolBodyError


@pytest.mark.parametrize("run_in_thread", [False, True])
@pytest.mark.parametrize(
    "kind", ["async_generator", "sync_generator", "coroutine", "sync_wrapper"]
)
async def test_generator_result_keeps_dependency_open(kind: str, run_in_thread: bool):
    stream = io.StringIO("payload")
    events = []

    @asynccontextmanager
    async def open_stream():
        events.append("enter")
        try:
            yield stream
        finally:
            events.append("exit")
            stream.close()

    async def produce(value) -> AsyncIterator[str]:
        try:
            events.append("body")
            yield value.read()
        finally:
            events.append("generator-finally")

    if kind == "async_generator":

        async def read(value=Depends(open_stream)) -> AsyncIterator[str]:
            async for item in produce(value):
                yield item
    elif kind == "sync_generator":

        def read(value=Depends(open_stream)) -> Iterator[str]:
            try:
                events.append("body")
                yield value.read()
            finally:
                events.append("generator-finally")
    elif kind == "coroutine":

        async def read(value=Depends(open_stream)) -> AsyncIterator[str]:
            return produce(value)
    else:

        def read(value=Depends(open_stream)) -> AsyncIterator[str]:
            return produce(value)

    mcp = FastMCP("generator-dependency")
    mcp.tool(read, run_in_thread=run_in_thread)
    async with Client(mcp) as client:
        result = await client.call_tool("read", {})
    assert result.content[0].text == '["payload"]'
    assert events == ["enter", "body", "generator-finally", "exit"]
    assert stream.closed


@pytest.mark.parametrize("failure", ["exception", "validation", "cancel"])
async def test_generator_failure_closes_dependency_after_generator(failure: str):
    events = []
    started = asyncio.Event()

    @asynccontextmanager
    async def dependency():
        events.append("enter")
        try:
            yield None
        finally:
            events.append("exit")

    class Model(BaseModel):
        value: int

    async def read(value=Depends(dependency)) -> AsyncIterator[str]:
        try:
            events.append("body")
            yield "first"
            if failure == "exception":
                raise RuntimeError("iteration failed")
            if failure == "validation":
                Model(value="invalid")
            started.set()
            await asyncio.Event().wait()
        finally:
            events.append("generator-finally")

    tool = FunctionTool.from_function(read)
    if failure == "cancel":
        task = asyncio.create_task(tool.run({}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        error = RuntimeError if failure == "exception" else _ToolBodyError
        with pytest.raises(error):
            await tool.run({})
    assert events == ["enter", "body", "generator-finally", "exit"]


async def test_invalid_generator_arguments_do_not_enter_dependency():
    events = []

    @asynccontextmanager
    async def dependency():
        events.append("enter")
        yield None
        events.append("exit")

    async def read(number: int, value=Depends(dependency)) -> AsyncIterator[str]:
        yield str(number)

    tool = FunctionTool.from_function(read)
    with pytest.raises(ValidationError):
        await tool.run({"number": "invalid"})
    assert events == []
