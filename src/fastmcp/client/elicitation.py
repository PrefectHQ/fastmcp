from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

import mcp.types
from mcp import ClientSession
from mcp.client.session import ElicitationFnT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import ElicitRequestParams, ElicitResult

__all__ = ["ElicitRequestParams", "ElicitResult", "ElicitationHandler"]


ElicitationHandler: TypeAlias = Callable[
    [
        str,  # message
        dict[str, Any],  # requested_schema
        RequestContext[ClientSession, LifespanContextT],
    ],
    ElicitResult | Awaitable[ElicitResult],
]


def create_elicitation_callback(
    elicitation_handler: ElicitationHandler,
) -> ElicitationFnT:
    async def _elicitation_handler(
        context: RequestContext[ClientSession, LifespanContextT],
        params: ElicitRequestParams,
    ) -> ElicitResult | mcp.types.ErrorData:
        try:
            result = elicitation_handler(
                params.message, params.requestedSchema, context
            )
            if inspect.isawaitable(result):
                result = await result

            return result
        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _elicitation_handler
