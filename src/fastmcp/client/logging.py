from typing import TypeAlias

from mcp.client.session import (
    LoggingFnT,
    MessageHandlerFnT,
)
from mcp.types import LoggingMessageNotificationParams

LogMessage: TypeAlias = LoggingMessageNotificationParams
LogHandler: TypeAlias = LoggingFnT
MessageHandler: TypeAlias = MessageHandlerFnT

__all__ = ["LogMessage", "LogHandler", "MessageHandler"]
