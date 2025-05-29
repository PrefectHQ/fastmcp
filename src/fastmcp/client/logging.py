from collections.abc import Awaitable, Callable
from typing import TypeAlias

from mcp.client.session import LoggingFnT, MessageHandlerFnT
from mcp.types import LoggingMessageNotificationParams

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

LogMessage: TypeAlias = LoggingMessageNotificationParams
LogHandler: TypeAlias = Callable[[LogMessage], Awaitable[None]]
MessageHandler: TypeAlias = MessageHandlerFnT


async def default_log_handler(message: LogMessage) -> None:
    logger.debug(f"Log received: {message}")


def create_log_callback(handler: LogHandler | None = None) -> LoggingFnT:
    if handler is None:
        handler = default_log_handler

    async def log_callback(params: LoggingMessageNotificationParams) -> None:
        await handler(params)

    return log_callback
