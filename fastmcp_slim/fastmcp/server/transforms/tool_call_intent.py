"""Ask for the agent's stated purpose when calling a tool."""

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Annotated, Any

from pydantic import Field

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.tools.tool_transform import TransformedTool, forward_raw
from fastmcp.utilities.versions import VersionSpec

_current_intent: ContextVar[str | None] = ContextVar("tool_call_intent", default=None)


async def _capture_intent(
    *,
    intent: Annotated[
        str | None,
        Field(
            description="In one sentence, what are you trying to accomplish with this call? Omit personal details."
        ),
    ] = None,
    **arguments: Any,
) -> ToolResult:
    if intent is not None and not isinstance(intent, str):
        raise ValueError("intent must be a string")
    token = _current_intent.set(intent)
    try:
        return await forward_raw(**arguments)
    finally:
        _current_intent.reset(token)


def _with_intent(tool: Tool) -> Tool:
    # A root subschema can own or forbid this argument even when the outer
    # properties do not mention it. Preserve those contracts as authored.
    schema = tool.parameters
    if (
        "intent" in schema.get("properties", {})
        or "intent" in schema.get("required", ())
        or schema.keys()
        & {
            "$ref",
            "$dynamicRef",
            "allOf",
            "anyOf",
            "oneOf",
            "not",
            "if",
            "dependentSchemas",
            "patternProperties",
            "propertyNames",
        }
    ):
        return tool
    transformed = TransformedTool.from_tool(
        tool, transform_fn=_capture_intent, output_schema=tool.output_schema
    )
    # The provider owns the rest of the schema, including constraints on the
    # whole object and any extra arguments accepted by a remote tool.
    intent = transformed.parameters["properties"]["intent"]
    transformed.parameters = {
        **tool.parameters,
        "properties": {**tool.parameters.get("properties", {}), "intent": intent},
    }
    return transformed


class ToolCallIntent(Transform):
    """Add an optional `intent` argument and expose it as `ctx.tool_call_intent`.

    Tools that declare their own `intent` argument retain its original behavior.
    """

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [_with_intent(tool) for tool in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return _with_intent(tool) if tool is not None else None
