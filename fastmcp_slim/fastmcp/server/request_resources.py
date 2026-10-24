"""Lifecycle for internal resources owned by one inbound MCP request."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Generic, TypeVar, cast

import anyio

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ResourceT = TypeVar("ResourceT")


class RequestResourceKey(Generic[ResourceT]):
    """An identity key carrying the type of one internal request resource."""


class _RequestResources:
    """Resources retained until root dispatch finishes a request."""

    def __init__(self) -> None:
        self.resources: dict[RequestResourceKey[Any], Any] = {}
        self.cleanup_callbacks: list[Callable[[], Awaitable[None]]] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        callbacks, self.cleanup_callbacks = self.cleanup_callbacks, []
        self.resources.clear()
        for callback in reversed(callbacks):
            try:
                with anyio.CancelScope(shield=True):
                    await callback()
            except Exception:
                # Dispatch already owns the operation result or error. Cleanup
                # must not replace it while the response unwinds to the wire.
                logger.warning("Request resource cleanup failed", exc_info=True)


_request_resources: ContextVar[_RequestResources | None] = ContextVar(
    "fastmcp_request_resources", default=None
)


@asynccontextmanager
async def request_resource_scope() -> AsyncIterator[None]:
    """Bind and clean the resource owner for one root-dispatched request."""
    resources = _RequestResources()
    token = _request_resources.set(resources)
    try:
        yield
    finally:
        try:
            await resources.close()
        finally:
            _request_resources.reset(token)


def request_resources_active() -> bool:
    resources = _request_resources.get()
    return resources is not None and not resources.closed


def get_request_resource(
    key: RequestResourceKey[ResourceT],
) -> ResourceT | None:
    resources = _request_resources.get()
    if resources is None or resources.closed:
        return None
    return cast("ResourceT | None", resources.resources.get(key))


def set_request_resource(
    key: RequestResourceKey[ResourceT],
    value: ResourceT,
    cleanup: Callable[[], Awaitable[None]] | None = None,
) -> None:
    resources = _request_resources.get()
    if resources is None or resources.closed:
        raise RuntimeError("No active FastMCP request resource scope")
    if key in resources.resources:
        raise RuntimeError("Request resource is already set")
    resources.resources[key] = value
    if cleanup is not None:
        resources.cleanup_callbacks.append(cleanup)
