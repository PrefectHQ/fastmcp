"""Regression tests for Monty external callback ownership."""

import asyncio
import importlib.util
from typing import Any

import anyio
import pytest

from fastmcp.experimental.transforms.code_mode import MontySandboxProvider

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pydantic_monty") is None,
    reason="pydantic-monty is required for the real Monty sandbox provider",
)


@pytest.mark.parametrize("raises", [False, True])
async def test_monty_provider_waits_for_unawaited_callback_cleanup(
    raises: bool,
) -> None:
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def background() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    async def wait_until_started() -> None:
        await started.wait()

    provider = MontySandboxProvider()
    code = "background()\nawait wait_until_started()"
    if raises:
        code += "\nraise ValueError('sandbox failed')"
    execution = provider.run(
        code,
        external_functions={
            "background": background,
            "wait_until_started": wait_until_started,
        },
    )
    if raises:
        with pytest.raises(Exception, match="sandbox failed"):
            await execution
    else:
        await execution
    assert cleaned_up.is_set()


@pytest.mark.parametrize("anyio_cancellation", [False, True])
async def test_monty_provider_joins_callbacks_on_cancellation(
    anyio_cancellation: bool,
) -> None:
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def background() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    provider = MontySandboxProvider()
    if anyio_cancellation:
        async with anyio.create_task_group() as tg:

            async def run() -> None:
                await provider.run(
                    "await background()", external_functions={"background": background}
                )

            tg.start_soon(run)
            await started.wait()
            tg.cancel_scope.cancel()
    else:
        task = asyncio.create_task(
            provider.run(
                "await background()", external_functions={"background": background}
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cleaned_up.is_set()


async def test_monty_provider_rejects_callbacks_queued_after_exit() -> None:
    callbacks: dict[str, Any] = {}
    called = False

    async def callback() -> None:
        nonlocal called
        called = True

    class QueuedCallbackProvider(MontySandboxProvider):
        async def _run_monty(
            self,
            pydantic_monty: Any,
            *,
            code: str,
            inputs: Any,
            external_functions: Any,
        ) -> None:
            callbacks.update(external_functions)

    await QueuedCallbackProvider().run(
        "callback()", external_functions={"callback": callback}
    )
    with pytest.raises(asyncio.CancelledError):
        await callbacks["callback"]()
    assert not called
