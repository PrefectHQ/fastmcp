"""Tests for fastmcp.utilities.async_utils."""

import functools
import inspect
from collections.abc import Awaitable, Iterator
from typing import Any

import anyio
import pytest
from exceptiongroup import BaseExceptionGroup

from fastmcp import Client, FastMCP
from fastmcp.prompts import prompt
from fastmcp.resources import resource
from fastmcp.tools import tool
from fastmcp.utilities import async_utils
from fastmcp.utilities.async_utils import gather, is_coroutine_function


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


class TestGather:
    async def test_returns_results_in_input_order(self) -> None:
        async def value(result: int) -> int:
            return result

        assert await gather([value(1), value(2), value(3)]) == [1, 2, 3]

    async def test_accepts_a_generator(self) -> None:
        async def value(result: int) -> int:
            return result

        assert await gather(value(i) for i in [1, 2, 3]) == [1, 2, 3]

    async def test_raises_by_default(self) -> None:
        async def fail() -> int:
            raise RuntimeError("boom")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await gather([fail()])

        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], RuntimeError)

    async def test_return_exceptions_collects_exceptions(self) -> None:
        async def fail() -> int:
            raise ValueError("bad")

        async def value() -> int:
            return 1

        result = await gather([fail(), value()], return_exceptions=True)

        assert isinstance(result[0], ValueError)
        assert result[1] == 1

    async def test_does_not_leak_coroutine_when_scheduling_is_interrupted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If handing an already-created awaitable off to the task group
        raises partway through scheduling, that awaitable must be closed
        rather than silently garbage collected later - which is what
        produces a "coroutine was never awaited" RuntimeWarning attributed
        to whatever unrelated code happens to be running when the garbage
        collector eventually reclaims it.

        In production this can happen when a synchronous signal handler
        (e.g. pytest-timeout's SIGALRM-based per-test timeout) fires inside
        anyio's task-spawning internals. This test reproduces the same
        shape of interruption deterministically by making the task group's
        ``start_soon`` raise partway through scheduling, instead of relying
        on real signal timing.
        """
        real_create_task_group = anyio.create_task_group

        class _FailOnSecondStart:
            def __init__(self) -> None:
                self._real_tg = real_create_task_group()
                self._calls = 0

            async def __aenter__(self) -> "_FailOnSecondStart":
                await self._real_tg.__aenter__()
                return self

            async def __aexit__(self, *exc_info: Any) -> bool | None:
                return await self._real_tg.__aexit__(*exc_info)

            def start_soon(self, func: Any, *args: Any) -> None:
                self._calls += 1
                if self._calls == 2:
                    raise RuntimeError("interrupted while scheduling")
                self._real_tg.start_soon(func, *args)

        monkeypatch.setattr(async_utils.anyio, "create_task_group", _FailOnSecondStart)

        created: list[Any] = []

        async def value(result: int) -> int:
            return result

        def awaitables() -> Iterator[Awaitable[int]]:
            for i in range(3):
                aw = value(i)
                created.append(aw)
                yield aw

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await gather(awaitables())

        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], RuntimeError)
        assert "interrupted while scheduling" in str(exc_info.value.exceptions[0])

        # created[1] was being handed to start_soon() when it raised - it
        # must have been closed rather than abandoned.
        assert inspect.getcoroutinestate(created[1]) == "CORO_CLOSED"

    async def test_closes_unscheduled_coroutines_from_an_eager_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lazy consumption keeps the leak window small, but a caller that
        builds its awaitables eagerly (a list or a parenthesized tuple) has
        coroutines queued behind the failing one that were never scheduled
        either. ``gather`` drains what is left of the iterable and closes
        those too, so it cannot leak regardless of how its argument was
        constructed."""
        real_create_task_group = anyio.create_task_group

        class _FailOnSecondStart:
            def __init__(self) -> None:
                self._real_tg = real_create_task_group()
                self._calls = 0

            async def __aenter__(self) -> "_FailOnSecondStart":
                await self._real_tg.__aenter__()
                return self

            async def __aexit__(self, *exc_info: Any) -> bool | None:
                return await self._real_tg.__aexit__(*exc_info)

            def start_soon(self, func: Any, *args: Any) -> None:
                self._calls += 1
                if self._calls == 2:
                    raise RuntimeError("interrupted while scheduling")
                self._real_tg.start_soon(func, *args)

        monkeypatch.setattr(async_utils.anyio, "create_task_group", _FailOnSecondStart)

        async def value(result: int) -> int:
            return result

        # Eagerly built: all four coroutines exist before gather() runs.
        eager = [value(0), value(1), value(2), value(3)]

        with pytest.raises(BaseExceptionGroup):
            await gather(eager)

        # The one that failed to schedule *and* the two queued behind it are
        # all closed; none is left to surface as a stray warning later.
        assert [inspect.getcoroutinestate(aw) for aw in eager[1:]] == [
            "CORO_CLOSED"
        ] * 3


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
