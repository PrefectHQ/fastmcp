"""Tests for Context background task support (SEP-2663 tasks).

Covers the Context API surface in a background task (unit tests, no Redis
needed) and end-to-end background-task behavior driven in-process through the
shared task helpers: progress reporting, context wiring, access-token
availability, and poll-based in-task elicitation.

A SEP-2663 worker has no live session and no back-channel: ``ctx.session`` is
unavailable, and elicitation is polled (the worker parks an input request that
the client answers via ``tasks/update``).
"""

from __future__ import annotations

import gc
from contextlib import AsyncExitStack
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastmcp_tasks.context import (
    _task_sessions,
    get_task_session,
    register_task_session,
)
from mcp import ServerSession
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp_types import (
    ClientCapabilities,
    Implementation,
    InitializeRequestParams,
)

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    running_task_server,
    submit_task,
    wait_for_task,
)

# =============================================================================
# Unit tests: Context API surface (no Redis/Docket needed)
# =============================================================================


class TestContextBackgroundTaskSupport:
    """Tests for Context.is_background_task and related functionality."""

    def test_context_not_background_task_by_default(self):
        """Context should not be a background task by default."""
        mcp = FastMCP("test")
        ctx = Context(mcp)
        assert ctx.is_background_task is False
        assert ctx.task_id is None

    def test_context_is_background_task_when_task_id_provided(self):
        """Context should be a background task when task_id is provided."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-123")
        assert ctx.is_background_task is True
        assert ctx.task_id == "test-task-123"

    def test_context_task_id_is_readonly(self):
        """task_id should be a read-only property."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-123")
        with pytest.raises(AttributeError):
            setattr(ctx, "task_id", "new-id")


async def test_live_task_session_is_released_on_connection_disconnect():
    """A registered in-process task session is dropped when its connection
    exit stack unwinds."""
    _task_sessions.clear()

    class MockConnection:
        def __init__(self) -> None:
            self.state: dict[str, object] = {}
            self.exit_stack = AsyncExitStack()

    class MockSession:
        def __init__(self, connection: MockConnection) -> None:
            self._connection = connection

    connection = MockConnection()
    session = MockSession(connection)
    async with connection.exit_stack:
        register_task_session("session", cast(ServerSession, session))
        session_ref = _task_sessions["session"]

    assert session_ref() is session
    assert _task_sessions == {}


async def test_connection_cleanup_does_not_remove_replacement_session():
    """Registering a replacement session under the same id keeps the newer one."""
    _task_sessions.clear()

    class MockConnection:
        def __init__(self) -> None:
            self.state: dict[str, object] = {}
            self.exit_stack = AsyncExitStack()

    class MockSession:
        def __init__(self, connection: MockConnection | None = None) -> None:
            self._connection = connection

    connection = MockConnection()
    old_session = MockSession(connection)
    new_session = MockSession()
    async with connection.exit_stack:
        register_task_session("shared", cast(ServerSession, old_session))
        register_task_session("shared", cast(ServerSession, new_session))

    assert get_task_session("shared") is new_session
    _task_sessions.clear()


def test_replaced_task_session_is_not_removed_by_old_weakref():
    """A stale weakref for a replaced session does not evict the new session."""
    _task_sessions.clear()

    class MockSession:
        pass

    old_session = MockSession()
    new_session = MockSession()
    register_task_session("shared", cast(ServerSession, old_session))
    old_ref = _task_sessions["shared"]
    register_task_session("shared", cast(ServerSession, new_session))

    del old_session
    gc.collect()

    assert old_ref() is None
    assert get_task_session("shared") is new_session


class TestContextSessionProperty:
    """Tests for Context.session property in different modes."""

    def test_session_raises_when_no_session_available(self):
        """session should raise RuntimeError when no session is available."""
        mcp = FastMCP("test")
        ctx = Context(mcp)  # No session, not a background task

        with pytest.raises(RuntimeError, match="session is not available"):
            _ = ctx.session

    def test_session_uses_stored_session_in_background_task(self):
        """session should use the stored session in background task mode."""
        mcp = FastMCP("test")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        mock_session = MockSession()
        ctx = Context(
            mcp, session=cast(ServerSession, mock_session), task_id="test-task-123"
        )

        assert ctx.session is mock_session

    def test_session_uses_stored_session_during_on_initialize(self):
        """session should use the stored session during on_initialize."""
        mcp = FastMCP("test")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        mock_session = MockSession()
        ctx = Context(mcp, session=cast(ServerSession, mock_session))

        assert ctx.session is mock_session


