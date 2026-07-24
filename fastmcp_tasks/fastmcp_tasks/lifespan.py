"""Docket lifecycle for FastMCP background tasks.

Extracted from the SEP-1686 ``LifespanMixin._docket_lifespan`` and driven by
``TasksExtension.lifespan()``. Core's ``_extensions_lifespan`` already enters
this once per runtime tree at the root and defers on mounted children, and
``SharedContext`` plus the server ContextVar are established before extension
lifespans run — so this no longer manages either. It starts Docket and a Worker
when there are task-enabled components, registers those components' callables,
and runs the worker (with the snapshot-restore dependency) until shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP
    from fastmcp_tasks.settings import DocketSettings

logger = get_logger(__name__)


@asynccontextmanager
async def docket_lifespan(
    server: FastMCP, settings: DocketSettings
) -> AsyncIterator[None]:
    """Manage the Docket instance and Worker for background task execution.

    Sets ``server._docket`` / ``server._worker`` for the duration and registers
    each task-enabled component's callable, then runs the worker until the
    context exits. A no-op if pydocket is unavailable or the server declares no
    task-enabled components.
    """
    from docket import Depends, Docket, Worker

    import fastmcp
    from fastmcp_tasks.components import register_component_with_docket
    from fastmcp_tasks.context import restore_task_snapshot
    from fastmcp_tasks.dependencies import (
        _current_docket,
        _current_worker,
        is_docket_available,
    )

    if not is_docket_available():
        yield
        return

    try:
        candidates = list(await server.get_tasks())
    except Exception as e:
        logger.warning(f"Failed to collect task components: {e}")
        if fastmcp.settings.mounted_components_raise_on_load_error:
            raise
        candidates = []

    # get_tasks() applies server-level transforms that can inject non-task tools;
    # re-filter by the actual task config (the recorded landmine).
    task_components = [c for c in candidates if c.task_config.supports_tasks()]
    if not task_components:
        yield
        return

    async with Docket(name=settings.name, url=settings.url) as docket:
        server._docket = docket
        for component in task_components:
            register_component_with_docket(component, docket)

        docket_token = _current_docket.set(docket)
        try:
            worker_kwargs: dict[str, Any] = {
                "concurrency": settings.concurrency,
                "redelivery_timeout": settings.redelivery_timeout,
                "reconnection_delay": settings.reconnection_delay,
                "minimum_check_interval": settings.minimum_check_interval,
            }
            if settings.worker_name:
                worker_kwargs["name"] = settings.worker_name

            async with Worker(
                docket,
                dependencies=[Depends(restore_task_snapshot)],
                **worker_kwargs,
            ) as worker:
                server._worker = worker
                worker_token = _current_worker.set(worker)
                try:
                    worker_task = asyncio.create_task(worker.run_forever())
                    try:
                        yield
                    finally:
                        # End-and-reenter never parks a worker on input, so a
                        # task waiting for input holds no worker slot: cancelling
                        # run_forever drains promptly regardless of task state.
                        worker_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await worker_task
                finally:
                    _current_worker.reset(worker_token)
                    server._worker = None
        finally:
            _current_docket.reset(docket_token)
            server._docket = None
