"""The SEP-2663 tasks extension: `io.modelcontextprotocol/tasks`.

`TasksExtension` is the wire adapter that turns FastMCP's task engine into an
`io.modelcontextprotocol/tasks` server extension. Registering it enables
`task=True` tools:

```python
from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension

mcp = FastMCP("Server")
mcp.add_extension(TasksExtension(url="redis://localhost:6379/0"))


@mcp.tool(task=True)
async def crunch(dataset: str) -> str:
    ...
```

The extension contributes the negotiated capability, the three additive request
methods (`tasks/get`, `tasks/update`, `tasks/cancel`), a `tools/call` interceptor
that decides whether to run a call as a task, and a lifespan that starts the
Docket backend/worker and installs the worker-side `Context` hooks core exposes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import extract_version_spec
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp.utilities.versions import VersionSpec
from fastmcp_tasks.creation import create_task
from fastmcp_tasks.handlers import tasks_cancel, tasks_get, tasks_update
from fastmcp_tasks.models import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    CancelTaskParams,
    CancelTaskResult,
    GetTaskParams,
    GetTaskResult,
    UpdateTaskParams,
    UpdateTaskResult,
    missing_capability_error_data,
)
from fastmcp_tasks.settings import DocketSettings

if TYPE_CHECKING:
    import mcp_types

    from fastmcp.server.context import Context
    from fastmcp.server.extensions import ToolCallContinuation, ToolCallOutcome

logger = get_logger(__name__)

# SEP-2663's request methods exist only at the 2026-07-28 era (the extensions
# mechanism itself is era-gated). Off that era the methods report as not found.
_TASK_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)


class TasksExtension(ServerExtension):
    """FastMCP server extension implementing SEP-2663 background tasks.

    Construct with backend/worker configuration; anything omitted falls back to
    the ``FASTMCP_DOCKET_*`` environment defaults (unchanged from FastMCP 3), so
    ``TasksExtension()`` works out of the box on an env-configured deployment.
    """

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self,
        *,
        url: str | None = None,
        name: str | None = None,
        worker_name: str | None = None,
        concurrency: int | None = None,
        redelivery_timeout: timedelta | None = None,
        reconnection_delay: timedelta | None = None,
        minimum_check_interval: timedelta | None = None,
    ) -> None:
        overrides: dict[str, Any] = {
            "url": url,
            "name": name,
            "worker_name": worker_name,
            "concurrency": concurrency,
            "redelivery_timeout": redelivery_timeout,
            "reconnection_delay": reconnection_delay,
            "minimum_check_interval": minimum_check_interval,
        }
        self._settings = DocketSettings(
            **{k: v for k, v in overrides.items() if v is not None}
        )

    @property
    def docket_settings(self) -> DocketSettings:
        """The resolved Docket settings (backend URL, worker options)."""
        return self._settings

    def settings(self) -> dict[str, Any]:
        """The tasks extension advertises no per-extension settings."""
        return {}

    def methods(self) -> Sequence[MethodBinding]:
        return [
            MethodBinding(
                method="tasks/get",
                params_type=GetTaskParams,
                handler=self._handle_get,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="tasks/update",
                params_type=UpdateTaskParams,
                handler=self._handle_update,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="tasks/cancel",
                params_type=CancelTaskParams,
                handler=self._handle_cancel,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
        ]

    def _require_tasks_capability(self, ctx: ServerRequestContext[Any, Any]) -> None:
        """Reject a task method from a client that did not declare the extension.

        SEP-2663: a client issuing `tasks/get`/`tasks/update`/`tasks/cancel`
        without the tasks capability in the request's `_meta` gets -32003. A
        client normally only holds a taskId because it declared the capability
        on the creating `tools/call`, but the method-level check is an explicit
        MUST, so enforce it here rather than assume.
        """
        if read_client_extension_settings(ctx, TASKS_EXTENSION_ID) is None:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=(
                    "This request targets the tasks extension "
                    f"({TASKS_EXTENSION_ID}); the client did not declare it for "
                    "this request."
                ),
                data=missing_capability_error_data(),
            )

    async def _handle_get(
        self, ctx: ServerRequestContext[Any, Any], params: GetTaskParams
    ) -> GetTaskResult:
        self._require_tasks_capability(ctx)
        return await tasks_get(self.server, params.task_id)

    async def _handle_update(
        self, ctx: ServerRequestContext[Any, Any], params: UpdateTaskParams
    ) -> UpdateTaskResult:
        self._require_tasks_capability(ctx)
        return await tasks_update(self.server, params.task_id, params.input_responses)

    async def _handle_cancel(
        self, ctx: ServerRequestContext[Any, Any], params: CancelTaskParams
    ) -> CancelTaskResult:
        self._require_tasks_capability(ctx)
        return await tasks_cancel(self.server, params.task_id)

    async def intercept_tool_call(
        self,
        params: mcp_types.CallToolRequestParams,
        context: Context,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        """Decide whether to run this ``tools/call`` as a task.

        Consults the tool's ``TaskConfig`` mode and the client's per-request
        opt-in: ``required`` always tasks (raising -32003 if the client did not
        opt in), ``optional`` tasks only when the client opted in, ``forbidden``
        never tasks. A non-task call passes straight through to the tool body.
        """
        # Resolve the same version core would dispatch: a versioned tools/call
        # carries its VersionSpec in the request _meta, so omitting it here would
        # task the highest version even when the client targeted an older one
        # (which may differ in task mode or implementation).
        version_str = extract_version_spec(params.meta)
        version = VersionSpec(eq=version_str) if version_str else None
        try:
            tool = await context.fastmcp.get_tool(params.name, version)
        except NotFoundError:
            tool = None
        if tool is None or not tool.task_config.supports_tasks():
            return await call_next()

        # Extension negotiation exists only on the modern era: the SDK strips
        # `capabilities.extensions` from pre-2026 handshakes, so a legacy client
        # cannot have negotiated this extension — a `_meta` opt-in arriving on a
        # handshake-era connection is treated as absent. This also keeps a
        # `CreateTaskResult` off legacy connections, whose result validation
        # does not admit it.
        rc = context.request_context
        on_modern_era = (
            rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS
        )
        opted_in = (
            on_modern_era
            and context.client_extension_settings(TASKS_EXTENSION_ID) is not None
        )
        mode = tool.task_config.mode

        if mode == "required":
            if not opted_in:
                raise MCPError(
                    code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                    message=(
                        f"Tool {tool.name!r} requires the tasks extension "
                        f"({TASKS_EXTENSION_ID}); the client did not declare it "
                        "for this request."
                    ),
                    data=missing_capability_error_data(),
                )
            return await create_task(tool, params.arguments, context)

        if mode == "optional" and opted_in:
            return await create_task(tool, params.arguments, context)

        return await call_next()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start the Docket backend/worker and install the worker-side hooks.

        Installs core's background-context factory and worker-server resolver for
        the duration so a worker's ``ctx`` (progress, server resolution) works,
        then runs the Docket lifespan. The hooks are process-global and
        refcounted: with several servers in one process (each its own
        runtime-tree root), the hooks stay installed until the last tasks
        extension shuts down, so one server's exit cannot strand another
        server's in-flight workers.
        """
        from fastmcp_tasks.lifespan import docket_lifespan

        _install_worker_hooks()
        try:
            async with docket_lifespan(self.server, self._settings):
                yield
        finally:
            _release_worker_hooks()


