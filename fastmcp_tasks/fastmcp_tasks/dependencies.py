"""Docket-specific dependency injection for FastMCP background tasks.

Moved out of ``fastmcp.server.dependencies`` during the SEP-1686 -> SEP-2663
migration. These helpers are all docket-touching: the ``require_docket``
install-hint, the docket/worker ContextVars, and the ``CurrentDocket`` /
``CurrentWorker`` dependencies. Everything here is wire-agnostic engine plumbing
that ``TasksExtension`` drives.

The generic ``is_docket_available`` probe stays in ``fastmcp.server.dependencies``
(core's ``Context``/``Progress`` still use it) and is re-exported here for the
tasks package's callers.
"""

from __future__ import annotations

import importlib.metadata
from contextvars import ContextVar
from types import TracebackType
from typing import TYPE_CHECKING, cast

from uncalled_for import Dependency

from fastmcp.server.dependencies import (
    _MIN_DOCKET_VERSION,
    get_server,
    is_docket_available,
)

if TYPE_CHECKING:
    from docket import Docket
    from docket.worker import Worker

__all__ = [
    "CurrentDocket",
    "CurrentWorker",
    "is_docket_available",
    "require_docket",
]


_current_docket: ContextVar[Docket | None] = ContextVar("docket", default=None)
_current_worker: ContextVar[Worker | None] = ContextVar("worker", default=None)


def require_docket(feature: str) -> None:
    """Raise ImportError with install instructions if docket not available.

    Args:
        feature: Description of what requires docket (e.g., "`task=True`",
                 "CurrentDocket()"). Will be included in the error message.
    """
    if is_docket_available():
        return

    try:
        installed = importlib.metadata.version("pydocket")
    except importlib.metadata.PackageNotFoundError:
        installed = None

    if installed is None:
        detail = (
            "FastMCP background tasks require the `tasks` extra. "
            "Install with: pip install 'fastmcp[tasks]'."
        )
    else:
        detail = (
            f"FastMCP background tasks require pydocket>={_MIN_DOCKET_VERSION}, "
            f"but pydocket {installed} is installed (likely pulled in by another "
            f"package). Upgrade with: pip install -U 'pydocket>={_MIN_DOCKET_VERSION}'."
        )

    raise ImportError(f"{detail} (Triggered by {feature})")


class _CurrentDocket(Dependency["Docket"]):
    """Async context manager for Docket dependency."""

    async def __aenter__(self) -> Docket:
        require_docket("CurrentDocket()")
        # Check server instance first, fall back to ContextVar for mounted children
        # whose parent owns the Docket
        try:
            docket = get_server()._docket
        except RuntimeError:
            docket = None
        if docket is None:
            docket = _current_docket.get()
        if docket is None:
            raise RuntimeError(
                "No Docket instance found. Docket is only initialized when there are "
                "task-enabled components (task=True). Add task=True to a component "
                "to enable Docket infrastructure."
            )
        return docket

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentDocket() -> Docket:
    """Get the current Docket instance managed by FastMCP.

    This dependency provides access to the Docket instance that FastMCP
    automatically creates for background task scheduling.

    Returns:
        A dependency that resolves to the active Docket instance

    Raises:
        RuntimeError: If not within a FastMCP server context
        ImportError: If fastmcp[tasks] not installed

    Example:
        ```python
        from fastmcp_tasks.dependencies import CurrentDocket

        @mcp.tool()
        async def schedule_task(docket: Docket = CurrentDocket()) -> str:
            await docket.add(some_function)(arg1, arg2)
            return "Scheduled"
        ```
    """
    require_docket("CurrentDocket()")
    return cast("Docket", _CurrentDocket())


class _CurrentWorker(Dependency["Worker"]):
    """Async context manager for Worker dependency."""

    async def __aenter__(self) -> Worker:
        require_docket("CurrentWorker()")
        # Check server instance first, fall back to ContextVar for mounted children
        try:
            worker = get_server()._worker
        except RuntimeError:
            worker = None
        if worker is None:
            worker = _current_worker.get()
        if worker is None:
            raise RuntimeError(
                "No Worker instance found. Worker is only initialized when there are "
                "task-enabled components (task=True). Add task=True to a component "
                "to enable Docket infrastructure."
            )
        return worker

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentWorker() -> Worker:
    """Get the current Docket Worker instance managed by FastMCP.

    This dependency provides access to the Worker instance that FastMCP
    automatically creates for background task processing.

    Returns:
        A dependency that resolves to the active Worker instance

    Raises:
        RuntimeError: If not within a FastMCP server context
        ImportError: If fastmcp[tasks] not installed

    Example:
        ```python
        from fastmcp_tasks.dependencies import CurrentWorker

        @mcp.tool()
        async def check_worker_status(worker: Worker = CurrentWorker()) -> str:
            return f"Worker: {worker.name}"
        ```
    """
    require_docket("CurrentWorker()")
    return cast("Worker", _CurrentWorker())
