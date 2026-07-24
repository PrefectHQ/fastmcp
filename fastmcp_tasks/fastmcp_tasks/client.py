"""SEP-2663 client task support: the tasks extension, resolver, and handle.

FastMCP drives a server's background tasks transparently. When a `task=True`
tool runs a call as a task, the server answers `tools/call` with a claimed
`CreateTaskResult` (SEP-2133) instead of the tool's result. This module supplies
the client half:

- `TasksClientExtension` advertises the tasks capability (so the server *may*
  task the call) and declares a `ResultClaim` for `resultType: "task"`. It is
  registered on every FastMCP `Client` automatically, so the caller opts in to
  nothing.
- The claim's resolver polls `tasks/get` to completion under the hood and returns
  the tool's real result as a `CallToolResult` — the caller of `call_tool` never
  learns the call was tasked. A task that pauses for input is answered through the
  client's `elicitation_handler` via `tasks/update`, then polling resumes.
- `ToolTask` is the explicit handle for callers who want to return immediately and
  drive the task themselves (`status`/`wait`/`result`/`cancel`), built via
  `call_tool_task`.

Tasks are modern-protocol only: on a legacy connection the SDK strips the
capability ad, the server never tasks, and this extension is inert.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.client.session import ClientRequestContext, ClientSession, ElicitationFnT
from mcp_types import CallToolResult
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.telemetry import client_span
from fastmcp.exceptions import ToolError
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp.utilities.timeout import normalize_timeout_to_seconds
from fastmcp_tasks.client_models import (
    CancelTaskRequest,
    CancelTaskRequestParams,
    ClientCreateTaskResult,
    ClientGetTaskResult,
    GetTaskRequest,
    GetTaskRequestParams,
    UpdateTaskRequest,
    UpdateTaskRequestParams,
)
from fastmcp_tasks.settings import client_settings

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult as FastMCPCallToolResult
    from fastmcp.client.client import Client

logger = get_logger(__name__)

#: Floor for the fallback poll interval (seconds). When the server does not
#: advertise a `pollIntervalMs`, each drive starts its backoff ramp here so quick
#: tasks resolve fast; when it does advertise one, this floors it so a server
#: sending `0` cannot spin the client in a tight loop.
MIN_POLL_INTERVAL = 0.02

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Wire senders (tasks/get, tasks/update, tasks/cancel) over a ClientSession
# ---------------------------------------------------------------------------


def _trace_meta() -> mcp_types.RequestParamsMeta | None:
    """Trace context for a task management request, for the current client span.

    Task management calls (`tasks/get`/`update`/`cancel`) use ordinary
    client-to-server trace propagation, so their server-side spans nest under
    the client span rather than becoming disconnected trace roots.
    """
    return cast("mcp_types.RequestParamsMeta | None", inject_trace_context(None))


async def _send_get(
    session: ClientSession,
    task_id: str,
    read_timeout_seconds: float | None = None,
) -> ClientGetTaskResult:
    """Send `tasks/get` and parse the detailed task response."""
    with client_span("tasks/get", "tasks/get", task_id):
        request = GetTaskRequest(
            params=GetTaskRequestParams(task_id=task_id, meta=_trace_meta())
        )
        return await session.send_request(
            request,
            ClientGetTaskResult,
            request_read_timeout_seconds=read_timeout_seconds,
        )


async def _send_update(
    session: ClientSession,
    task_id: str,
    input_responses: dict[str, Any],
    read_timeout_seconds: float | None = None,
) -> None:
    """Send `tasks/update` delivering the caller's answers to a parked task."""
    with client_span("tasks/update", "tasks/update", task_id):
        request = UpdateTaskRequest(
            params=UpdateTaskRequestParams(
                task_id=task_id, input_responses=input_responses, meta=_trace_meta()
            )
        )
        await session.send_request(
            request, mcp_types.Result, request_read_timeout_seconds=read_timeout_seconds
        )


async def _send_cancel(
    session: ClientSession,
    task_id: str,
    read_timeout_seconds: float | None = None,
) -> None:
    """Send `tasks/cancel` to cooperatively cancel a task."""
    with client_span("tasks/cancel", "tasks/cancel", task_id):
        request = CancelTaskRequest(
            params=CancelTaskRequestParams(task_id=task_id, meta=_trace_meta())
        )
        await session.send_request(
            request, mcp_types.Result, request_read_timeout_seconds=read_timeout_seconds
        )


# ---------------------------------------------------------------------------
# Poll cadence
# ---------------------------------------------------------------------------


