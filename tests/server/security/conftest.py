from __future__ import annotations

import asyncio
import inspect
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def ensure_default_event_loop(request) -> Generator[None, None, None]:
    """Restore a default event loop for sync security tests.

    A number of older security tests use ``asyncio.get_event_loop()`` directly
    from synchronous test functions. Python 3.12 no longer guarantees an
    implicit loop in that situation, so we provision one here for this test
    package only.
    """

    test_fn = getattr(request.node, "obj", None)
    if inspect.iscoroutinefunction(test_fn):
        yield
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        loop.close()
        asyncio.set_event_loop(None)
