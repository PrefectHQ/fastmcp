from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeAlias

import mcp_types
from mcp import ClientSession
from mcp.client.session import ClientRequestContext, ElicitationFnT
from mcp_types import ElicitRequestFormParams, ElicitRequestParams
from mcp_types import ElicitResult as MCPElicitResult
from pydantic_core import to_jsonable_python
from typing_extensions import TypeVar

from fastmcp.client._sdk_context_shim import LifespanContextT, RequestContext
from fastmcp.utilities.json_schema_type import json_schema_to_type

__all__ = ["ElicitRequestParams", "ElicitResult", "ElicitationHandler"]

T = TypeVar("T", default=Any)


class ElicitResult(MCPElicitResult, Generic[T]):
    content: T | None = None  # ty:ignore[invalid-attribute-override]


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


def _validate_form_content(content: dict[str, Any], schema: dict[str, Any]) -> None:
    """Check accepted form content against the server's requested schema.

    Validates the JSON value that goes on the wire, as JSON Schema defines it,
    so `"42"` does not satisfy an integer property and ISO strings satisfy
    `format: date-time`.
    """
    # Imported lazily, as the MCP SDK does for tool output validation.
    from jsonschema.validators import validator_for
    from referencing import Registry

    # An empty registry keeps `$ref` resolution inside the schema document.
    validator_for(schema)(schema, registry=Registry()).validate(content)


def create_elicitation_callback(
    elicitation_handler: ElicitationHandler,
) -> ElicitationFnT:
    async def _elicitation_handler(
        context: ClientRequestContext,
        params: ElicitRequestParams,
    ) -> MCPElicitResult | mcp_types.ErrorData:
        try:
            # requestedSchema only exists on ElicitRequestFormParams, not ElicitRequestURLParams
            if isinstance(params, ElicitRequestFormParams):
                if params.requested_schema == {"type": "object", "properties": {}}:
                    response_type = None
                else:
                    response_type = json_schema_to_type(params.requested_schema)
            else:
                # URL-based elicitation doesn't have a schema
                response_type = None

            # The public ElicitationHandler alias is typed against the
            # subscriptable RequestContext shim; the runtime object is the SDK's
            # ClientRequestContext, passed through opaquely.
            result = await elicitation_handler(
                params.message,
                response_type,
                params,
                context,  # ty: ignore[invalid-argument-type]
            )
            # if the user returns data, we assume they've accepted the elicitation.
            # An ElicitResult, including the SDK's own, carries an explicit action.
            if not isinstance(result, MCPElicitResult):
                result = ElicitResult(action="accept", content=result)
            content = to_jsonable_python(result.content)
            if not isinstance(content, dict | None):
                # Auto-wrap scalar values for ScalarElicitationType schemas
                # (single "value" property). This lets handlers return T directly
                # for ctx.elicit("msg", str/int/float/bool).
                if isinstance(params, ElicitRequestFormParams) and set(
                    params.requested_schema.get("properties", {}).keys()
                ) == {"value"}:
                    content = {"value": content}
                else:
                    raise ValueError(
                        "Elicitation responses must be serializable as a JSON object (dict). Received: "
                        f"{result.content!r}"
                    )
            if isinstance(content, dict) and isinstance(
                params, ElicitRequestFormParams
            ):
                # Elicitation properties have no null type: an optional field
                # without a value is omitted rather than sent as null.
                required = set(params.requested_schema.get("required", []))
                content = {
                    key: value
                    for key, value in content.items()
                    if value is not None or key in required
                }
            if (
                result.action == "accept"
                and content is not None
                and isinstance(params, ElicitRequestFormParams)
            ):
                _validate_form_content(content, params.requested_schema)
            return MCPElicitResult(
                _meta=result.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                action=result.action,
                content=content,
            )

        except Exception as e:
            return mcp_types.ErrorData(
                code=mcp_types.INTERNAL_ERROR,
                message=str(e),
            )

    return _elicitation_handler
