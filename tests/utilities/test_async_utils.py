"""Tests for fastmcp.utilities.async_utils."""

import functools

import pytest

from fastmcp import Client, FastMCP
from fastmcp.prompts import prompt
from fastmcp.resources import resource
from fastmcp.tools import tool
from fastmcp.utilities.async_utils import is_coroutine_function


async def _async_fn(x: int) -> int:
    return x


def _sync_fn(x: int) -> int:
    return x


class TestIsCoroutineFunction:
    def test_plain_async(self) -> None:
        assert is_coroutine_function(_async_fn) is True

    def test_plain_sync(self) -> None:
        assert is_coroutine_function(_sync_fn) is False

    def test_partial_async(self) -> None:
        p = functools.partial(_async_fn, x=1)
        assert is_coroutine_function(p) is True

    def test_partial_sync(self) -> None:
        p = functools.partial(_sync_fn, x=1)
        assert is_coroutine_function(p) is False

    def test_nested_partial_async(self) -> None:
        p = functools.partial(functools.partial(_async_fn, x=1))
        assert is_coroutine_function(p) is True

    def test_nested_partial_sync(self) -> None:
        p = functools.partial(functools.partial(_sync_fn, x=1))
        assert is_coroutine_function(p) is False

    def test_lambda(self) -> None:
        assert is_coroutine_function(lambda: None) is False

    def test_non_callable(self) -> None:
        assert is_coroutine_function(42) is False


class TestAsyncPartialIntegration:
    async def test_async_partial_tool_runs(self) -> None:
        async def greet(greeting: str, name: str) -> str:
            return f"{greeting}, {name}!"

        greet_tool = tool(name="greet")(functools.partial(greet, "Hello"))

        mcp = FastMCP()
        mcp.add_tool(greet_tool)

        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "world"})
            assert result.content[0].text == "Hello, world!"

    async def test_async_partial_resource_reads(self) -> None:
        async def make_greeting(greeting: str) -> str:
            return f"{greeting}, resource!"

        greet_resource = resource("test://greet")(
            functools.partial(make_greeting, "Hi")
        )

        mcp = FastMCP()
        mcp.add_resource(greet_resource)

        async with Client(mcp) as client:
            result = await client.read_resource("test://greet")
            assert result[0].text == "Hi, resource!"

    async def test_async_partial_prompt_renders(self) -> None:
        async def make_prompt(prefix: str) -> str:
            return f"{prefix}: prompt content"

        note_prompt = prompt(name="note")(functools.partial(make_prompt, "Note"))

        mcp = FastMCP()
        mcp.add_prompt(note_prompt)

        async with Client(mcp) as client:
            result = await client.get_prompt("note")
            assert "Note: prompt content" in result.messages[0].content.text

    async def test_async_partial_with_task_true_does_not_raise(self) -> None:
        async def slow_task(prefix: str, x: int) -> str:
            return f"{prefix}-{x}"

        slow_tool = tool(name="slow", task=True)(functools.partial(slow_task, "ok"))

        mcp = FastMCP()
        mcp.add_tool(slow_tool)

    async def test_sync_partial_with_task_true_raises(self) -> None:
        def sync_task(prefix: str, x: int) -> str:
            return f"{prefix}-{x}"

        mcp = FastMCP()
        with pytest.raises(ValueError, match="sync function"):
            decorated = tool(name="slow", task=True)(functools.partial(sync_task, "ok"))
            mcp.add_tool(decorated)
