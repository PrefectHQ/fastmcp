from collections.abc import Awaitable, Callable
from typing import TypeAlias

from mcp import CreateMessageResult
from mcp.server.session import ServerSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import CreateMessageResultWithTools, SamplingMessage

# Result type that handlers can return
SamplingHandlerResult: TypeAlias = (
    str | CreateMessageResult | CreateMessageResultWithTools
)

ServerSamplingHandler: TypeAlias = Callable[
    [
        list[SamplingMessage],
        SamplingParams,
        RequestContext[ServerSession, LifespanContextT],
    ],
    SamplingHandlerResult | Awaitable[SamplingHandlerResult],
]
