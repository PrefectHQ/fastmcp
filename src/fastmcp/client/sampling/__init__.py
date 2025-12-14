import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias, TypeVar

import mcp.types
from mcp import ClientSession, CreateMessageResult
from mcp.client.session import SamplingFnT
from mcp.server.session import ServerSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import CreateMessageResultWithTools, SamplingMessage

# Result type that handlers can return
SamplingHandlerResult: TypeAlias = (
    str | CreateMessageResult | CreateMessageResultWithTools
)

# Session type for sampling handlers - works with both client and server sessions
SessionT = TypeVar("SessionT", ClientSession, ServerSession)

# Unified sampling handler type that works for both clients and servers.
# Handlers receive messages and parameters from the MCP sampling flow
# and return LLM responses.
SamplingHandler: TypeAlias = Callable[
    [
        list[SamplingMessage],
        SamplingParams,
        RequestContext[SessionT, LifespanContextT],
    ],
    SamplingHandlerResult | Awaitable[SamplingHandlerResult],
]


__all__ = [
    "RequestContext",
    "SamplingHandler",
    "SamplingHandlerResult",
    "SamplingMessage",
    "SamplingParams",
    "create_sampling_callback",
]


def create_sampling_callback(
    sampling_handler: SamplingHandler,
) -> SamplingFnT:
    async def _sampling_handler(
        context,
        params: SamplingParams,
    ) -> CreateMessageResult | CreateMessageResultWithTools | mcp.types.ErrorData:
        try:
            result = sampling_handler(params.messages, params, context)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, str):
                result = CreateMessageResult(
                    role="assistant",
                    model="fastmcp-client",
                    content=mcp.types.TextContent(type="text", text=result),
                )
            return result
        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _sampling_handler
