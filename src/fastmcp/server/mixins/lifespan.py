"""Lifespan and Docket task infrastructure for FastMCP Server."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

import fastmcp
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class LifespanMixin:
    """Mixin providing lifespan and Docket task infrastructure for FastMCP."""

    @property
    def docket(self: FastMCP) -> Docket | None:
        """Get the Docket instance if Docket support is enabled.

        Returns None if Docket is not enabled or server hasn't been started yet.
        """
        return self._docket

    @asynccontextmanager
    async def _docket_lifespan(self: FastMCP) -> AsyncIterator[None]:
        """Manage Docket instance and Worker for background task execution.

        Docket infrastructure is only initialized if:
        1. pydocket is installed (fastmcp[tasks] extra)
        2. There are task-enabled components (task_config.mode != 'forbidden')

        This means users with pydocket installed but no task-enabled components
        won't spin up Docket/Worker infrastructure.
        """
        from fastmcp.server.dependencies import _current_server, is_docket_available

        # Set FastMCP server in ContextVar so CurrentFastMCP can access it
        # (use weakref to avoid reference cycles)
        server_token = _current_server.set(weakref.ref(self))

        try:
            # If docket is not available, skip task infrastructure
            if not is_docket_available():
                yield
                return

            # Collect task-enabled components at startup with all transforms applied.
            # Components must be available now to be registered with Docket workers;
            # dynamically added components after startup won't be registered.
            try:
                task_components = list(await self.get_tasks())
            except Exception as e:
                logger.warning(f"Failed to get tasks: {e}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                task_components = []

            # If no task-enabled components, skip Docket infrastructure entirely
            if not task_components:
                yield
                return

            # Docket is available AND there are task-enabled components
            from docket import Docket, Worker

            from fastmcp import settings
            from fastmcp.server.dependencies import (
                _current_docket,
                _current_worker,
            )

            # Create Docket instance using configured name and URL
            async with Docket(
                name=settings.docket.name,
                url=settings.docket.url,
            ) as docket:
                # Store on server instance for cross-task access (FastMCPTransport)
                self._docket = docket

                # Register task-enabled components with Docket
                for component in task_components:
                    component.register_with_docket(docket)

                # Set Docket in ContextVar so CurrentDocket can access it
                docket_token = _current_docket.set(docket)
                try:
                    # Build worker kwargs from settings
                    worker_kwargs: dict[str, Any] = {
                        "concurrency": settings.docket.concurrency,
                        "redelivery_timeout": settings.docket.redelivery_timeout,
                        "reconnection_delay": settings.docket.reconnection_delay,
                    }
                    if settings.docket.worker_name:
                        worker_kwargs["name"] = settings.docket.worker_name

                    # Create and start Worker
                    async with Worker(docket, **worker_kwargs) as worker:
                        # Store on server instance for cross-context access
                        self._worker = worker
                        # Set Worker in ContextVar so CurrentWorker can access it
                        worker_token = _current_worker.set(worker)
                        try:
                            worker_task = asyncio.create_task(worker.run_forever())
                            try:
                                yield
                            finally:
                                worker_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await worker_task
                        finally:
                            _current_worker.reset(worker_token)
                            self._worker = None
                finally:
                    # Reset ContextVar
                    _current_docket.reset(docket_token)
                    # Clear instance attribute
                    self._docket = None
        finally:
            # Reset server ContextVar
            _current_server.reset(server_token)

    @asynccontextmanager
    async def _lifespan_manager(self: FastMCP) -> AsyncIterator[None]:
        if self._lifespan_result_set:
            yield
            return

        async with (
            self._lifespan(self) as user_lifespan_result,
            self._docket_lifespan(),
        ):
            self._lifespan_result = user_lifespan_result
            self._lifespan_result_set = True

            async with AsyncExitStack[bool | None]() as stack:
                # Start lifespans for all providers
                for provider in self.providers:
                    await stack.enter_async_context(provider.lifespan())

                self._started.set()
                try:
                    yield
                finally:
                    self._started.clear()

        self._lifespan_result_set = False
        self._lifespan_result = None

    def _setup_task_protocol_handlers(self: FastMCP) -> None:
        """Register SEP-1686 task protocol handlers with SDK.

        Only registers handlers if docket is installed. Without docket,
        task protocol requests will return "method not found" errors.
        """
        from fastmcp.server.dependencies import is_docket_available

        if not is_docket_available():
            return

        from mcp.types import (
            CancelTaskRequest,
            GetTaskPayloadRequest,
            GetTaskRequest,
            ListTasksRequest,
            ServerResult,
        )

        from fastmcp.server.tasks.requests import (
            tasks_cancel_handler,
            tasks_get_handler,
            tasks_list_handler,
            tasks_result_handler,
        )

        # Manually register handlers (SDK decorators fail with locally-defined functions)
        # SDK expects handlers that receive Request objects and return ServerResult

        async def handle_get_task(req: GetTaskRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_get_handler(self, params)
            return ServerResult(result)

        async def handle_get_task_result(req: GetTaskPayloadRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_result_handler(self, params)
            return ServerResult(result)

        async def handle_list_tasks(req: ListTasksRequest) -> ServerResult:
            params = (
                req.params.model_dump(by_alias=True, exclude_none=True)
                if req.params
                else {}
            )
            result = await tasks_list_handler(self, params)
            return ServerResult(result)

        async def handle_cancel_task(req: CancelTaskRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_cancel_handler(self, params)
            return ServerResult(result)

        # Register directly with SDK (same as what decorators do internally)
        self._mcp_server.request_handlers[GetTaskRequest] = handle_get_task
        self._mcp_server.request_handlers[GetTaskPayloadRequest] = (
            handle_get_task_result
        )
        self._mcp_server.request_handlers[ListTasksRequest] = handle_list_tasks
        self._mcp_server.request_handlers[CancelTaskRequest] = handle_cancel_task
