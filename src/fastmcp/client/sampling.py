import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import mcp.types
from mcp import CreateMessageResult
from mcp.client.session import ClientSession, SamplingFnT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import SamplingMessage

from fastmcp.server.sampling.handler import ServerSamplingHandler

__all__ = ["SamplingMessage", "SamplingParams", "SamplingHandler"]


ClientSamplingHandler: TypeAlias = Callable[
    [
        list[SamplingMessage],
        SamplingParams,
        RequestContext[ClientSession, LifespanContextT],
    ],
    str | CreateMessageResult | Awaitable[str | CreateMessageResult],
]

SamplingHandler: TypeAlias = (
    ClientSamplingHandler[LifespanContextT] | ServerSamplingHandler[LifespanContextT]
)


def create_sampling_callback(
    sampling_handler: ClientSamplingHandler[LifespanContextT],
) -> SamplingFnT:
    async def _sampling_handler(
        context: RequestContext[ClientSession, LifespanContextT],
        params: SamplingParams,
    ) -> CreateMessageResult | mcp.types.ErrorData:
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
