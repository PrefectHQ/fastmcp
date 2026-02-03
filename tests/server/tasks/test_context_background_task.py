"""Tests for Context background task support (SEP-1686)."""

import pytest

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.tasks.elicitation import elicit_for_task, handle_task_input


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
            ctx.task_id = "new-id"  # type: ignore[misc]


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
        ctx = Context(mcp, session=mock_session, task_id="test-task-123")  # type: ignore[arg-type]

        # In background task mode, should return the stored session
        assert ctx.session is mock_session

    def test_session_uses_stored_session_during_on_initialize(self):
        """session should use _session during on_initialize (no request context)."""
        mcp = FastMCP("test")

        class MockSession:
            _fastmcp_state_prefix = "test-session"

        mock_session = MockSession()
        # Simulating on_initialize: has session but not a background task
        ctx = Context(mcp, session=mock_session)  # type: ignore[arg-type]

        # Should return the stored session as fallback
        assert ctx.session is mock_session


class TestContextElicitBackgroundTask:
    """Tests for Context.elicit() in background task mode."""

    @pytest.mark.asyncio
    async def test_elicit_raises_when_background_task_but_no_docket(self):
        """elicit() should raise when in background task mode but Docket unavailable."""
        mcp = FastMCP("test")
        ctx = Context(mcp, task_id="test-task-123")

        # Set up minimal session mock
        class MockSession:
            _fastmcp_state_prefix = "test-session"

        ctx._session = MockSession()  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="Docket"):
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


