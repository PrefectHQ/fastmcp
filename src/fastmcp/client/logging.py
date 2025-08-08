from collections.abc import Awaitable, Callable
from typing import TypeAlias

from mcp.client.session import LoggingFnT
from mcp.types import LoggingMessageNotificationParams

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

LogMessage: TypeAlias = LoggingMessageNotificationParams
LogHandler: TypeAlias = Callable[[LogMessage], Awaitable[None]]


async def default_log_handler(message: LogMessage) -> None:
    """Default handler that properly routes server log messages to appropriate log levels."""
    msg = message.data.get("msg", str(message))
    extra = message.data.get("extra", {})

    # Map MCP log levels to Python logging levels
    level_map = {
        "debug": logger.debug,
        "info": logger.info,
        "notice": logger.info,  # Python doesn't have 'notice', map to info
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
        "alert": logger.critical,  # Map alert to critical
        "emergency": logger.critical,  # Map emergency to critical
    }

    # Get the appropriate logging function based on the message level
    log_fn = level_map.get(message.level.lower(), logger.info)

    # Include logger name if available
    if message.logger:
        msg = f"[{message.logger}] {msg}"

    # Log with appropriate level and extra data
    log_fn(f"Server log: {msg}", extra=extra)


def create_log_callback(handler: LogHandler | None = None) -> LoggingFnT:
    if handler is None:
        handler = default_log_handler

    async def log_callback(params: LoggingMessageNotificationParams) -> None:
        await handler(params)

    return log_callback
