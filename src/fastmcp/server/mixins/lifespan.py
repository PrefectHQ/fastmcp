"""Lifespan and Docket task infrastructure for FastMCP Server."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

import anyio
from uncalled_for import SharedContext

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
            # If docket is not available, skip task infrastructure but still
            # set up SharedContext so Shared() dependencies work.
            if not is_docket_available():
                async with SharedContext():
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

            # If no task-enabled components, skip Docket infrastructure but still
            # set up SharedContext so Shared() dependencies work.
            if not task_components:
                async with SharedContext():
                    yield
                return

            # Docket is available AND there are task-enabled components
            from docket import Depends, Docket, Worker

            from fastmcp import settings
            from fastmcp.server.dependencies import (
                _current_docket,
                _current_worker,
            )
            from fastmcp.server.tasks.context import restore_task_snapshot

            # Create Docket instance using configured name and URL
            async with Docket(
                name=settings.docket.name,
                url=settings.docket.url,
            ) as docket:
                self._docket = docket

                # Register task-enabled components with Docket
                for component in task_components:
                    component.register_with_docket(docket)

                docket_token = _current_docket.set(docket)
                try:
                    # Build worker kwargs from settings
                    worker_kwargs: dict[str, Any] = {
                        "concurrency": settings.docket.concurrency,
                        "redelivery_timeout": settings.docket.redelivery_timeout,
                        "reconnection_delay": settings.docket.reconnection_delay,
                        "minimum_check_interval": settings.docket.minimum_check_interval,
                    }
                    if settings.docket.worker_name:
                        worker_kwargs["name"] = settings.docket.worker_name

                    # Create and start Worker.  The restore_task_snapshot
                    # worker-level dependency runs before every task so the
                    # per-task snapshot ContextVar is populated before user
                    # code or task-scoped dependencies observe it.
                    async with Worker(
                        docket,
                        dependencies=[Depends(restore_task_snapshot)],
                        **worker_kwargs,
                    ) as worker:
                        self._worker = worker
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
                    _current_docket.reset(docket_token)
                    self._docket = None
        finally:
            # Reset server ContextVar
            _current_server.reset(server_token)

    @asynccontextmanager
    async def _lifespan_manager(self: FastMCP) -> AsyncIterator[None]:
        async with self._lifespan_lock:
            if self._lifespan_result_set:
                self._lifespan_ref_count += 1
                should_enter_lifespan = False
            else:
                self._lifespan_ref_count = 1
                should_enter_lifespan = True

        if not should_enter_lifespan:
            try:
                yield
            finally:
                async with self._lifespan_lock:
                    self._lifespan_ref_count -= 1
                    if self._lifespan_ref_count == 0:
                        self._lifespan_result_set = False
                        self._lifespan_result = None
            return

        # Use an explicit AsyncExitStack so we can shield teardown from
        # cancellation. Without this, Ctrl-C causes CancelledError to
        # propagate into lifespan finally blocks, preventing any async
        # cleanup (e.g. closing DB connections, flushing buffers).
        stack = AsyncExitStack()
        try:
            user_lifespan_result = await stack.enter_async_context(self._lifespan(self))
            await stack.enter_async_context(self._docket_lifespan())

            self._lifespan_result = user_lifespan_result
            self._lifespan_result_set = True

            # Start lifespans for all providers
            for provider in self.providers:
                await stack.enter_async_context(provider.lifespan())

            # After providers are up, adjust MCP handlers to reflect actual
            # backend capabilities (removes handlers for unsupported methods).
            self._sync_proxy_capabilities()

            self._started.set()
            try:
                yield
            finally:
                self._started.clear()
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await stack.aclose()
            finally:
                async with self._lifespan_lock:
                    self._lifespan_ref_count -= 1
                    if self._lifespan_ref_count == 0:
                        self._lifespan_result_set = False
                        self._lifespan_result = None

    def _sync_proxy_capabilities(self: FastMCP) -> None:
        """Remove MCP handlers for capabilities the backend does not support.

        After provider lifespans have run, any ProxyProvider instances have had a
        chance to preload their backend's serverCapabilities. If the backend doesn't
        support a capability (resources, prompts, tools) and there are no local
        components of that type either, we remove the corresponding request handlers
        from the low-level MCP server.

        This has two effects:
        1. The ``initialize`` response no longer advertises unsupported capabilities.
        2. Clients that try to use an unsupported method receive a proper
           ``METHOD_NOT_FOUND`` (-32601) JSON-RPC error instead of an empty list.

        The adjustment is conservative: if there are any providers whose capabilities
        are not known (i.e. not a LocalProvider or ProxyProvider with loaded caps),
        we leave the handlers untouched.
        """
        import mcp.types

        from fastmcp.server.providers.local_provider.local_provider import LocalProvider
        from fastmcp.server.providers.proxy import ProxyProvider
        from fastmcp.server.providers.wrapped_provider import _WrappedProvider

        def _unwrap(p: Any) -> Any:
            """Recursively unwrap _WrappedProvider to reach the inner provider."""
            while isinstance(p, _WrappedProvider):
                p = p._inner
            return p

        # Restore handlers to the baseline that was saved at construction time
        # so that a server reused across multiple lifespan cycles starts clean.
        baseline = getattr(self._mcp_server, "_baseline_request_handlers", None)
        if baseline is not None:
            self._mcp_server.request_handlers = dict(baseline)
        else:
            self._mcp_server._baseline_request_handlers = dict(  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
                self._mcp_server.request_handlers
            )

        # Unwrap all providers so we can inspect the actual provider type,
        # including namespaced providers wrapped in _WrappedProvider.
        unwrapped = [_unwrap(p) for p in self.providers]

        all_proxy_providers = [p for p in unwrapped if isinstance(p, ProxyProvider)]
        if not all_proxy_providers:
            return

        # If any ProxyProvider failed to preload capabilities, we can't safely
        # prune: that backend's capabilities are unknown and removing handlers
        # could break capabilities it can actually serve.
        if any(p._backend_capabilities is None for p in all_proxy_providers):
            return

        # Only adjust when every provider is either a LocalProvider or a
        # ProxyProvider with known capabilities. Unknown providers may have
        # components we can't inspect synchronously, so we leave things alone.
        if any(not isinstance(p, (LocalProvider, ProxyProvider)) for p in unwrapped):
            return

        # Aggregate: a capability is "supported" if ANY proxy backend supports it.
        backend_caps = [
            p._backend_capabilities
            for p in all_proxy_providers
            if p._backend_capabilities is not None
        ]
        any_resources = any(bool(c.resources) for c in backend_caps)
        any_prompts = any(bool(c.prompts) for c in backend_caps)
        any_tools = any(bool(c.tools) for c in backend_caps)

        # Check all LocalProvider instances for statically-registered components.
        # A user may pass additional LocalProvider instances via the providers kwarg,
        # so we aggregate across every LocalProvider in self.providers, not just
        # the server's built-in self._local_provider.
        from fastmcp.prompts.base import Prompt
        from fastmcp.resources.base import Resource
        from fastmcp.resources.template import ResourceTemplate
        from fastmcp.tools.base import Tool

        local_components = [
            c
            for p in unwrapped
            if isinstance(p, LocalProvider)
            for c in p._components.values()
        ]
        local_has_resources = any(
            isinstance(c, (Resource, ResourceTemplate)) for c in local_components
        )
        local_has_prompts = any(isinstance(c, Prompt) for c in local_components)
        local_has_tools = any(isinstance(c, Tool) for c in local_components)

        if not any_resources and not local_has_resources:
            self._mcp_server.request_handlers.pop(mcp.types.ListResourcesRequest, None)
            self._mcp_server.request_handlers.pop(
                mcp.types.ListResourceTemplatesRequest, None
            )
            self._mcp_server.request_handlers.pop(mcp.types.ReadResourceRequest, None)

        if not any_prompts and not local_has_prompts:
            self._mcp_server.request_handlers.pop(mcp.types.ListPromptsRequest, None)
            self._mcp_server.request_handlers.pop(mcp.types.GetPromptRequest, None)

        if not any_tools and not local_has_tools:
            self._mcp_server.request_handlers.pop(mcp.types.ListToolsRequest, None)
            self._mcp_server.request_handlers.pop(mcp.types.CallToolRequest, None)

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