def _poll_ceiling(poll_interval_ms: float | None) -> float:
    """The upper bound for the poll backoff, in seconds.

    A server-advertised `pollIntervalMs` is a deliberate statement about how much
    load the server wants to take, so it caps the backoff. A zero, negative, or
    absent value falls back to the `poll_interval` client setting; the ceiling is
    never below `MIN_POLL_INTERVAL` so a hostile `0` cannot spin the client.
    """
    if poll_interval_ms is not None and poll_interval_ms > 0:
        return max(poll_interval_ms / 1000, MIN_POLL_INTERVAL)
    return client_settings.poll_interval


def _next_poll_delay(
    poll_interval_ms: float | None, backoff: float
) -> tuple[float, float]:
    """Delay before the next poll, plus the backoff for the round after.

    With no status notifications on the modern protocol, polling is the only
    signal, so a fixed cadence at the server's advertised interval would make a
    quick task take that full interval to observe as done. Instead the backoff
    ramps from `MIN_POLL_INTERVAL`, doubling each round up to the ceiling
    (`_poll_ceiling`): a quick task resolves in ~20ms while a long one settles to
    the server's advertised cadence, hammering neither.
    """
    ceiling = _poll_ceiling(poll_interval_ms)
    return min(backoff, ceiling), min(backoff * 2, ceiling)


# ---------------------------------------------------------------------------
# In-task input: answer a parked task's requests via the elicitation handler
# ---------------------------------------------------------------------------


async def _answer_input_requests(
    session: ClientSession,
    task_id: str,
    input_requests: dict[str, Any],
    elicitation_callback: ElicitationFnT | None,
    read_timeout_seconds: float | None = None,
) -> None:
    """Answer a task's outstanding input requests, then deliver via `tasks/update`.

    Each request is surfaced by a server-minted key and carries a serialized
    `ElicitRequest`. The client's elicitation handler produces each answer; the
    keyed answers are sent back with `tasks/update`, which re-enters the task.
    Sampling and roots requests are not supported on the modern protocol.
    """
    if elicitation_callback is None:
        raise ToolError(
            f"Task {task_id} requires input but the client has no elicitation "
            "handler; pass elicitation_handler= to Client() to drive tasks that "
            "ask for input."
        )

    # Bound the whole answer phase — elicitation callbacks included — by the
    # call's remaining budget: a stalled handler must not outlast `timeout=N`
    # any more than a stalled poll does, matching the synchronous path.
    loop = asyncio.get_event_loop()
    deadline = (
        None if read_timeout_seconds is None else loop.time() + read_timeout_seconds
    )

    def _remaining() -> float | None:
        if deadline is None:
            return None
        left = deadline - loop.time()
        if left <= 0:
            raise TimeoutError(f"Task {task_id} timed out awaiting input")
        return left

    responses: dict[str, Any] = {}
    for surfaced_key, payload in input_requests.items():
        method = payload.get("method") if isinstance(payload, dict) else None
        if method != "elicitation/create":
            raise ToolError(
                f"Task {task_id} requested in-task input via {method!r}, which the "
                "client cannot answer; only elicitation is supported on the modern "
                "protocol (sampling and roots are deprecated)."
            )
        request = mcp_types.ElicitRequest.model_validate(payload)
        context = ClientRequestContext(
            session=session, request_id=f"task-{task_id}-{surfaced_key}"
        )
        budget = _remaining()
        call = elicitation_callback(context, request.params)
        try:
            answer = await (
                asyncio.wait_for(call, budget) if budget is not None else call
            )
        except asyncio.TimeoutError as exc:
            # Normalize to the builtin: on Python 3.10 `asyncio.wait_for` raises
            # `asyncio.TimeoutError`, a distinct type from the builtin the rest of
            # the drive raises (they were unified in 3.11).
            raise TimeoutError(f"Task {task_id} timed out awaiting input") from exc
        if isinstance(answer, mcp_types.ErrorData):
            raise ToolError(f"Elicitation for task {task_id} failed: {answer.message}")
        responses[surfaced_key] = answer.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )

    await _send_update(session, task_id, responses, _remaining())


# ---------------------------------------------------------------------------
# The shared poll loop
# ---------------------------------------------------------------------------


