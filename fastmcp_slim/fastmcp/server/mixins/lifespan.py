"""Lifespan infrastructure for FastMCP Server."""

from __future__ import annotations

import weakref
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

import anyio
from uncalled_for import SharedContext

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


# Set True by `FastMCPProvider.lifespan` immediately before it enters the
# wrapped (mounted) server's `_lifespan_manager`, and reset on exit. The
# mounted server's `_shared_context_lifespan` reads this and becomes a no-op so
# that SharedContext and the server ContextVar are not re-initialized — there's
# one set per runtime tree, owned by the root. Extension lifespans (e.g. the
# tasks extension's Docket/Worker) defer to the root the same way.
#
# Independent servers entered as siblings (e.g. via `AsyncExitStack` in the
# same async context) are NOT in a parent/child relationship; the flag is not
# set in that case, so each independently establishes its own server context.
_lifespan_root_active: ContextVar[bool] = ContextVar(
    "fastmcp_lifespan_root_active", default=False
)


class LifespanMixin:
    """Mixin providing lifespan infrastructure for FastMCP."""

    @property
    def docket(self: FastMCP) -> Docket | None:
        """The Docket instance owned by this server, if the tasks extension is active.

        Returns the Docket that the tasks extension initialized as the root of a
        runtime tree, or None when no task backend is running. Mounted children do
        not own their own Docket — they share the root's via ``_current_docket``
        ContextVar inheritance — so accessing ``.docket`` on a mounted child
        returns None even while its tasks run on the root's Docket.
        """
        return self._docket

    @asynccontextmanager
    async def _shared_context_lifespan(self: FastMCP) -> AsyncIterator[None]:
        """Set up the process-level ``SharedContext`` and server ContextVar.

        ``SharedContext`` backs app-scoped ``Shared()`` dependencies and is
        process-level, not server-level: only the first server in a runtime tree
        establishes it. Mounted children entered via ``FastMCPProvider.lifespan``
        see ``_lifespan_root_active=True`` (set by the provider before delegating
        to ``_lifespan_manager``) and become no-ops, sharing the root's context
        via ContextVars.

        Independent servers entered as siblings — for example two unrelated
        ``FastMCP`` instances each entered through ``AsyncExitStack`` in the same
        async context — are not in a parent/child relationship; no provider has
        set the flag for them, so each runs the full root setup.
        """
        if _lifespan_root_active.get():
            yield
            return

        from fastmcp.server.dependencies import _current_server

        # Set FastMCP server in ContextVar so CurrentFastMCP can access it
        # (use weakref to avoid reference cycles)
        server_token = _current_server.set(weakref.ref(self))
        try:
            async with SharedContext():
                self._capture_shared_context()
                yield
        finally:
            _current_server.reset(server_token)

    @asynccontextmanager
    async def _extensions_lifespan(self: FastMCP) -> AsyncIterator[None]:
        """Enter each registered extension's lifespan, exit them on shutdown.

        Extension lifespans are entered once per runtime tree, at the root. A
        mounted child sees ``_lifespan_root_active`` set by its
        ``FastMCPProvider`` and defers to the root: an extension whose lifespan
        starts shared infrastructure (a task-queue backend and worker, say) is
        therefore owned by the tree root, and mounted children reach it through
        the same context rather than starting a second copy.

        Extensions are entered in registration order; the ``AsyncExitStack``
        exits them in reverse on teardown.
        """
        if _lifespan_root_active.get() or not self._extensions:
            yield
            return

        async with AsyncExitStack() as stack:
            for extension in self._extensions.values():
                await stack.enter_async_context(extension.lifespan())
            yield

    async def _validate_task_extension_registered(self: FastMCP) -> None:
        """Fail loudly if a task-enabled tool has no tasks extension registered.

        `task=True` on a tool is only an intent declaration; the engine that runs
        it lives in the `fastmcp-tasks` package and is installed by registering a
        `ServerExtension` whose identifier is `TASKS_EXTENSION_ID`
        (`mcp.add_extension(...)`). A task-configured tool serving without that
        extension would silently never run as a task — a correctness bug — so we
        raise at serve time instead.
        """
        from fastmcp.utilities.tasks import TASKS_EXTENSION_ID

        # A mounted child defers to the root, which owns the extension and whose
        # aggregated get_tasks() already covers this child's task tools — the
        # same root-deferral the extension lifespan uses. Validating here would
        # fail a child that legitimately relies on the root's registration.
        if _lifespan_root_active.get():
            return

        if TASKS_EXTENSION_ID in self._extensions:
            return

        candidates = list(await self.get_tasks())

        # ``get_tasks()`` applies server-level transforms, which can inject
        # non-task tools (e.g. ResourcesAsTools' synthetic list/read tools) into
        # the result, so re-filter by the actual task config here — mirroring the
        # guard the old per-component docket registration applied.
        task_components = [c for c in candidates if c.task_config.supports_tasks()]
        if not task_components:
            return

        names = ", ".join(sorted(c.name for c in task_components))
        raise RuntimeError(
            f"Task-enabled tools ({names}) require the tasks extension, but no "
            f"extension with identifier {TASKS_EXTENSION_ID!r} is registered. "
            "Install it with `pip install 'fastmcp[tasks]'` and register it via "
            "`mcp.add_extension(TasksExtension(...))`."
        )

    def _capture_shared_context(self: FastMCP) -> None:
        """Snapshot the live ``SharedContext`` ContextVar values.

        The SDK v2 dispatcher runs each request handler in the *message
        sender's* contextvars (via ``ContextReceiveStream.last_context``), not
        the server-lifespan context. App-scoped ``Shared()`` dependencies rely
        on ``uncalled_for.SharedContext`` ContextVars set during the lifespan,
        which are therefore invisible to handlers. We capture those values here
        so ``FastMCPServerMiddleware`` can re-apply them per request.
        """
        try:
            self._shared_context_snapshot = {
                SharedContext.resolved: SharedContext.resolved.get(),
                SharedContext.lock: SharedContext.lock.get(),
                SharedContext.stack: SharedContext.stack.get(),
            }
        except LookupError:  # pragma: no cover - SharedContext not active
            self._shared_context_snapshot = None

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
            await stack.enter_async_context(self._shared_context_lifespan())
            await stack.enter_async_context(self._extensions_lifespan())

            self._lifespan_result = user_lifespan_result
            self._lifespan_result_set = True

            # Start lifespans for all providers
            for provider in self.providers:
                await stack.enter_async_context(provider.lifespan())

            await self._validate_task_extension_registered()

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
