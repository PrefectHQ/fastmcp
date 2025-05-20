from typing import TypeAlias

from mcp.client.session import (
    LoggingFnT,
    MessageHandlerFnT,
)
from mcp.types import LoggingMessageNotificationParams

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

LogMessage: TypeAlias = LoggingMessageNotificationParams
LogHandler: TypeAlias = LoggingFnT
MessageHandler: TypeAlias = MessageHandlerFnT

__all__ = ["LogMessage", "LogHandler", "MessageHandler"]


async def default_log_handler(params: LogMessage) -> None:
    logger.debug(f"Log received: {params}")