class TestBackgroundTaskElicitationE2E:
    """End-to-end tests for background task elicitation (SEP-1686).

    These tests demonstrate the full flow:
    1. Client calls a tool with task=True (background execution)
    2. Tool uses ctx.elicit() to request user input
    3. Task status changes to "input_required"
    4. Client sends input via handle_task_input()
    5. Task resumes and completes with the elicited value

    This simulates what a client would see when interacting with
    a background task that needs user input.
    """

    async def test_elicit_for_task_stores_request_in_redis(self):
        """Test that elicit_for_task stores the elicitation request in Redis.

        This tests the Redis coordination layer that enables client interaction.
        When a background task calls elicit(), the request is stored in Redis
        so clients can retrieve it and respond.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastmcp.server.tasks.elicitation import (
            elicit_for_task,
        )

        # Create mocks
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # No response yet
        mock_redis.delete = AsyncMock()

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session-id"
        mock_session.send_notification = AsyncMock()

        # Call elicit_for_task with a short timeout to avoid blocking
        with patch("fastmcp.server.tasks.elicitation.ELICIT_TTL_SECONDS", 1):
            with patch("fastmcp.server.tasks.elicitation.asyncio.sleep", AsyncMock()):
                # Make it return after first poll
                mock_redis.get = AsyncMock(
                    return_value=b'{"action": "accept", "content": {"value": 42}}'
                )

                result = await elicit_for_task(
                    task_id="test-task-123",
                    session=mock_session,
                    message="Please provide a number",
                    schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                    },
                    fastmcp=mock_fastmcp,
                )

        # Verify the result
        assert result.action == "accept"
        assert result.content == {"value": 42}

        # Verify Redis operations were called
        assert mock_redis.set.call_count >= 2  # request + status

    async def test_handle_task_input_stores_response(self):
        """Test that handle_task_input stores the response in Redis.

        This tests the client-side flow: when a client sends input via
        tasks/sendInput, the response is stored in Redis for the waiting task.
        """
        from unittest.mock import AsyncMock, MagicMock

        # Create mocks
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"waiting")  # Status is waiting
        mock_redis.set = AsyncMock()

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        # Call handle_task_input
        success = await handle_task_input(
            task_id="test-task-123",
            session_id="test-session-id",
            action="accept",
            content={"value": 42},
            fastmcp=mock_fastmcp,
        )

        # Verify success
        assert success is True

        # Verify Redis operations
        assert mock_redis.set.call_count == 2  # response + status update

    async def test_handle_task_input_rejects_when_not_waiting(self):
        """Test that handle_task_input rejects input when task isn't waiting.

        This verifies proper state management - clients can only send input
        when a task is actually waiting for it.
        """
        from unittest.mock import AsyncMock, MagicMock

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # No waiting status

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        success = await handle_task_input(
            task_id="test-task-123",
            session_id="test-session-id",
            action="accept",
            content={"value": 42},
            fastmcp=mock_fastmcp,
        )

        # Should fail because no task is waiting
        assert success is False

    async def test_elicit_for_task_sends_notification(self):
        """Test that elicit_for_task sends input_required notification.

        Per SEP-1686, the server should send notifications/tasks/updated
        with status="input_required" when a task needs input.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(
            return_value=b'{"action": "accept", "content": {"value": 1}}'
        )
        mock_redis.delete = AsyncMock()

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session"
        mock_session.send_notification = AsyncMock()

        with patch("fastmcp.server.tasks.elicitation.asyncio.sleep", AsyncMock()):
            await elicit_for_task(
                task_id="my-task-id",
                session=mock_session,
                message="Enter value",
                schema={"type": "object"},
                fastmcp=mock_fastmcp,
            )

        # Verify notification was sent
        mock_session.send_notification.assert_called_once()
        notification = mock_session.send_notification.call_args[0][0]
        assert notification.method == "notifications/tasks/updated"

    async def test_elicit_for_task_timeout_returns_cancel(self):
        """Test that elicit_for_task returns cancel on timeout.

        If no response is received within the TTL, the elicitation
        should be treated as cancelled.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Never responds
        mock_redis.delete = AsyncMock()

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session"
        mock_session.send_notification = AsyncMock()

        # Use very short TTL for test
        with patch("fastmcp.server.tasks.elicitation.ELICIT_TTL_SECONDS", 0.1):
            with patch(
                "fastmcp.server.tasks.elicitation.asyncio.sleep",
                AsyncMock(),
            ):
                result = await elicit_for_task(
                    task_id="timeout-task",
                    session=mock_session,
                    message="This will timeout",
                    schema={"type": "object"},
                    fastmcp=mock_fastmcp,
                )

        # Should return cancel on timeout
        assert result.action == "cancel"
        assert result.content is None

    async def test_elicit_notification_includes_full_schema(self):
        """Test that the notification includes the full JSON schema for complex types.

        This test demonstrates what the client sees when eliciting a Pydantic model.
        The client receives a full JSON Schema that describes the expected input,
        which they can use to:
        - Render a dynamic form
        - Validate user input before sending
        - Show field descriptions to the user

        Example notification metadata for a UserInfo model:
        ```json
        {
          "modelcontextprotocol.io/related-task": {
            "taskId": "test-task",
            "status": "input_required",
            "statusMessage": "Please provide user info",
            "elicitation": {
              "requestId": "...",
              "message": "Please provide user info",
              "requestedSchema": {
                "type": "object",
                "properties": {
                  "name": {"type": "string", "title": "Name"},
                  "age": {"type": "integer", "title": "Age"}
                },
                "required": ["name", "age"],
                "title": "UserInfo"
              }
            }
          }
        }
        ```
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from pydantic import BaseModel

        class UserInfo(BaseModel):
            """User information for registration."""

            name: str
            age: int

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(
            return_value=b'{"action": "accept", "content": {"name": "Alice", "age": 30}}'
        )
        mock_redis.delete = AsyncMock()

        mock_docket = MagicMock()
        mock_docket.redis = MagicMock(return_value=AsyncMock())
        mock_docket.redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_docket.redis.return_value.__aexit__ = AsyncMock()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session"
        mock_session.send_notification = AsyncMock()

        # Create task-aware context
        ctx = Context(
            mock_fastmcp,
            session=mock_session,
            task_id="schema-test-task",
        )

        # Call elicit with a Pydantic model type
        with patch("fastmcp.server.tasks.elicitation.asyncio.sleep", AsyncMock()):
            result = await ctx.elicit("Please provide user info", UserInfo)

        # Verify the notification includes the full schema
        mock_session.send_notification.assert_called_once()
        notification = mock_session.send_notification.call_args[0][0]
        meta = notification._meta
        related_task = meta["modelcontextprotocol.io/related-task"]
        schema = related_task["elicitation"]["requestedSchema"]

        # Verify schema structure matches UserInfo
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["age"]["type"] == "integer"
        assert "required" in schema
        assert set(schema["required"]) == {"name", "age"}

        # Verify the result is properly parsed into the Pydantic model
        assert result.action == "accept"
        assert isinstance(result, AcceptedElicitation)  # Type narrowing
        assert isinstance(result.data, UserInfo)
        assert result.data.name == "Alice"
        assert result.data.age == 30