async def _drive_to_terminal(
    session: ClientSession,
    task_id: str,
    elicitation_callback: ElicitationFnT | None,
    timeout_seconds: float | None = None,
) -> ClientGetTaskResult:
    """Poll `tasks/get` until the task reaches a terminal state.

    `working` sleeps and polls again; `input_required` answers the outstanding
    requests through the elicitation handler and re-enters; a terminal state
    (completed / failed / cancelled) is returned. Shared by the transparent
    resolver and `ToolTask.result()`.

    `timeout_seconds`, when set, is one deadline for the *entire* drive — not a
    per-request timeout. The synchronous path aborts a `tools/call` once total
    execution exceeds the call's timeout, so the tasked path must too: each poll
    and sleep is bounded by the time remaining, and a `TimeoutError` is raised
    once the deadline passes. `None` drives to completion unbounded (the default
    for `ToolTask.result()`, whose caller bounds waiting via `wait(timeout=...)`).
    """
    loop = asyncio.get_event_loop()
    deadline = None if timeout_seconds is None else loop.time() + timeout_seconds
    backoff = MIN_POLL_INTERVAL

    def remaining() -> float | None:
        return None if deadline is None else deadline - loop.time()

    while True:
        budget = remaining()
        if budget is not None and budget <= 0:
            raise TimeoutError(
                f"Task {task_id} did not finish within {timeout_seconds}s"
            )

        current = await _send_get(session, task_id, budget)
        if current.status in _TERMINAL_STATES:
            return current
        if current.status == "input_required":
            await _answer_input_requests(
                session,
                task_id,
                current.input_requests or {},
                elicitation_callback,
                remaining(),
            )
            backoff = MIN_POLL_INTERVAL
            continue
        # working
        delay, backoff = _next_poll_delay(current.poll_interval_ms, backoff)
        budget = remaining()
        if budget is not None:
            delay = min(delay, budget)
        await asyncio.sleep(delay)


def _inlined_call_tool_result(result: dict[str, Any] | None) -> CallToolResult:
    """Parse a completed task's inlined result dict into a `CallToolResult`."""
    return CallToolResult.model_validate(result or {})


def _terminal_error_message(final: ClientGetTaskResult) -> str:
    """The best available error message for a failed task."""
    if isinstance(final.error, dict):
        message = final.error.get("message")
        if isinstance(message, str) and message:
            return message
    if final.status_message:
        return final.status_message
    return f"Task {final.task_id} failed"


# ---------------------------------------------------------------------------
# The tasks client extension and its claim resolver
# ---------------------------------------------------------------------------


class TasksClientExtension(ClientExtension):
    """The client half of the `io.modelcontextprotocol/tasks` extension (SEP-2663).

    Advertising this extension tells the server the client can drive tasks, so a
    `task=True` tool may run as a task; the declared `ResultClaim` then resolves
    the `CreateTaskResult` the server returns by polling `tasks/get` to the real
    result. Registered automatically on every FastMCP `Client`.
    """

    identifier = TASKS_EXTENSION_ID

    def __init__(self, elicitation_callback: ElicitationFnT | None = None) -> None:
        self._elicitation_callback = elicitation_callback

    def settings(self) -> dict[str, Any]:
        """The tasks extension advertises no per-extension settings."""
        return {}

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return (
            ResultClaim(
                result_type="task",
                model=ClientCreateTaskResult,
                resolve=self._resolve_task,
                protocol_versions=frozenset(MODERN_PROTOCOL_VERSIONS),
            ),
        )

    async def _resolve_task(
        self, create_result: ClientCreateTaskResult, ctx: ClaimContext
    ) -> CallToolResult:
        """Finish a tasked `tools/call` by polling `tasks/get` to completion.

        Returns the tool's real result on completion; a failed or cancelled task
        becomes an error `CallToolResult` so the ordinary `call_tool` error path
        (raise `ToolError`) applies uniformly, and the completed inlined result is
        schema-valid so the SDK's output-schema revalidation passes.
        """
        final = await _drive_to_terminal(
            ctx.session,
            create_result.task_id,
            self._elicitation_callback,
            ctx.read_timeout_seconds,
        )
        if final.status == "completed":
            return _inlined_call_tool_result(final.result)
        if final.status == "failed":
            message = _terminal_error_message(final)
        else:
            message = f"Task {final.task_id} was cancelled"
        return CallToolResult(
            content=[mcp_types.TextContent(type="text", text=message)],
            is_error=True,
        )


def _build_tasks_client_extension(
    elicitation_callback: ElicitationFnT | None,
) -> ClientExtension:
    """Factory registered with core so every `Client` folds in task support."""
    return TasksClientExtension(elicitation_callback)


# ---------------------------------------------------------------------------
# The explicit task handle (return-quickly surface)
# ---------------------------------------------------------------------------


