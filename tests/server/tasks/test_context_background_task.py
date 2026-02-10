"""Tests for Context background task support (SEP-1686).

Tests Context API surface (unit) and background task elicitation (integration).
Integration tests use Client(mcp) with the real memory:// Docket backend —
no mocking of Redis, Docket, or session internals.
"""

import asyncio
from typing import cast

import pytest
from mcp import ServerSession

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.context import Context
from fastmcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from fastmcp.server.tasks.elicitation import handle_task_input

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


class TestContextSessionProperty:
    """Tests for Context.session property in different modes."""

    def test_session_raises_when_no_session_available(self):
        """session should raise RuntimeError when no session is available."""
        mcp = FastMCP("test")
        ctx = Context(mcp)  # No session, not a background task

        with pytest.raises(RuntimeError, match="session is not available"):
            _ = ctx.session

    def test_session_uses_stored_session_in_background_task(self):
        """session should use _session in background task mode."""
        mcp = FastMCP("test")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        mock_session = MockSession()
        ctx = Context(
            mcp, session=cast(ServerSession, mock_session), task_id="test-task-123"
        )

        assert ctx.session is mock_session

    def test_session_uses_stored_session_during_on_initialize(self):
        """session should use _session during on_initialize (no request context)."""
        mcp = FastMCP("test")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        mock_session = MockSession()
        ctx = Context(mcp, session=cast(ServerSession, mock_session))

        assert ctx.session is mock_session


