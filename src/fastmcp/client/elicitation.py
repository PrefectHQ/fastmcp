from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeAlias

import mcp.types
from mcp import ClientSession
from mcp.client.session import ElicitationFnT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import ElicitRequestFormParams, ElicitRequestParams
from mcp.types import ElicitResult as MCPElicitResult
from pydantic_core import to_jsonable_python
from typing_extensions import TypeVar

from fastmcp.utilities.json_schema_type import json_schema_to_type

__all__ = ["ElicitRequestParams", "ElicitResult", "ElicitationHandler"]

T = TypeVar("T", default=Any)


class ElicitResult(MCPElicitResult, Generic[T]):
    content: T | None = None


ElicitationHandler: TypeAlias = Callable[
    [
        str,  # message
        type[T]
        | None,  # a class for creating a structured response (None for URL elicitation)
        ElicitRequestParams,
        RequestContext[ClientSession, LifespanContextT],
    ],
    Awaitable[T | dict[str, Any] | ElicitResult[T | dict[str, Any]]],
]


def create_elicitation_callback(
    elicitation_handler: ElicitationHandler,
) -> ElicitationFnT:
    async def _elicitation_handler(
        context: RequestContext[ClientSession, LifespanContextT],
        params: ElicitRequestParams,
    ) -> MCPElicitResult | mcp.types.ErrorData:
        try:
            # requestedSchema only exists on ElicitRequestFormParams, not ElicitRequestURLParams
            if isinstance(params, ElicitRequestFormParams):
                if params.requestedSchema == {"type": "object", "properties": {}}:
                    response_type = None
                else:
                    response_type = json_schema_to_type(params.requestedSchema)
            else:
                # URL-based elicitation doesn't have a schema
                response_type = None

            result = await elicitation_handler(
                params.message, response_type, params, context
            )
            # if the user returns data, we assume they've accepted the elicitation
            if not isinstance(result, ElicitResult):
                result = ElicitResult(action="accept", content=result)
            content = to_jsonable_python(result.content)
            if not isinstance(content, dict | None):
                raise ValueError(
                    "Elicitation responses must be serializable as a JSON object (dict). Received: "
                    f"{result.content!r}"
                )
            return MCPElicitResult(
                _meta=result.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                action=result.action,
                content=content,
            )

        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _elicitation_handler
