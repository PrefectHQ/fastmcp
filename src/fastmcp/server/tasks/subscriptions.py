"""Task subscription helpers for sending MCP notifications (SEP-1686).

Subscribes to Docket execution state changes and sends notifications/tasks/status
to clients when their tasks change state.

This module requires fastmcp[tasks] (pydocket). It is only imported when docket is available.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from docket.execution import ExecutionState
from mcp.types import TaskStatusNotification, TaskStatusNotificationParams

from fastmcp.server.tasks.keys import parse_task_key
from fastmcp.server.tasks.requests import DOCKET_TO_MCP_STATE
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution
    from mcp.server.session import ServerSession

logger = get_logger(__name__)


async def subscribe_to_task_updates(
    task_id: str,
    task_key: str,
    session: ServerSession,
    docket: Docket,
    poll_interval_ms: int = 5000,
) -> None:
    """Subscribe to Docket execution events and send MCP notifications.

    Per SEP-1686 lines 436-444, servers MAY send notifications/tasks/status
    when task state changes. This is an optional optimization that reduces
    client polling frequency.

    Args:
        task_id: Client-visible task ID (server-generated UUID)
        task_key: Internal Docket execution key (includes session, type, component)
        session: MCP ServerSession for sending notifications
        docket: Docket instance for subscribing to execution events
        poll_interval_ms: Poll interval in milliseconds to include in notifications
    """
    try:
        execution = await docket.get_execution(task_key)
        if execution is None:
            logger.warning(f"No execution found for task {task_id}")
            return

        # Subscribe to state and progress events from Docket
        async for event in execution.subscribe():
            if event["type"] == "state":
                # Send notifications/tasks/status when state changes
                await _send_status_notification(
                    session=session,
                    task_id=task_id,
                    task_key=task_key,
                    docket=docket,
                    state=event["state"],
                    poll_interval_ms=poll_interval_ms,
                )
            elif event["type"] == "progress":
                # Send notification when progress message changes
                await _send_progress_notification(
                    session=session,
                    task_id=task_id,
                    task_key=task_key,
                    docket=docket,
                    execution=execution,
                    poll_interval_ms=poll_interval_ms,
                )

    except Exception as e:
        logger.warning(f"Subscription task failed for {task_id}: {e}", exc_info=True)


async def _send_status_notification(
    session: ServerSession,
    task_id: str,
    task_key: str,
    docket: Docket,
    state: ExecutionState,
    poll_interval_ms: int = 5000,
) -> None:
    """Send notifications/tasks/status to client.

    Per SEP-1686 line 454: notification SHOULD NOT include related-task metadata
    (taskId is already in params).

    Args:
        session: MCP ServerSession
        task_id: Client-visible task ID
        task_key: Internal task key (for metadata lookup)
        docket: Docket instance
        state: Docket execution state (enum)
        poll_interval_ms: Poll interval in milliseconds
    """
    # Map Docket state to MCP status
    state_map = DOCKET_TO_MCP_STATE
    mcp_status = state_map.get(state, "failed")

    # Extract session_id from task_key for Redis lookup
    key_parts = parse_task_key(task_key)
    session_id = key_parts["session_id"]

    created_at_key = docket.key(f"fastmcp:task:{session_id}:{task_id}:created_at")
    async with docket.redis() as redis:
        created_at_bytes = await redis.get(created_at_key)

    created_at = (
        created_at_bytes.decode("utf-8")
        if created_at_bytes
        else datetime.now(timezone.utc).isoformat()
    )

    # Build status message
    status_message = None
    if state == ExecutionState.COMPLETED:
        status_message = "Task completed successfully"
    elif state == ExecutionState.FAILED:
        status_message = "Task failed"
    elif state == ExecutionState.CANCELLED:
        status_message = "Task cancelled"

    params_dict = {
        "taskId": task_id,
        "status": mcp_status,
        "createdAt": created_at,
        "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
        "ttl": 60000,
        "pollInterval": poll_interval_ms,
    }

    if status_message:
        params_dict["statusMessage"] = status_message

    # Create notification (no related-task metadata per spec line 454)
    notification = TaskStatusNotification(
        params=TaskStatusNotificationParams.model_validate(params_dict),
    )

    # Send notification (don't let failures break the subscription)
    with suppress(Exception):
        await session.send_notification(notification)  # type: ignore[arg-type]


async def _send_progress_notification(
    session: ServerSession,
    task_id: str,
    task_key: str,
    docket: Docket,
    execution: Execution,
    poll_interval_ms: int = 5000,
) -> None:
    """Send notifications/tasks/status when progress updates.

    Args:
        session: MCP ServerSession
        task_id: Client-visible task ID
        task_key: Internal task key
        docket: Docket instance
        execution: Execution object with current progress
        poll_interval_ms: Poll interval in milliseconds
    """
    # Sync execution to get latest progress
    await execution.sync()

    # Only send if there's a progress message
    if not execution.progress or not execution.progress.message:
        return

    # Map Docket state to MCP status
    state_map = DOCKET_TO_MCP_STATE
    mcp_status = state_map.get(execution.state, "failed")

    # Extract session_id from task_key for Redis lookup
    key_parts = parse_task_key(task_key)
    session_id = key_parts["session_id"]

    created_at_key = docket.key(f"fastmcp:task:{session_id}:{task_id}:created_at")
    async with docket.redis() as redis:
        created_at_bytes = await redis.get(created_at_key)

    created_at = (
        created_at_bytes.decode("utf-8")
        if created_at_bytes
        else datetime.now(timezone.utc).isoformat()
    )

    params_dict = {
        "taskId": task_id,
        "status": mcp_status,
        "createdAt": created_at,
        "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
        "ttl": 60000,
        "pollInterval": poll_interval_ms,
        "statusMessage": execution.progress.message,
    }

    # Create and send notification
    notification = TaskStatusNotification(
        params=TaskStatusNotificationParams.model_validate(params_dict),
    )

    with suppress(Exception):
        await session.send_notification(notification)  # type: ignore[arg-type]
