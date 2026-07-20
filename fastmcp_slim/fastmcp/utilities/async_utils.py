"""Async utilities for FastMCP."""

import functools
import inspect
from collections.abc import Awaitable, Callable, Iterable
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
    awaitables: Iterable[Awaitable[T]],
    *,
    return_exceptions: Literal[True],
) -> list[T | BaseException]: ...


@overload
async def gather(
    awaitables: Iterable[Awaitable[T]],
    *,
    return_exceptions: Literal[False] = ...,
) -> list[T]: ...


async def gather(
    awaitables: Iterable[Awaitable[T]],
    *,
    return_exceptions: bool = False,
) -> list[T] | list[T | BaseException]:
    """Run awaitables concurrently and return results in order.

    Uses anyio TaskGroup for structured concurrency.

    ``awaitables`` is consumed lazily, one item at a time, right before each
    is handed to the task group. Callers with a dynamic number of awaitables
    should pass a generator expression (e.g. ``gather(f(x) for x in xs)``)
    rather than a list or list comprehension: a list comprehension calls
    every ``f(x)`` up front, creating a batch of coroutine objects before
    this function even starts, whereas a generator expression creates each
    coroutine only as this function's own scheduling loop asks for it. That
    matters because coroutine creation and scheduling can be interrupted
    between any two bytecode instructions by a synchronous signal handler
    (for example pytest-timeout's SIGALRM-based per-test timeout). If that
    happens while a whole batch of coroutines is sitting unscheduled, they
    are silently abandoned and eventually trigger a "coroutine was never
    awaited" warning attributed to whatever unrelated code happens to be
    running when the garbage collector gets to them. Lazy consumption keeps
    the window in which a created-but-unscheduled coroutine can exist as
    small as possible.

    Args:
        awaitables: Iterable of awaitables to run concurrently.
        return_exceptions: If True, exceptions are returned in results.
                          If False, first exception cancels all and raises.

    Returns:
        List of results in the same order as input awaitables.
    """
    results: list[T | BaseException] = []

    async def run_at(i: int, aw: Awaitable[T]) -> None:
        try:
            results[i] = await aw
        except BaseException as e:
            if return_exceptions:
                results[i] = e
            else:
                raise

    pending = enumerate(awaitables)
    async with anyio.create_task_group() as tg:
        for i, aw in pending:
            results.append(None)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            try:
                tg.start_soon(run_at, i, aw)
            except BaseException:
                # `aw` was just created (possibly moments ago, by the
                # generator's own iteration) but never handed off - close it
                # explicitly so it isn't silently garbage collected later.
                if inspect.iscoroutine(aw):
                    aw.close()
                # Lazy consumption keeps the leak window small, but a caller
                # that passed an already-built sequence has coroutines sitting
                # behind this one that were never scheduled either. Draining
                # the iterator closes them too, so `gather` cannot leak
                # regardless of how eagerly its argument was constructed.
                for _, remaining in pending:
                    if inspect.iscoroutine(remaining):
                        remaining.close()
                raise

    return results
