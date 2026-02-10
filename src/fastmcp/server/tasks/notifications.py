"""Distributed notification queue for background task events (SEP-1686).

Enables distributed Docket workers to send MCP notifications to clients
without holding session references. Workers push to a Redis queue,
the MCP server process subscribes and forwards to the client's session.

Pattern: Fire-and-forward with retry
- One queue per session_id
- LPUSH/BRPOP for reliable ordered delivery
- Retry up to 3 times on delivery failure, then discard
- TTL-based expiration for stale messages

Note: Docket's execution.subscribe() handles task state/progress events via
Redis Pub/Sub. This module handles elicitation-specific notifications that
require reliable delivery (input_required prompts, cancel signals).
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import mcp.types

if TYPE_CHECKING:
    from docket import Docket
    from mcp.server.session import ServerSession

    from fastmcp.server.server import FastMCP

logger = logging.getLogger(__name__)

# Redis key patterns
NOTIFICATION_QUEUE_KEY = "fastmcp:notifications:{session_id}"
NOTIFICATION_ACTIVE_KEY = "fastmcp:notifications:{session_id}:active"

# Configuration
NOTIFICATION_TTL_SECONDS = 300  # 5 minute message TTL (elicitation response window)
MAX_DELIVERY_ATTEMPTS = 3  # Retry failed deliveries before discarding
SUBSCRIBER_TIMEOUT_SECONDS = 30  # BRPOP timeout (also heartbeat interval)


async def push_notification(
    session_id: str,
    notification: dict[str, Any],
    docket: Docket,
) -> None:
    """Push notification to session's queue (called from Docket worker).

    Used for elicitation-specific notifications (input_required, cancel)
    that need reliable delivery across distributed processes.

    Args:
        session_id: Target session's identifier
        notification: MCP notification dict (method, params, _meta)
        docket: Docket instance for Redis access
    """
    key = docket.key(NOTIFICATION_QUEUE_KEY.format(session_id=session_id))
    message = json.dumps(
        {
            "notification": notification,
            "attempt": 0,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    async with docket.redis() as redis:
        await redis.lpush(key, message)  # type: ignore[invalid-await]  # redis-py union type (sync/async)
        await redis.expire(key, NOTIFICATION_TTL_SECONDS)


async def notification_subscriber_loop(
    session_id: str,
    session: ServerSession,
    docket: Docket,
    fastmcp: FastMCP,
) -> None:
    """Subscribe to notification queue and forward to session.

    Runs in the MCP server process. Bridges distributed workers to clients.

    This loop:
    1. Maintains a heartbeat (active subscriber marker for debugging)
    2. Blocks on BRPOP waiting for notifications
    3. Forwards notifications to the client's session
    4. Retries failed deliveries, then discards (no dead-letter queue)

    Args:
        session_id: Session identifier to subscribe to
        session: MCP ServerSession for sending notifications
        docket: Docket instance for Redis access
        fastmcp: FastMCP server instance (for elicitation relay)
    """
    queue_key = docket.key(NOTIFICATION_QUEUE_KEY.format(session_id=session_id))
    active_key = docket.key(NOTIFICATION_ACTIVE_KEY.format(session_id=session_id))

    logger.debug("Starting notification subscriber for session %s", session_id)

    while True:
        try:
            async with docket.redis() as redis:
                # Heartbeat: mark subscriber as active (for distributed debugging)
                await redis.set(active_key, "1", ex=SUBSCRIBER_TIMEOUT_SECONDS * 2)

                # Blocking wait for notification (timeout refreshes heartbeat)
                # Using BRPOP (right pop) for FIFO order with LPUSH (left push)
                result = await cast(
                    Any, redis.brpop([queue_key], timeout=SUBSCRIBER_TIMEOUT_SECONDS)
                )
                if not result:
                    continue  # Timeout - refresh heartbeat and retry

                _, message_bytes = result
                message = json.loads(message_bytes)
                notification_dict = message["notification"]
                attempt = message.get("attempt", 0)

                try:
                    # Reconstruct and send MCP notification
                    await _send_mcp_notification(
                        session, notification_dict, session_id, docket, fastmcp
                    )
                    logger.debug(
                        "Delivered notification to session %s (attempt %d)",
                        session_id,
                        attempt + 1,
                    )
                except Exception as send_error:
                    # Delivery failed - retry or discard
                    if attempt < MAX_DELIVERY_ATTEMPTS - 1:
                        # Re-queue with incremented attempt (back of queue)
                        message["attempt"] = attempt + 1
                        message["last_error"] = str(send_error)
                        await redis.lpush(queue_key, json.dumps(message))  # type: ignore[invalid-await]
                        logger.debug(
                            "Requeued notification for session %s (attempt %d): %s",
                            session_id,
                            attempt + 2,
                            send_error,
                        )
                    else:
                        # Discard after max attempts (session likely disconnected)
                        logger.warning(
                            "Discarding notification for session %s after %d attempts: %s",
                            session_id,
                            MAX_DELIVERY_ATTEMPTS,
                            send_error,
                        )

        except asyncio.CancelledError:
            # Graceful shutdown - leave pending messages in queue for reconnect
            logger.debug("Notification subscriber cancelled for session %s", session_id)
            break
        except Exception as e:
            logger.debug(
                "Notification subscriber error for session %s: %s", session_id, e
            )
            await asyncio.sleep(1)  # Backoff on error


async def _send_mcp_notification(
    session: ServerSession,
    notification_dict: dict[str, Any],
    session_id: str,
    docket: Docket,
    fastmcp: FastMCP,
) -> None:
    """Reconstruct MCP notification from dict and send to session.

    For input_required notifications with elicitation metadata, also sends
    a standard elicitation/create request to the client and relays the
    response back to the worker via Redis.

    Args:
        session: MCP ServerSession
        notification_dict: Notification as dict (method, params, _meta)
        session_id: Session identifier (for elicitation relay)
        docket: Docket instance (for notification delivery)
        fastmcp: FastMCP server instance (for elicitation relay)
    """
    method = notification_dict.get("method", "notifications/tasks/status")
    if method != "notifications/tasks/status":
        raise ValueError(f"Unsupported notification method for subscriber: {method}")

    notification = mcp.types.TaskStatusNotification.model_validate(
        {
            "method": "notifications/tasks/status",
            "params": notification_dict.get("params", {}),
            "_meta": notification_dict.get("_meta"),
        }
    )
    server_notification = mcp.types.ServerNotification(notification)

    await session.send_notification(server_notification)

    # If this is an input_required notification with elicitation metadata,
    # relay the elicitation to the client via standard elicitation/create
    params = notification_dict.get("params", {})
    if params.get("status") == "input_required":
        meta = notification_dict.get("_meta", {})
        related_task = meta.get("io.modelcontextprotocol/related-task", {})
        elicitation = related_task.get("elicitation")
        if elicitation:
            task_id = params.get("taskId")
            if not task_id:
                logger.warning(
                    "input_required notification missing taskId, skipping relay"
                )
                return
            from fastmcp.server.tasks.elicitation import relay_elicitation

            task = asyncio.create_task(
                relay_elicitation(session, session_id, task_id, elicitation, fastmcp),
                name=f"elicitation-relay-{task_id[:8]}",
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)


# =============================================================================
# Subscriber Management
# =============================================================================

# Strong references to fire-and-forget relay tasks (prevent GC mid-flight)
_background_tasks: set[asyncio.Task[None]] = set()

# Registry of active subscribers per session (prevents duplicates)
# Uses weakref to session to detect disconnects
_active_subscribers: dict[
    str, tuple[asyncio.Task[None], weakref.ref[ServerSession]]
] = {}


async def ensure_subscriber_running(
    session_id: str,
    session: ServerSession,
    docket: Docket,
    fastmcp: FastMCP,
) -> None:
    """Start notification subscriber if not already running (idempotent).

    Subscriber is created on first task submission and cleaned up on disconnect.
    Safe to call multiple times for the same session.

    Args:
        session_id: Session identifier
        session: MCP ServerSession
        docket: Docket instance
        fastmcp: FastMCP server instance (for elicitation relay)
    """
    # Check if subscriber already running for this session
    if session_id in _active_subscribers:
        task, session_ref = _active_subscribers[session_id]
        # Check if task is still running AND session is still alive
        if not task.done() and session_ref() is not None:
            return  # Already running

        # Task finished or session dead - clean up
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        del _active_subscribers[session_id]

    # Start new subscriber task
    task = asyncio.create_task(
        notification_subscriber_loop(session_id, session, docket, fastmcp),
        name=f"notification-subscriber-{session_id[:8]}",
    )
    _active_subscribers[session_id] = (task, weakref.ref(session))
    logger.debug("Started notification subscriber for session %s", session_id)


async def stop_subscriber(session_id: str) -> None:
    """Stop notification subscriber for a session.

    Called when session disconnects. Pending messages remain in queue
    for delivery if client reconnects (with TTL expiration).

    Args:
        session_id: Session identifier
    """
    if session_id not in _active_subscribers:
        return

    task, _ = _active_subscribers.pop(session_id)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    logger.debug("Stopped notification subscriber for session %s", session_id)


def get_subscriber_count() -> int:
    """Get number of active subscribers (for monitoring)."""
    return len(_active_subscribers)