class ToolTask:
    """A handle to a tool call the server is running as a background task.

    Returned by `call_tool_task`. Lets a caller return immediately and then drive
    the task: check `status`, `wait` for a state, get the finished `result`
    (answering any input prompts through the client's elicitation handler), or
    `cancel`. Awaiting the handle is shorthand for `result()`.
    """

    def __init__(
        self,
        client: Client,
        tool_name: str,
        create_result: ClientCreateTaskResult,
        *,
        raise_on_error: bool = True,
    ) -> None:
        self._client = client
        self._tool_name = tool_name
        self._create_result = create_result
        self._raise_on_error = raise_on_error
        self._cached_result: FastMCPCallToolResult | None = None

    @property
    def task_id(self) -> str:
        """The server-generated task id."""
        return self._create_result.task_id

    @property
    def create_result(self) -> ClientCreateTaskResult:
        """The raw `CreateTaskResult` the server returned for the tasked call."""
        return self._create_result

    @property
    def _session(self) -> ClientSession:
        return self._client.session

    @property
    def _elicitation_callback(self) -> ElicitationFnT | None:
        return self._client._elicitation_callback

    async def status(self) -> ClientGetTaskResult:
        """Fetch the task's current status via `tasks/get`."""
        return await _send_get(self._session, self.task_id)

    async def wait(
        self, *, state: str | None = None, timeout: float = 300.0
    ) -> ClientGetTaskResult:
        """Poll until the task reaches `state` (or any terminal state if `None`).

        Does not answer input prompts: a caller that wants automatic answering
        should use `result()`. `wait(state="input_required")` lets a caller
        observe the parked state and answer it manually.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        backoff = MIN_POLL_INTERVAL
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {self.task_id} did not reach "
                    f"{state or 'a terminal state'} within {timeout}s"
                )
            # Bound the request itself by the remaining deadline: a stalled
            # `tasks/get` must not block past the caller's timeout waiting for
            # the session-wide default before the deadline is next checked.
            current = await _send_get(
                self._session, self.task_id, read_timeout_seconds=remaining
            )
            if state is not None:
                if current.status == state:
                    return current
            elif current.status in _TERMINAL_STATES:
                return current
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {self.task_id} did not reach "
                    f"{state or 'a terminal state'} within {timeout}s"
                )
            delay, backoff = _next_poll_delay(current.poll_interval_ms, backoff)
            # Never sleep past the deadline, so `wait` returns on time rather
            # than up to one poll interval late.
            await asyncio.sleep(min(delay, remaining))

    async def result(self) -> FastMCPCallToolResult:
        """Drive the task to completion and return its parsed result.

        Answers any input prompts through the client's elicitation handler.
        Raises `ToolError` on a failed or cancelled task when `raise_on_error`
        (the default); otherwise returns an error result. The result is cached, so
        repeated calls return the same object.
        """
        if self._cached_result is not None:
            return self._cached_result

        final = await _drive_to_terminal(
            self._session, self.task_id, self._elicitation_callback
        )
        if final.status == "completed":
            mcp_result = _inlined_call_tool_result(final.result)
        else:
            if final.status == "failed":
                message = _terminal_error_message(final)
            else:
                message = f"Task {self.task_id} was cancelled"
            if self._raise_on_error:
                raise ToolError(message)
            mcp_result = CallToolResult(
                content=[mcp_types.TextContent(type="text", text=message)],
                is_error=True,
            )

        parsed = await self._client._parse_call_tool_result(
            self._tool_name, mcp_result, raise_on_error=self._raise_on_error
        )
        self._cached_result = parsed
        return parsed

    async def cancel(self) -> None:
        """Request cooperative cancellation of the task via `tasks/cancel`."""
        await _send_cancel(self._session, self.task_id)

    def __await__(self):
        return self.result().__await__()


async def call_tool_task(
    client: Client,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float | int | None = None,
    raise_on_error: bool = True,
    version: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ToolTask:
    """Call a tool as a background task and return a `ToolTask` handle immediately.

    Unlike `client.call_tool` (which polls to completion transparently), this
    returns as soon as the server accepts the task, so the caller can do other
    work and drive the task through the handle. Requires the server to run the
    call as a task (a `task=True` tool on a task-serving backend); a call the
    server runs synchronously raises `ToolError`.

    `version` targets a specific component version, the same as
    `client.call_tool(..., version=...)`: the server tasks that version rather
    than the highest. It is carried in the request metadata FastMCP reads.
    """
    read_timeout_seconds = normalize_timeout_to_seconds(timeout)
    combined_meta: dict[str, Any] = dict(meta) if meta else {}
    if version is not None:
        fastmcp_meta = dict(combined_meta.get("fastmcp") or {})
        fastmcp_meta["version"] = version
        combined_meta["fastmcp"] = fastmcp_meta
    with client_span("tools/call", "tools/call", name, tool_name=name):
        # Propagate the trace into the tasked submission, like a foreground call.
        propagated = inject_trace_context(combined_meta)
        request_meta = cast("mcp_types.RequestParamsMeta | None", propagated or None)
        raw = await client._await_with_session_monitoring(
            client.session.call_tool(
                name=name,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout_seconds,
                meta=request_meta,
                allow_claimed=True,
            )
        )
    if isinstance(raw, ClientCreateTaskResult):
        return ToolTask(client, name, raw, raise_on_error=raise_on_error)
    raise ToolError(
        f"Tool {name!r} did not run as a task: the server returned a "
        f"{type(raw).__name__} instead of a task. Ensure the tool is declared "
        "task=True and the connection is modern (mode='auto')."
    )
