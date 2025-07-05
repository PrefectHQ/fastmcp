from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeAlias, TypeVar

import mcp.types
from mcp import ClientSession
from mcp.client.session import ElicitationFnT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import ElicitRequestParams
from mcp.types import ElicitResult as MCPElicitResult
from pydantic_core import to_jsonable_python

from fastmcp.utilities.json_schema_type import json_schema_to_type

__all__ = ["ElicitRequestParams", "ElicitResult", "ElicitationHandler"]

T = TypeVar("T")


class ElicitResult(MCPElicitResult, Generic[T]):
    content: T | None = None


ElicitationHandler: TypeAlias = Callable[
    [
        str,  # message
        type[T],  # a class for creating a structured response
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
            if params.requestedSchema == {"type": "object", "properties": {}}:
                response_type = None
            else:
                response_type = json_schema_to_type(params.requestedSchema)

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
            return MCPElicitResult(**result.model_dump() | {"content": content})
        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _elicitation_handler
