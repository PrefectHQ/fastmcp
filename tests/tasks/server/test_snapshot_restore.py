"""Tests for ``restore_task_snapshot`` — the worker-level Docket dependency
that restores the task-context snapshot into the ``_task_snapshot``
ContextVar before each task runs.

With the snapshot restored up front, sync helpers (``get_access_token``,
``get_http_request``, etc.) never need to hit Redis themselves.  These
tests exercise the restore path end-to-end (via in-memory Docket) and
the edge cases around non-fastmcp keys and failed restores.
"""

from __future__ import annotations

import contextvars
from unittest.mock import patch

import pytest
from fastmcp_tasks.context import (
    TaskContextSnapshot,
    _apply_snapshot_to_context,
    _recall_snapshot,
    get_task_context,
    restore_task_snapshot,
)
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token, get_http_headers
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    running_task_server,
    submit_task,
    wait_for_task,
)


async def test_snapshot_restored_before_user_code_runs():
    """A tool with no declared deps finds the snapshot already cached."""
    mcp = FastMCP("snapshot-restore-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> bool:
        info = get_task_context()
        assert info is not None
        return _recall_snapshot(info.task_id) is not None

    async with running_task_server(mcp):
        created = await submit_task(mcp, "bare_tool", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": True}


async def test_get_access_token_in_bg_task_without_context_dep():
    """Issue #3897 repro: get_access_token() works in a bg task that does
    not declare Context as a dependency."""
    mcp = FastMCP("access-token-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> str:
        token = get_access_token()
        return token.token if token else "no-token"

    test_token = AccessToken(
        token="jwt-3897",
        client_id="test-client",
        scopes=["read"],
        claims={"sub": "user-x"},
    )
    auth_context_var.set(AuthenticatedUser(test_token))

    async with running_task_server(mcp):
        created = await submit_task(mcp, "bare_tool", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "jwt-3897"}


def test_apply_snapshot_restores_auth_and_headers_in_clean_context():
    """The cross-process path: with nothing inherited, the snapshot alone makes
    get_access_token()/get_http_headers() see the submitting caller.

    A Redis-backed worker runs in a separate process and inherits none of the
    submitter's context vars, so contextvar inheritance (which carries the token
    on the same-process memory:// path) cannot help. Running in a fresh
    `copy_context()` with no auth/request bound simulates that worker: only
    `_apply_snapshot_to_context` populating the ambient vars makes the token and
    headers reachable.
    """
    token = AccessToken(
        token="jwt-remote",
        client_id="remote-client",
        scopes=["read"],
        claims={"sub": "user-y"},
    )
    snapshot = TaskContextSnapshot(
        access_token_json=token.model_dump_json(),
        http_headers={"x-trace-id": "abc123"},
    )

    def run_in_clean_worker_context() -> None:
        # Nothing bound here — no inheritance to fall back on.
        assert get_access_token() is None
        assert get_http_headers() == {}
        _apply_snapshot_to_context(snapshot)
        restored = get_access_token()
        assert restored is not None
        assert restored.token == "jwt-remote"
        assert restored.client_id == "remote-client"
        assert get_http_headers()["x-trace-id"] == "abc123"

    contextvars.copy_context().run(run_in_clean_worker_context)


def test_apply_snapshot_headers_without_faking_a_request():
    """Snapshot headers are readable, but no live request is fabricated.

    `get_http_headers()` returns the submitting request's headers, while
    `get_http_request()` still raises — there is no live request inside a
    background task, and impersonating one would make `CurrentRequest()` expose
    invented method/URL/client data.
    """
    from fastmcp.server.dependencies import get_http_request

    snapshot = TaskContextSnapshot(http_headers={"x-trace-id": "abc123"})

    def run_in_clean_worker_context() -> None:
        _apply_snapshot_to_context(snapshot)
        assert get_http_headers()["x-trace-id"] == "abc123"
        with pytest.raises(RuntimeError):
            get_http_request()

    contextvars.copy_context().run(run_in_clean_worker_context)


def test_apply_snapshot_skips_expired_token():
    """An expired snapshot token is not installed, so the worker is unauthenticated.

    A task may sit queued past its submitter's token expiry. A live request with
    an expired bearer token is rejected (401), so restoring one as authenticated
    would let a delayed task run under credentials that should now be treated as
    unauthenticated. The headers still restore — only the auth token is dropped.
    """
    expired = AccessToken(
        token="jwt-expired",
        client_id="remote-client",
        scopes=["read"],
        expires_at=1,  # 1970 — long past
    )
    snapshot = TaskContextSnapshot(
        access_token_json=expired.model_dump_json(),
        http_headers={"x-trace-id": "abc123"},
    )

    def run_in_clean_worker_context() -> None:
        assert get_access_token() is None
        _apply_snapshot_to_context(snapshot)
        assert get_access_token() is None
        # Non-auth context still restores independently of the token.
        assert get_http_headers()["x-trace-id"] == "abc123"

    contextvars.copy_context().run(run_in_clean_worker_context)


def test_apply_snapshot_clears_prior_auth_in_reused_context():
    """An anonymous task must not inherit a prior task's identity or headers.

    A Docket worker may reuse an asyncio context across executions. Applying a
    tokenless snapshot after an authenticated one must clear the earlier
    caller's `auth_context_var` and headers rather than leave them installed.
    """
    prior = AccessToken(token="jwt-prior", client_id="prior-client", scopes=["read"])
    authed = TaskContextSnapshot(
        access_token_json=prior.model_dump_json(),
        http_headers={"x-trace-id": "prior"},
    )
    anonymous = TaskContextSnapshot()

    def run_in_reused_worker_context() -> None:
        _apply_snapshot_to_context(authed)
        assert get_access_token() is not None
        assert get_http_headers()["x-trace-id"] == "prior"

        # Same context, next task carries no auth/headers.
        _apply_snapshot_to_context(anonymous)
        assert get_access_token() is None
        assert get_http_headers() == {}

    contextvars.copy_context().run(run_in_reused_worker_context)


async def test_restore_failure_is_nonfatal():
    """If deserialization blows up, the task still runs to completion and
    the snapshot cache stays empty."""
    mcp = FastMCP("restore-failure-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> bool:
        info = get_task_context()
        assert info is not None
        return _recall_snapshot(info.task_id) is not None

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated deserialization failure")

    async with running_task_server(mcp):
        with patch.object(TaskContextSnapshot, "from_json", boom):
            created = await submit_task(mcp, "bare_tool", {})
            final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": False}


async def test_restore_skipped_for_non_fastmcp_task_keys():
    """The restore dep returns cleanly for keys it doesn't recognize and
    writes nothing to the snapshot cache."""
    # Direct calls bypass the worker, so Redis/Docket never gets involved
    # — any attempt to touch them would raise.
    await restore_task_snapshot(key="not-a-fastmcp-key")
    await restore_task_snapshot(key="weird:client-a:task-1:tool:my_tool")
    await restore_task_snapshot(key="")