class TestContextBackgroundTaskLogging:
    """Tests for per-session log gating in background task mode."""

    def _make_task_context(
        self, mcp: FastMCP, session_id: str
    ) -> tuple[Context, AsyncMock]:
        send_log_message = AsyncMock()

        class MockConnection:
            def __init__(self, session_id: str) -> None:
                self.session_id = session_id

        class MockSession:
            def __init__(self, session_id: str) -> None:
                self._connection = MockConnection(session_id)
                self._fastmcp_state_prefix = session_id
                self.send_log_message = send_log_message

        session = MockSession(session_id)
        ctx = Context(
            mcp, session=cast(ServerSession, session), task_id="test-task-123"
        )
        return ctx, send_log_message

    async def test_background_task_honors_session_level(self):
        """A background task has a stored session but no request context; the
        per-session minimum registered via logging/setLevel must still gate
        its logs, so sub-threshold messages are not sent to the client."""
        mcp = FastMCP("test")
        session_id = "session-abc"
        mcp._client_log_levels[session_id] = "error"

        ctx, send_log_message = self._make_task_context(mcp, session_id)
        assert ctx.is_background_task is True
        assert ctx.request_context is None

        await ctx.info("info msg")
        send_log_message.assert_not_called()

        await ctx.error("error msg")
        send_log_message.assert_called_once()

    async def test_background_task_without_session_level_sends_all(self):
        """When no per-session level is registered, background-task logs fall
        back to the server default (which allows everything by default)."""
        mcp = FastMCP("test")
        ctx, send_log_message = self._make_task_context(mcp, "session-xyz")

        await ctx.info("info msg")
        send_log_message.assert_called_once()


class TestContextClientExtensionBackgroundTask:
    """Tests for Context.client_supports_extension() in background task mode.

    A background task may carry a stored snapshot session but no request
    context. The client's advertised capabilities are preserved on the
    session's ``client_params``, so extension detection reads from the session
    rather than gating on ``request_context``.
    """

    def _make_task_context(
        self, mcp: FastMCP, extensions: dict[str, dict[str, Any]] | None
    ) -> Context:
        capabilities = ClientCapabilities(extensions=extensions)
        client_params = InitializeRequestParams(
            protocol_version="2025-06-18",
            capabilities=capabilities,
            client_info=Implementation(name="test-client", version="1.0"),
        )

        class MockSession:
            _fastmcp_state_prefix = "session-ext"

            def __init__(self) -> None:
                self.client_params = client_params

        session = MockSession()
        return Context(
            mcp, session=cast(ServerSession, session), task_id="test-task-ext"
        )

    def test_background_task_detects_advertised_extension(self):
        """The stored session preserves the client's initialize params, so an
        advertised extension is detected even with no request context."""
        mcp = FastMCP("test")
        ctx = self._make_task_context(mcp, {"ext-abc": {}})

        assert ctx.is_background_task is True
        assert ctx.request_context is None
        assert ctx.client_supports_extension("ext-abc") is True
        assert ctx.client_supports_extension("ext-missing") is False

    def test_background_task_no_extensions_returns_false(self):
        """When the client advertised no extensions, detection returns False."""
        mcp = FastMCP("test")
        ctx = self._make_task_context(mcp, None)

        assert ctx.client_supports_extension("ext-abc") is False

    def test_no_session_returns_false(self):
        """With no session available at all (e.g. distributed worker), the
        method degrades to False rather than raising."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-ext")

        assert ctx.client_supports_extension("ext-abc") is False


class TestContextElicitBackgroundTask:
    """Tests for Context.elicit() in background task mode.

    Imperative elicitation is not supported inside a background task: the worker
    never blocks on a client round-trip. A task gathers input with the guard
    pattern (return an ``InputRequiredResult``), so ``ctx.elicit()`` in a task
    fails fast with guidance rather than parking a worker.
    """

    async def test_elicit_raises_with_guard_guidance(self):
        """elicit() inside a background task raises a ToolError pointing to the
        guard/return pattern (InputRequiredResult)."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-123")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        ctx._session = cast(ServerSession, MockSession())

        with pytest.raises(ToolError, match="InputRequiredResult"):
            await ctx.elicit("Need input", str)


class TestContextDocumentation:
    """Tests to verify Context documentation and API surface."""

    def test_is_background_task_has_docstring(self):
        """is_background_task property should have documentation."""
        assert Context.is_background_task.__doc__ is not None
        assert "background task" in Context.is_background_task.__doc__.lower()

    def test_task_id_has_docstring(self):
        """task_id property should have documentation."""
        assert Context.task_id.fget.__doc__ is not None
        assert "task ID" in Context.task_id.fget.__doc__

    def test_session_has_docstring(self):
        """session property should document background task support."""
        assert Context.session.fget.__doc__ is not None
        assert "background task" in Context.session.fget.__doc__.lower()


