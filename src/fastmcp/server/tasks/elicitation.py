"""Background task elicitation support (SEP-1686).

This module provides elicitation capabilities for background tasks running
in Docket workers. Unlike regular MCP requests, background tasks don't have
an active request context, so elicitation requires special handling:

1. Set task status to "input_required" via Redis
2. Send notifications/tasks/updated with elicitation metadata
3. Wait for client to send input via tasks/sendInput
4. Resume task execution with the provided input

This uses the public MCP SDK APIs where possible, with minimal use of
internal APIs for background task coordination.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

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
    session: ServerSession,
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

    # Get session ID for Redis key construction
    session_id = getattr(session, "_fastmcp_state_prefix", None)
    if session_id is None:
        # Generate a session ID if not already set
        session_id = str(uuid.uuid4())
        session._fastmcp_state_prefix = session_id  # type: ignore[attr-defined]

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

    # Send task status update notification with input_required status
    # This follows SEP-1686 for background task status updates
    notification = mcp.types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/tasks/updated",
        params={},
        _meta={  # type: ignore[call-arg]
            "modelcontextprotocol.io/related-task": {
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
    )

    # Send notification (best effort - task status is stored in Redis)
    # Log failures for debugging but don't fail the elicitation
    try:
        await session.send_notification(notification)  # type: ignore[arg-type]
    except Exception as e:
        logger.warning(
            "Failed to send input_required notification for task %s: %s",
            task_id,
            e,
        )

    # Wait for response (poll Redis)
    # In a production implementation, this could use Redis pub/sub for lower latency
    max_wait_seconds = ELICIT_TTL_SECONDS
    poll_interval = 0.5  # seconds

    for _ in range(int(max_wait_seconds / poll_interval)):
        async with docket.redis() as redis:
            response_data = await redis.get(docket.key(response_key))
            if response_data:
                response = json.loads(response_data)
                # Clean up Redis keys
                await redis.delete(
                    docket.key(request_key),
                    docket.key(response_key),
                    docket.key(status_key),
                )
                # Convert to ElicitResult
                return mcp.types.ElicitResult(
                    action=response.get("action", "accept"),
                    content=response.get("content"),
                )

        await asyncio.sleep(poll_interval)

    # Timeout - treat as cancellation
    async with docket.redis() as redis:
        await redis.delete(
            docket.key(request_key),
            docket.key(response_key),
            docket.key(status_key),
        )

    return mcp.types.ElicitResult(action="cancel", content=None)


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

        # Store the response
        await redis.set(
            docket.key(response_key),
            json.dumps(response),
            ex=ELICIT_TTL_SECONDS,
        )
        # Update status to "responded"
        await redis.set(
            docket.key(status_key),
            "responded",
            ex=ELICIT_TTL_SECONDS,
        )

    return True
