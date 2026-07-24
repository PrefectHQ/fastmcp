"""Shared helpers for driving SEP-2663 tasks in server-side tests.

There is no client task-submission API until Phase 4, so server-side tests drive
the task lifecycle in-process: the create decision runs through the real
`tools/call` interceptor (with a per-request tasks opt-in bound into the request
context), and `tasks/get` / `tasks/update` / `tasks/cancel` call the extension's
handler functions directly. Optional auth binding exercises the auth-scoped task
isolation.

Typical use::

    async with running_task_server(mcp):
        created = await submit_task(mcp, "square", {"n": 6})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"

or the one-shot::

    async with running_task_server(mcp):
        final = await run_task(mcp, "square", {"n": 6})
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast

from fastmcp_tasks.handlers import tasks_cancel, tasks_get, tasks_update
from fastmcp_tasks.models import (
    CancelTaskResult,
    CreateTaskResult,
    GetTaskResult,
    UpdateTaskResult,
)
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp_types import CLIENT_CAPABILITIES_META_KEY

from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import bind_request_context
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


def opt_in_meta(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The per-request `_meta` block that opts the tasks extension in."""
    return {
        CLIENT_CAPABILITIES_META_KEY: {
            "extensions": {TASKS_EXTENSION_ID: settings or {}}
        }
    }


def make_access_token(client_id: str, sub: str | None = None) -> AccessToken:
    """A minimal FastMCP access token for auth-scoped task tests."""
    claims: dict[str, Any] = {"sub": sub} if sub is not None else {}
    return AccessToken(
        token=f"token-{client_id}-{sub}",
        client_id=client_id,
        scopes=[],
        claims=claims,
    )


@contextlib.contextmanager
def auth_scope(access_token: AccessToken | None):
    """Bind (or clear) the auth context so `get_task_scope` sees a caller."""
    if access_token is None:
        yield
        return
    token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        yield
    finally:
        auth_context_var.reset(token)


@contextlib.contextmanager
def _opted_in_request(
    name: str, arguments: dict[str, Any] | None, settings: dict[str, Any] | None
):
    """Bind a request context carrying the tasks opt-in for a `tools/call`."""
    params: dict[str, Any] = {
        "name": name,
        "arguments": arguments or {},
        "_meta": opt_in_meta(settings),
    }
    srctx = ServerRequestContext(
        session=cast(ServerSession, SimpleNamespace()),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        params=params,
    )
    with bind_request_context(srctx):
        yield


def running_task_server(server: FastMCP):
    """Enter the server lifespan (Docket backend + worker) for the block."""
    return server._lifespan_manager()


async def submit_task(
    server: FastMCP,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    access_token: AccessToken | None = None,
    settings: dict[str, Any] | None = None,
) -> CreateTaskResult:
    """Run an opted-in `tools/call` through the interceptor and return its task."""
    with auth_scope(access_token), _opted_in_request(name, arguments, settings):
        result = await server.call_tool(name, arguments or {})
    if not isinstance(result, CreateTaskResult):
        raise AssertionError(
            f"Expected the call to be tasked, got {type(result).__name__}: {result!r}"
        )
    return result


async def call_tool_without_optin(
    server: FastMCP,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    access_token: AccessToken | None = None,
):
    """Run a `tools/call` with no tasks opt-in (synchronous unless mode=required)."""
    with auth_scope(access_token):
        return await server.call_tool(name, arguments or {})


async def get_task(
    server: FastMCP,
    task_id: str,
    *,
    access_token: AccessToken | None = None,
) -> GetTaskResult:
    """Call the `tasks/get` handler within the given auth scope."""
    with auth_scope(access_token):
        return await tasks_get(server, task_id)


async def update_task(
    server: FastMCP,
    task_id: str,
    input_responses: dict[str, Any],
    *,
    access_token: AccessToken | None = None,
) -> UpdateTaskResult:
    """Call the `tasks/update` handler within the given auth scope."""
    with auth_scope(access_token):
        return await tasks_update(server, task_id, input_responses)


async def cancel_task(
    server: FastMCP,
    task_id: str,
    *,
    access_token: AccessToken | None = None,
) -> CancelTaskResult:
    """Call the `tasks/cancel` handler within the given auth scope."""
    with auth_scope(access_token):
        return await tasks_cancel(server, task_id)


async def wait_for_task(
    server: FastMCP,
    task_id: str,
    *,
    access_token: AccessToken | None = None,
    target_states: frozenset[str] = TERMINAL_STATES,
    timeout: float = 5.0,
    poll: float = 0.02,
) -> GetTaskResult:
    """Poll `tasks/get` until the task reaches one of `target_states`."""
    deadline = asyncio.get_event_loop().time() + timeout
    result = await get_task(server, task_id, access_token=access_token)
    while result.status not in target_states:
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(
                f"Task {task_id} still {result.status!r} after {timeout}s "
                f"(waiting for {sorted(target_states)})"
            )
        await asyncio.sleep(poll)
        result = await get_task(server, task_id, access_token=access_token)
    return result


async def run_task(
    server: FastMCP,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    access_token: AccessToken | None = None,
    timeout: float = 5.0,
) -> GetTaskResult:
    """Submit a task and wait for it to reach a terminal state."""
    created = await submit_task(server, name, arguments, access_token=access_token)
    return await wait_for_task(
        server, created.task_id, access_token=access_token, timeout=timeout
    )