# The worker-side hooks core exposes are process-global, but several servers in
# one process may each run a TasksExtension (sibling roots in tests, or two
# apps sharing an interpreter). Refcount the installs so the hooks are cleared
# only when the last active extension lifespan exits. The installed callables
# are stateless module functions that resolve their target per task, so
# repeated installs are idempotent.
_active_worker_hook_holds: int = 0


def _install_worker_hooks() -> None:
    from fastmcp.server.dependencies import (
        set_background_context_factory,
        set_worker_server_resolver,
    )
    from fastmcp_tasks import wire_production
    from fastmcp_tasks.context import make_task_context, resolve_worker_server

    global _active_worker_hook_holds
    _active_worker_hook_holds += 1
    set_background_context_factory(make_task_context)
    set_worker_server_resolver(resolve_worker_server)
    # Enable server-side production of the claimed CreateTaskResult on tools/call
    # (the SDK ships only claim consumption). Refcounted independently but
    # installed/released in lockstep with the worker hooks.
    wire_production.install()


def _release_worker_hooks() -> None:
    from fastmcp.server.dependencies import (
        set_background_context_factory,
        set_worker_server_resolver,
    )
    from fastmcp_tasks import wire_production

    global _active_worker_hook_holds
    _active_worker_hook_holds -= 1
    if _active_worker_hook_holds <= 0:
        _active_worker_hook_holds = 0
        set_worker_server_resolver(None)
        set_background_context_factory(None)
    wire_production.uninstall()