# =============================================================================
# Integration tests: in-process SEP-2663 tasks via the shared helpers
# =============================================================================


class TestBackgroundTaskIntegration:
    """End-to-end background task context, driven in-process via the helpers."""

    async def test_report_progress_in_background_task(self):
        """report_progress() should complete without error in a background task."""
        mcp = FastMCP("progress-test")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def progress_tool(ctx: Context) -> str:
            await ctx.report_progress(0, 100, "Starting...")
            await ctx.report_progress(50, 100, "Half done")
            await ctx.report_progress(100, 100, "Complete")
            return "done"

        async with running_task_server(mcp):
            created = await submit_task(mcp, "progress_tool", {})
            final = await wait_for_task(mcp, created.task_id)

        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": "done"}

    async def test_context_wiring_in_background_task(self):
        """A worker Context is wired as a background task with no live session."""
        mcp = FastMCP("wiring-test")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def verify_wiring(ctx: Context) -> dict[str, bool]:
            session_unavailable = False
            try:
                _ = ctx.session
            except RuntimeError:
                session_unavailable = True
            return {
                "task_id_set": ctx.task_id is not None,
                "is_background": ctx.is_background_task,
                "no_request_context": ctx.request_context is None,
                "session_unavailable": session_unavailable,
            }

        async with running_task_server(mcp):
            created = await submit_task(mcp, "verify_wiring", {})
            final = await wait_for_task(mcp, created.task_id)

        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {
            "task_id_set": True,
            "is_background": True,
            "no_request_context": True,
            "session_unavailable": True,
        }

    async def test_imperative_elicit_fails_with_guard_guidance(self):
        """A task=True tool that calls ctx.elicit() errors with guard guidance.

        The ToolError it raises surfaces as a completed is_error result (like any
        raised tool error, SEP-2663), never parking a worker on a round-trip.
        """
        mcp = FastMCP("elicit-forbidden")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def ask_name(ctx: Context) -> str:
            result = await ctx.elicit("What is your name?", str)
            return str(result)

        async with running_task_server(mcp):
            created = await submit_task(mcp, "ask_name", {})
            final = await wait_for_task(mcp, created.task_id)

        assert final.status == "completed"
        assert final.result is not None
        assert final.result["isError"] is True
        assert "InputRequiredResult" in final.result["content"][0]["text"]


class TestAccessTokenInBackgroundTasks:
    """Tests for access token availability in background tasks (#3095).

    The token set at submit time is available inside the worker (via the
    captured context snapshot). Async tests run in isolated asyncio tasks, so
    ContextVar changes are automatically scoped — no cleanup required.
    """

    async def test_token_round_trips_through_background_task(self):
        """E2E: token set at submit time is available inside the worker."""
        mcp = FastMCP("token-roundtrip")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def check_token(ctx: Context) -> str:
            token = get_access_token()
            if token is None:
                return "no-token"
            return f"{token.token}|{token.client_id}"

        test_token = AccessToken(
            token="roundtrip-jwt",
            client_id="test-client",
            scopes=["read"],
            claims={"sub": "user-1"},
        )
        auth_context_var.set(AuthenticatedUser(test_token))

        async with running_task_server(mcp):
            created = await submit_task(mcp, "check_token", {})
            final = await wait_for_task(mcp, created.task_id)

        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {
            "result": "roundtrip-jwt|test-client"
        }

    async def test_no_token_when_unauthenticated(self):
        """E2E: background task gets no token when nothing was set."""
        mcp = FastMCP("no-auth")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def check_token(ctx: Context) -> str:
            token = get_access_token()
            return "no-token" if token is None else token.token

        async with running_task_server(mcp):
            created = await submit_task(mcp, "check_token", {})
            final = await wait_for_task(mcp, created.task_id)

        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {"result": "no-token"}


class TestLifespanContextInBackgroundTasks:
    """Tests for lifespan_context availability in background tasks (#3095)."""

    def test_lifespan_context_falls_back_to_server_result(self):
        """lifespan_context reads from server when request_context is None."""
        mcp = FastMCP("test")
        mcp._lifespan_result = {"db": "mock-db-connection", "cache": "mock-cache"}

        ctx = Context(mcp, task_id="test-task")
        assert ctx.request_context is None
        assert ctx.lifespan_context == {
            "db": "mock-db-connection",
            "cache": "mock-cache",
        }

    def test_lifespan_context_returns_empty_dict_when_no_lifespan(self):
        """lifespan_context returns {} when no lifespan is configured."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task")
        assert ctx.request_context is None
        assert ctx.lifespan_context == {}