class TestContextElicitBackgroundTask:
    """Tests for Context.elicit() in background task mode."""

    async def test_elicit_raises_when_background_task_but_no_docket(self):
        """elicit() should raise when in background task mode but Docket unavailable."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-123")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        ctx._session = cast(ServerSession, MockSession())

        with pytest.raises(RuntimeError, match="Docket"):
            await ctx.elicit("Need input", str)


class TestElicitFailFast:
    """Tests for elicit_for_task fail-fast on notification push failure."""

    async def test_elicit_returns_cancel_when_notification_push_fails(self):
        """elicit_for_task should return cancel immediately when push_notification fails.

        If the client can't receive the input_required notification, waiting
        for a response that will never come would block for up to 1 hour.
        Instead, we return cancel immediately (fail-fast).

        This test patches ONLY push_notification — all other components
        (Docket, Redis, session) are real via the memory:// backend.
        """
        from unittest.mock import patch

        from fastmcp.server.elicitation import CancelledElicitation

        mcp = FastMCP("failfast-test")
        elicit_started = asyncio.Event()
        captured: dict[str, object] = {}

        @mcp.tool(task=True)
        async def failfast_tool(ctx: Context) -> str:
            elicit_started.set()
            result = await ctx.elicit("This notification will fail", str)
            captured["result_type"] = type(result).__name__
            captured["is_cancelled"] = isinstance(result, CancelledElicitation)
            return "done"

        # Patch push_notification BEFORE starting client so it's active
        # when the tool runs in the Docket worker
        with patch(
            "fastmcp.server.tasks.notifications.push_notification",
            side_effect=ConnectionError("Redis queue unavailable"),
        ):
            async with Client(mcp) as client:
                task = await client.call_tool("failfast_tool", {}, task=True)
                await asyncio.wait_for(elicit_started.wait(), timeout=5.0)
                await task.wait(timeout=10.0)
                result = await task.result()
                assert result.data == "done"

        # The tool should have received CancelledElicitation (fail-fast)
        assert captured["is_cancelled"] is True
        assert captured["result_type"] == "CancelledElicitation"


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
# Integration tests: Client(mcp) + memory:// Docket backend
# =============================================================================


class TestBackgroundTaskIntegration:
    """Integration tests for background task context using real Docket memory backend.

    These tests use Client(mcp) with the memory:// broker — no mocking.
    The memory:// backend provides a fully functional in-memory Redis store
    that Docket uses automatically when running tests.
    """

    async def test_report_progress_in_background_task(self):
        """report_progress() should complete without error in a background task."""
        mcp = FastMCP("progress-test")
        progress_reported = asyncio.Event()

        @mcp.tool(task=True)
        async def progress_tool(ctx: Context) -> str:
            await ctx.report_progress(0, 100, "Starting...")
            await ctx.report_progress(50, 100, "Half done")
            await ctx.report_progress(100, 100, "Complete")
            progress_reported.set()
            return "done"

        async with Client(mcp) as client:
            task = await client.call_tool("progress_tool", {}, task=True)
            await asyncio.wait_for(progress_reported.wait(), timeout=5.0)
            await task.wait(timeout=5.0)
            result = await task.result()
            assert result.data == "done"

    async def test_context_wiring_in_background_task(self):
        """Context should be properly wired with task_id and session_id."""
        mcp = FastMCP("wiring-test")
        task_completed = asyncio.Event()
        captured: dict[str, object] = {}

        @mcp.tool(task=True)
        async def verify_wiring(ctx: Context) -> str:
            captured["task_id"] = ctx.task_id
            captured["session_id"] = ctx.session_id
            captured["is_background"] = ctx.is_background_task
            task_completed.set()
            return "ok"

        async with Client(mcp) as client:
            task = await client.call_tool("verify_wiring", {}, task=True)
            await asyncio.wait_for(task_completed.wait(), timeout=5.0)
            await task.wait(timeout=5.0)
            result = await task.result()
            assert result.data == "ok"

        assert captured["task_id"] is not None
        assert captured["session_id"] is not None
        assert captured["is_background"] is True

    async def test_elicit_accept_flow(self):
        """E2E: tool elicits input, client accepts via elicitation_handler."""
        mcp = FastMCP("elicit-accept-test")

        @mcp.tool(task=True)
        async def ask_name(ctx: Context) -> str:
            result = await ctx.elicit("What is your name?", str)
            if isinstance(result, AcceptedElicitation):
                return f"Hello, {result.data}!"
            return "No name provided"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "Bob"})

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("ask_name", {}, task=True)
            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "Hello, Bob!"

    async def test_elicit_decline_flow(self):
        """E2E: tool elicits input, client declines via elicitation_handler."""
        mcp = FastMCP("elicit-decline-test")

        @mcp.tool(task=True)
        async def optional_input(ctx: Context) -> str:
            result = await ctx.elicit("Want to provide a name?", str)
            if isinstance(result, DeclinedElicitation):
                return "User declined"
            if isinstance(result, AcceptedElicitation):
                return f"Got: {result.data}"
            return "Cancelled"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="decline")

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("optional_input", {}, task=True)
            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "User declined"

    async def test_elicit_with_pydantic_model(self):
        """E2E: tool elicits structured Pydantic input via elicitation_handler."""
        from pydantic import BaseModel

        class UserInfo(BaseModel):
            name: str
            age: int

        mcp = FastMCP("elicit-pydantic-test")

        @mcp.tool(task=True)
        async def get_user_info(ctx: Context) -> str:
            result = await ctx.elicit("Provide user info", UserInfo)
            if isinstance(result, AcceptedElicitation):
                assert isinstance(result.data, UserInfo)
                return f"{result.data.name} is {result.data.age}"
            return "No info"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"name": "Alice", "age": 30})

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("get_user_info", {}, task=True)
            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "Alice is 30"

    async def test_handle_task_input_rejects_when_not_waiting(self):
        """handle_task_input returns False when no task is waiting for input."""
        mcp = FastMCP("reject-test")

        @mcp.tool(task=True)
        async def simple_tool() -> str:
            return "done"

        async with Client(mcp) as client:
            task = await client.call_tool("simple_tool", {}, task=True)
            await task.wait(timeout=5.0)

            # Task already completed — no elicitation waiting
            success = await handle_task_input(
                task_id=task.task_id,
                session_id="nonexistent-session",
                action="accept",
                content={"value": "too late"},
                fastmcp=mcp,
            )
            assert success is False