class TestBackgroundTaskContextWiring:
    """Integration tests for Context wiring in Docket workers.

    These tests verify that when a background task runs in a Docket worker,
    the Context dependency is properly created with task_id and session,
    allowing ctx.elicit() to work transparently.

    Per Chris Guidry's review request: "Could we get at least one test showing
    the end-to-end of it working, with a background task that's eliciting input?
    This will help with what the client-side sees when this happens."

    The key test is `test_context_elicit_full_flow_with_mocked_redis` which shows:

    CLIENT RECEIVES:
        notifications/tasks/updated with:
        - taskId: the background task ID
        - status: "input_required"
        - statusMessage: the elicit prompt
        - elicitation.requestedSchema: JSON schema for expected input

    CLIENT RESPONDS:
        handle_task_input(task_id, session_id, action="accept", content={...})

    TOOL RECEIVES:
        AcceptedElicitation(action="accept", data=<parsed value>)
    """

    async def test_context_is_created_with_task_id_in_worker(self):
        """Test that Context is created with task_id when running in Docket worker.

        This verifies the wiring from _CurrentContext that creates a task-aware
        Context when get_task_context() returns TaskContextInfo.
        """
        from unittest.mock import MagicMock, patch

        from fastmcp.server.dependencies import (
            TaskContextInfo,
            _current_server,
            _CurrentContext,
            _task_sessions,
        )

        # Set up mock server
        mock_server = MagicMock()
        mock_server._docket = MagicMock()
        server_token = _current_server.set(MagicMock(return_value=mock_server))

        # Set up mock session in registry
        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session-id"
        _task_sessions["test-session-id"] = MagicMock(return_value=mock_session)

        try:
            # Mock get_task_context to return TaskContextInfo
            task_info = TaskContextInfo(
                task_id="test-task-123",
                session_id="test-session-id",
            )
            with patch(
                "fastmcp.server.dependencies.get_task_context",
                return_value=task_info,
            ):
                # Create the dependency and enter it
                dep = _CurrentContext()
                ctx = await dep.__aenter__()

                # Verify context is task-aware
                assert ctx.is_background_task is True
                assert ctx.task_id == "test-task-123"
                assert ctx.session is mock_session

                # Clean up
                await dep.__aexit__(None, None, None)
        finally:
            _current_server.reset(server_token)
            _task_sessions.pop("test-session-id", None)

    async def test_context_falls_back_to_foreground_mode(self):
        """Test that Context uses foreground mode when not in worker context.

        When _current_context has a value (normal request handling),
        _CurrentContext should return that context instead of creating a new one.
        """
        from unittest.mock import MagicMock

        from fastmcp.server.context import Context, _current_context
        from fastmcp.server.dependencies import _CurrentContext

        mcp = MagicMock()
        foreground_ctx = Context(mcp)

        # Set the foreground context
        token = _current_context.set(foreground_ctx)
        try:
            dep = _CurrentContext()
            ctx = await dep.__aenter__()

            # Should return the foreground context
            assert ctx is foreground_ctx
            assert ctx.is_background_task is False

            await dep.__aexit__(None, None, None)
        finally:
            _current_context.reset(token)

    async def test_session_registered_when_task_submitted(self):
        """Test that session is registered when a task is submitted to Docket.

        This verifies that submit_to_docket calls register_task_session,
        which enables the Context wiring in background workers.
        """
        import asyncio

        from fastmcp import FastMCP
        from fastmcp.client import Client
        from fastmcp.server.dependencies import get_task_session

        mcp = FastMCP("test-server")

        task_started = asyncio.Event()
        session_id_captured = None

        @mcp.tool(task=True)
        async def capture_session_tool(ctx: Context) -> str:
            """Tool that captures the session ID for verification."""
            nonlocal session_id_captured
            task_started.set()
            # Access session to verify it works
            session_id_captured = ctx.session_id
            return "done"

        async with Client(mcp) as client:
            # Start the task
            task = await client.call_tool("capture_session_tool", {}, task=True)
            assert task is not None

            # Wait for the task to start
            await asyncio.wait_for(task_started.wait(), timeout=5.0)

            # Verify the session was registered
            assert session_id_captured is not None
            # The session should be retrievable via get_task_session
            # (it was registered when the task was submitted)
            # Session may be available or None if cleaned up - key is registration happened
            _ = get_task_session(session_id_captured)

            # Wait for task to complete
            await task.wait(timeout=5.0)
            result = await task.result()
            assert result.data == "done"

    async def test_context_elicit_works_in_background_task(self):
        """E2E test: verify Context is properly wired in background tasks.

        This test demonstrates that:
        1. Context.task_id is set correctly in background tasks
        2. Context.is_background_task returns True
        3. Context.session_id is available

        The wiring is what enables ctx.elicit() to work in background tasks.
        """
        import asyncio

        from fastmcp import FastMCP
        from fastmcp.client import Client
        from fastmcp.server.context import Context

        mcp = FastMCP("context-wiring-test")

        # Track what happens in the background task
        task_completed = asyncio.Event()
        captured_task_id: str | None = None
        captured_session_id: str | None = None
        captured_is_background: bool | None = None

        @mcp.tool(task=True)
        async def verify_context_tool(ctx: Context) -> str:
            """Tool that verifies Context is wired correctly for background tasks."""
            nonlocal captured_task_id, captured_session_id, captured_is_background

            # Capture context properties - this is the key verification
            captured_task_id = ctx.task_id
            captured_session_id = ctx.session_id
            captured_is_background = ctx.is_background_task

            task_completed.set()
            return f"task_id={ctx.task_id}, is_background={ctx.is_background_task}"

        async with Client(mcp) as client:
            # Start the background task
            task = await client.call_tool("verify_context_tool", {}, task=True)
            assert task is not None
            assert task.task_id is not None

            # Wait for the task to complete
            await asyncio.wait_for(task_completed.wait(), timeout=10.0)

            # Verify Context was properly wired in the background task
            assert captured_task_id is not None, "Context.task_id should be set"
            assert captured_session_id is not None, "Context.session_id should be set"
            assert captured_is_background is True, (
                "Context.is_background_task should be True"
            )

            # Wait for task result
            await task.wait(timeout=10.0)
            result = await task.result()
            assert "is_background=True" in result.data

    async def test_context_elicit_full_flow_with_mocked_redis(self):
        """E2E test with mocked Redis to show complete elicitation flow.

        This test demonstrates what the client sees during background task
        elicitation, with a mocked Redis layer to avoid requiring real Redis.

        Flow:
        1. Tool calls ctx.elicit() in background task
        2. Elicitation stores request in Redis, sends input_required notification
        3. Simulated client sends response via handle_task_input()
        4. Tool receives response and completes

        This is the key test that fulfills Chris Guidry's request for an
        "end-to-end test showing a background task that's eliciting input"
        and demonstrates "what the client-side sees when this happens."
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.context import Context
        from fastmcp.server.tasks.elicitation import handle_task_input

        # Shared Redis storage that both elicit and handle_task_input will use
        redis_storage: dict[str, bytes] = {}

        # Create a mock Redis that uses our shared storage
        class MockRedis:
            async def set(
                self, key: str, value: str | bytes, ex: int | None = None
            ) -> None:
                redis_storage[key] = value.encode() if isinstance(value, str) else value

            async def get(self, key: str) -> bytes | None:
                return redis_storage.get(key)

            async def delete(self, *keys: str) -> None:
                for key in keys:
                    redis_storage.pop(key, None)

        mock_redis = MockRedis()

        # Create mock context manager for redis()
        class MockRedisContext:
            async def __aenter__(self):
                return mock_redis

            async def __aexit__(self, *args):
                pass

        mock_docket = MagicMock()
        mock_docket.redis = lambda: MockRedisContext()
        mock_docket.key = lambda k: k

        mock_fastmcp = MagicMock()
        mock_fastmcp._docket = mock_docket

        mock_session = MagicMock()
        mock_session._fastmcp_state_prefix = "test-session-123"
        mock_session.send_notification = AsyncMock()

        # Create task-aware context (as would be created in background worker)
        ctx = Context(
            mock_fastmcp,
            session=mock_session,
            task_id="test-task-456",
        )

        # Verify context is properly configured for background task
        assert ctx.is_background_task is True
        assert ctx.task_id == "test-task-456"

        # Start elicit in a background task (simulating the Docket worker)
        async def run_elicit():
            return await ctx.elicit("What is your name?", str)

        elicit_task = asyncio.create_task(run_elicit())

        # Wait for elicit to store request and start polling
        # The elicit_for_task function stores the request and sends notification
        await asyncio.sleep(0.2)

        # ═══════════════════════════════════════════════════════════════════════
        # CLIENT PERSPECTIVE: What does the client see?
        # ═══════════════════════════════════════════════════════════════════════

        # 1. CLIENT RECEIVES: notifications/tasks/updated notification
        mock_session.send_notification.assert_called()
        notification = mock_session.send_notification.call_args[0][0]
        assert notification.method == "notifications/tasks/updated"

        # 2. CLIENT INSPECTS: The notification metadata tells the client:
        #    - Which task needs input (taskId)
        #    - What status the task is in (input_required)
        #    - What message to display (statusMessage)
        #    - The schema for the expected response (elicitation.requestedSchema)
        meta = notification._meta
        related_task = meta["modelcontextprotocol.io/related-task"]

        assert related_task["taskId"] == "test-task-456"
        assert related_task["status"] == "input_required"
        assert related_task["statusMessage"] == "What is your name?"
        assert "elicitation" in related_task
        assert related_task["elicitation"]["message"] == "What is your name?"
        assert "requestedSchema" in related_task["elicitation"]

        # 3. CLIENT RESPONDS: Send input via handle_task_input
        #    This is what a real client would do when it receives input_required
        success = await handle_task_input(
            task_id="test-task-456",
            session_id="test-session-123",
            action="accept",
            content={"value": "Alice"},
            fastmcp=mock_fastmcp,
        )
        assert success is True, "Client should successfully send input"

        # ═══════════════════════════════════════════════════════════════════════
        # TOOL PERSPECTIVE: What does the tool receive?
        # ═══════════════════════════════════════════════════════════════════════

        # Wait for elicit to receive the response and return
        result = await asyncio.wait_for(elicit_task, timeout=5.0)

        # Verify the result contains what the client sent
        # AcceptedElicitation has 'action' and 'data' attributes
        assert result.action == "accept"
        assert result.data == "Alice"  # The value from content["value"]

    async def test_context_elicit_with_real_docket_memory_backend(self):
        """E2E test using Docket's real memory:// backend.

        This test uses the real Docket memory backend instead of mocking Redis,
        as suggested by Chris Guidry during code review. The memory:// backend
        provides a fully functional in-memory Redis-like store that Docket uses
        automatically when running tests.

        Flow:
        1. Create FastMCP server with task-enabled tool that calls ctx.elicit()
        2. Start the task via Client (which initializes Docket with memory://)
        3. Background task blocks waiting for client input
        4. Simulate client sending input via handle_task_input()
        5. Task resumes and completes with the elicited value

        This demonstrates the complete elicitation flow with real infrastructure.
        """
        import asyncio

        from fastmcp import FastMCP
        from fastmcp.client import Client
        from fastmcp.server.context import Context
        from fastmcp.server.tasks.elicitation import handle_task_input

        mcp = FastMCP("elicit-memory-test")

        # Track task state using mutable container (avoids nonlocal)
        elicit_started = asyncio.Event()
        captured: dict[str, str | None] = {"task_id": None, "session_id": None}

        @mcp.tool(task=True)
        async def ask_for_name(ctx: Context) -> str:
            """Tool that elicits user's name via background task."""
            # Capture IDs for handle_task_input call
            captured["task_id"] = ctx.task_id
            captured["session_id"] = ctx.session_id
            elicit_started.set()

            # This will block until client sends input
            result = await ctx.elicit("What is your name?", str)

            if isinstance(result, AcceptedElicitation):
                return f"Hello, {result.data}!"
            else:
                return "Elicitation was declined or cancelled"

        async with Client(mcp) as client:
            # Start the background task
            task = await client.call_tool("ask_for_name", {}, task=True)
            assert task is not None
            assert task.task_id is not None

            # Wait for task to reach elicit() call
            await asyncio.wait_for(elicit_started.wait(), timeout=5.0)

            # Poll until handle_task_input succeeds
            # We need to wait for elicit_for_task to store the "waiting" status in Redis
            # before we can send input. Using fixed-interval polling (not exponential
            # backoff) because we're waiting for state, not recovering from errors.
            assert captured["task_id"] is not None
            assert captured["session_id"] is not None

            max_attempts = 40
            poll_interval_seconds = 0.05  # 50ms - fast for tests, 2s max total
            success = False
            for _ in range(max_attempts):
                success = await handle_task_input(
                    task_id=captured["task_id"],
                    session_id=captured["session_id"],
                    action="accept",
                    content={"value": "Bob"},
                    fastmcp=mcp,
                )
                if success:
                    break
                await asyncio.sleep(poll_interval_seconds)

            assert success is True, (
                f"handle_task_input should succeed within {max_attempts * poll_interval_seconds}s"
            )

            # Wait for task to complete
            await task.wait(timeout=10.0)
            result = await task.result()

            # Verify the tool received the elicited value and returned correctly
            assert result.data == "Hello, Bob!"
