import threading
from collections.abc import AsyncIterator, Iterator
from typing import Literal

import pytest
from pydantic import BaseModel

from fastmcp import Client, Context, FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool


@pytest.mark.parametrize("injection", ["none", "context", "dependency"])
async def test_sync_generator_runs_on_worker(
    injection: Literal["none", "context", "dependency"],
) -> None:
    loop_thread = threading.get_ident()

    def resource() -> int:
        return 7

    def ordinary() -> Iterator[int]:
        assert threading.get_ident() != loop_thread
        yield 7

    def context(ctx: Context) -> Iterator[int]:
        assert isinstance(ctx, Context)
        assert threading.get_ident() != loop_thread
        yield 7

    def dependency(value: int = Depends(resource)) -> Iterator[int]:
        assert threading.get_ident() != loop_thread
        yield value

    def parent() -> list[int]:
        return []

    server = FastMCP()
    fn = {"none": ordinary, "context": context, "dependency": dependency}[injection]
    server.add_tool(
        Tool.from_tool(Tool.from_function(parent), transform_fn=fn, name="test")
    )
    async with Client(server) as client:
        result = await client.call_tool("test", {})
    assert result.data == [7]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("injected", [False, True])
async def test_generator_body_validation_is_tool_error(
    asynchronous: bool,
    injected: bool,
) -> None:
    class Payload(BaseModel):
        value: int

    def sync_body() -> Iterator[int]:
        yield 1
        Payload.model_validate({"value": "not-an-int"})

    async def async_body() -> AsyncIterator[int]:
        yield 1
        Payload.model_validate({"value": "not-an-int"})

    def sync_context(ctx: Context) -> Iterator[int]:
        assert isinstance(ctx, Context)
        yield from sync_body()

    async def async_context(ctx: Context) -> AsyncIterator[int]:
        assert isinstance(ctx, Context)
        async for item in async_body():
            yield item

    def parent() -> list[int]:
        return []

    functions = {
        (False, False): sync_body,
        (True, False): async_body,
        (False, True): sync_context,
        (True, True): async_context,
    }
    server = FastMCP()
    server.add_tool(
        Tool.from_tool(
            Tool.from_function(parent),
            transform_fn=functions[asynchronous, injected],
            name="test",
        )
    )
    async with Client(server) as client:
        with pytest.raises(ToolError, match="validation error"):
            await client.call_tool("test", {})
