"""Background task elicitation support (SEP-1686).

This module provides elicitation capabilities for background tasks running
in Docket workers. Unlike regular MCP requests, background tasks don't have
an active request context, so elicitation requires special handling:

1. Set task status to "input_required" via Redis
2. Send notifications/tasks/status with elicitation metadata
3. Wait for client to send input via tasks/sendInput
4. Resume task execution with the provided input

This uses the public MCP SDK APIs where possible, with minimal use of
internal APIs for background task coordination.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import mcp.types
from mcp import ServerSession

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


# Redis key patterns for task elicitation state
ELICIT_REQUEST_KEY = "fastmcp:task:{session_id}:{task_id}:elicit:request"
ELICIT_RESPONSE_KEY = "fastmcp:task:{session_id}:{task_id}:elicit:response"
ELICIT_STATUS_KEY = "fastmcp:task:{session_id}:{task_id}:elicit:status"

# TTL for elicitation state (1 hour)
ELICIT_TTL_SECONDS = 3600


async def elicit_for_task(
    task_id: str,
    session: ServerSession | None,
    message: str,
    schema: dict[str, Any],
    fastmcp: FastMCP,
) -> mcp.types.ElicitResult:
    """Send an elicitation request from a background task.

    This function handles the complexity of eliciting user input when running
    in a Docket worker context where there's no active MCP request.

    Args:
        task_id: The background task ID
        session: The MCP ServerSession for this task
        message: The message to display to the user
        schema: The JSON schema for the expected response
        fastmcp: The FastMCP server instance

    Returns:
        ElicitResult containing the user's response

    Raises:
        RuntimeError: If Docket is not available
        McpError: If the elicitation request fails
    """
    docket = fastmcp._docket
    if docket is None:
        raise RuntimeError(
            "Background task elicitation requires Docket. "
            "Ensure 'fastmcp[tasks]' is installed and the server has task-enabled components."
        )

    # Generate a unique request ID for this elicitation
    request_id = str(uuid.uuid4())

    # Get session ID from task context (authoritative source for background tasks)
    # This is extracted from the Docket execution key: {session_id}:{task_id}:...
    from fastmcp.server.dependencies import get_task_context

    task_context = get_task_context()
    if task_context is not None:
        session_id = task_context.session_id
    else:
        # Fallback: try to get from session attribute (shouldn't happen in background)
        session_id = getattr(session, "_fastmcp_state_prefix", None)
        if session_id is None:
            raise RuntimeError(
                "Cannot determine session_id for elicitation. "
                "This typically means elicit_for_task() was called outside a Docket worker context."
            )

    # Store elicitation request in Redis
    request_key = ELICIT_REQUEST_KEY.format(session_id=session_id, task_id=task_id)
    response_key = ELICIT_RESPONSE_KEY.format(session_id=session_id, task_id=task_id)
    status_key = ELICIT_STATUS_KEY.format(session_id=session_id, task_id=task_id)

    elicit_request = {
        "request_id": request_id,
        "message": message,
        "schema": schema,
    }

    async with docket.redis() as redis:
        # Store the elicitation request
        await redis.set(
            docket.key(request_key),
            json.dumps(elicit_request),
            ex=ELICIT_TTL_SECONDS,
        )
        # Set status to "waiting"
        await redis.set(
            docket.key(status_key),
            "waiting",
            ex=ELICIT_TTL_SECONDS,
        )

    # Send task status update notification with input_required status.
    # Use notifications/tasks/status so typed MCP clients can consume it.
    #
    # NOTE: We use the distributed notification queue instead of session.send_notification()
    # This enables notifications to work when workers run in separate processes
    # (Azure Web PubSub / Service Bus inspired pattern)
    timestamp = datetime.now(timezone.utc).isoformat()
    notification_dict = {
        "method": "notifications/tasks/status",
        "params": {
            "taskId": task_id,
            "status": "input_required",
            "statusMessage": message,
            "createdAt": timestamp,
            "lastUpdatedAt": timestamp,
            "ttl": ELICIT_TTL_SECONDS * 1000,
        },
        "_meta": {
            "io.modelcontextprotocol/related-task": {
                "taskId": task_id,
                "status": "input_required",
                "statusMessage": message,
                "elicitation": {
                    "requestId": request_id,
                    "message": message,
                    "requestedSchema": schema,
                },
            }
        },
    }

    # Push notification to Redis queue (works from any process)
    # Server's subscriber loop will forward to client
    from fastmcp.server.tasks.notifications import push_notification

    try:
        await push_notification(session_id, notification_dict, docket)
    except Exception as e:
        # Fail fast: if notification can't be queued, client won't know to respond
        # Return cancel immediately rather than waiting for 1-hour timeout
        logger.warning(
            "Failed to queue input_required notification for task %s, cancelling elicitation: %s",
            task_id,
            e,
        )
        # Best-effort cleanup
        try:
            async with docket.redis() as redis:
                await redis.delete(
                    docket.key(request_key),
                    docket.key(status_key),
                )
        except Exception:
            pass  # Keys will expire via TTL
        return mcp.types.ElicitResult(action="cancel", content=None)

    # Wait for response using BLPOP (blocking pop)
    # This is much more efficient than polling - single Redis round-trip
    # that blocks until a response is pushed, vs 7,200 round-trips/hour with polling
    max_wait_seconds = ELICIT_TTL_SECONDS

    try:
        async with docket.redis() as redis:
            # BLPOP blocks until an item is pushed to the list or timeout
            # Returns tuple of (key, value) or None on timeout
            result = await cast(
                Any,
                redis.blpop(
                    [docket.key(response_key)],
                    timeout=max_wait_seconds,
                ),
            )

            if result:
                # result is (key, value) tuple
                _key, response_data = result
                response = json.loads(response_data)

                # Clean up Redis keys
                await redis.delete(
                    docket.key(request_key),
                    docket.key(status_key),
                )

                # Convert to ElicitResult
                return mcp.types.ElicitResult(
                    action=response.get("action", "accept"),
                    content=response.get("content"),
                )
    except Exception as e:
        logger.warning(
            "BLPOP failed for task %s elicitation, falling back to cancel: %s",
            task_id,
            e,
        )

    # Timeout or error - treat as cancellation
    # Best-effort cleanup - if Redis is unavailable, keys will expire via TTL
    try:
        async with docket.redis() as redis:
            await redis.delete(
                docket.key(request_key),
                docket.key(response_key),
                docket.key(status_key),
            )
    except Exception as cleanup_error:
        logger.debug(
            "Failed to clean up elicitation keys for task %s (will expire via TTL): %s",
            task_id,
            cleanup_error,
        )

    return mcp.types.ElicitResult(action="cancel", content=None)


async def relay_elicitation(
    session: ServerSession,
    session_id: str,
    task_id: str,
    elicitation: dict[str, Any],
    fastmcp: FastMCP,
) -> None:
    """Relay elicitation from a background task worker to the client.

    Called by the notification subscriber when it detects an input_required
    notification with elicitation metadata. Sends a standard elicitation/create
    request to the client session, then uses handle_task_input() to push the
    response to Redis so the blocked worker can resume.

    Args:
        session: MCP ServerSession
        session_id: Session identifier
        task_id: Background task ID
        elicitation: Elicitation metadata (message, requestedSchema)
        fastmcp: FastMCP server instance
    """
    try:
        result = await session.elicit(
            message=elicitation["message"],
            requestedSchema=elicitation["requestedSchema"],
        )
        await handle_task_input(
            task_id=task_id,
            session_id=session_id,
            action=result.action,
            content=result.content,
            fastmcp=fastmcp,
        )
        logger.debug(
            "Relayed elicitation response for task %s (action=%s)",
            task_id,
            result.action,
        )
    except Exception as e:
        logger.warning("Failed to relay elicitation for task %s: %s", task_id, e)
        # Push a cancel response so the worker's BLPOP doesn't block forever
        success = await handle_task_input(
            task_id=task_id,
            session_id=session_id,
            action="cancel",
            content=None,
            fastmcp=fastmcp,
        )
        if not success:
            logger.warning(
                "Failed to push cancel response for task %s "
                "(worker may block until TTL)",
                task_id,
            )


async def handle_task_input(
    task_id: str,
    session_id: str,
    action: str,
    content: dict[str, Any] | None,
    fastmcp: FastMCP,
) -> bool:
    """Handle input sent to a background task via tasks/sendInput.

    This is called when a client sends input in response to an elicitation
    request from a background task.

    Args:
        task_id: The background task ID
        session_id: The MCP session ID
        action: The elicitation action ("accept", "decline", "cancel")
        content: The response content (for "accept" action)
        fastmcp: The FastMCP server instance

    Returns:
        True if the input was successfully stored, False otherwise
    """
    docket = fastmcp._docket
    if docket is None:
        return False

    response_key = ELICIT_RESPONSE_KEY.format(session_id=session_id, task_id=task_id)
    status_key = ELICIT_STATUS_KEY.format(session_id=session_id, task_id=task_id)

    response = {
        "action": action,
        "content": content,
    }

    async with docket.redis() as redis:
        # Check if there's a pending elicitation
        status = await redis.get(docket.key(status_key))
        if status is None or status.decode("utf-8") != "waiting":
            return False

        # Push response to list - this wakes up the BLPOP in elicit_for_task
        # Using LPUSH instead of SET enables the efficient blocking wait pattern
        await redis.lpush(  # type: ignore[invalid-await]  # redis-py union type (sync/async)
            docket.key(response_key),
            json.dumps(response),
        )
        # Set TTL on the response list (in case BLPOP doesn't consume it)
        await redis.expire(docket.key(response_key), ELICIT_TTL_SECONDS)

        # Update status to "responded"
        await redis.set(
            docket.key(status_key),
            "responded",
            ex=ELICIT_TTL_SECONDS,
        )

    return True
