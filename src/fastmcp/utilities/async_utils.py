"""Async utilities for FastMCP."""

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar, overload

import anyio
from anyio.to_thread import run_sync as run_sync_in_threadpool

T = TypeVar("T")


def is_coroutine_function(fn: Any) -> bool:
    """Check if a callable is a coroutine function, unwrapping functools.partial.

    ``inspect.iscoroutinefunction`` returns ``False`` for
    ``functools.partial`` objects wrapping an async function on Python < 3.12.
    This helper unwraps any layers of ``partial`` before checking.
    """
    while isinstance(fn, functools.partial):
        fn = fn.func
    return inspect.iscoroutinefunction(fn)


async def call_sync_fn_in_threadpool(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Call a sync function in a threadpool to avoid blocking the event loop.

    Uses anyio.to_thread.run_sync which properly propagates contextvars,
    making this safe for functions that depend on context (like dependency injection).
    """
    return await run_sync_in_threadpool(functools.partial(fn, *args, **kwargs))


@overload
async def gather(
    *awaitables: Awaitable[T],
    return_exceptions: Literal[True],
) -> list[T | BaseException]: ...


@overload
async def gather(
    *awaitables: Awaitable[T],
    return_exceptions: Literal[False] = ...,
) -> list[T]: ...


async def gather(
    *awaitables: Awaitable[T],
    return_exceptions: bool = False,
) -> list[T] | list[T | BaseException]:
    """Run awaitables concurrently and return results in order.

    Uses anyio TaskGroup for structured concurrency.

    Args:
        *awaitables: Awaitables to run concurrently
        return_exceptions: If True, exceptions are returned in results.
                          If False, first exception cancels all and raises.

    Returns:
        List of results in the same order as input awaitables.
    """
    results: list[T | BaseException] = [None] * len(awaitables)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

    async def run_at(i: int, aw: Awaitable[T]) -> None:
        try:
            results[i] = await aw
        except BaseException as e:
            if return_exceptions:
                results[i] = e
            else:
                raise

    async with anyio.create_task_group() as tg:
        for i, aw in enumerate(awaitables):
            tg.start_soon(run_at, i, aw)

    return results
