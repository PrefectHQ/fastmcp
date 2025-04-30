import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import mcp.types
from mcp import ClientSession, CreateMessageResult
from mcp.client.session import SamplingFnT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import SamplingMessage

__all__ = ["SamplingMessage", "SamplingParams", "MessageResult", "SamplingHandler"]


class MessageResult(CreateMessageResult):
    role: mcp.types.Role = "assistant"
    content: mcp.types.TextContent | mcp.types.ImageContent
    model: str = "client-model"


SamplingHandler: TypeAlias = Callable[
    [
        list[SamplingMessage],
        SamplingParams,
        RequestContext[ClientSession, LifespanContextT],
    ],
    str | CreateMessageResult | Awaitable[str | CreateMessageResult],
]


def create_sampling_callback(sampling_handler: SamplingHandler) -> SamplingFnT:
    async def _sampling_handler(
        context: RequestContext[ClientSession, LifespanContextT],
        params: SamplingParams,
    ) -> CreateMessageResult | mcp.types.ErrorData:
        try:
            result = sampling_handler(params.messages, params, context)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, str):
                result = MessageResult(
                    content=mcp.types.TextContent(type="text", text=result)
                )
            return result
        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _sampling_handler
