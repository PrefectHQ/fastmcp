"""Middleware that dereferences $ref in JSON schemas before sending to clients."""

from collections.abc import Sequence
from typing import Any

import mcp.types as mt
from typing_extensions import override

from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool
from fastmcp.utilities.json_schema import dereference_refs


class DereferenceRefsMiddleware(Middleware):
    """Dereferences $ref in component schemas before sending to clients.

    Some MCP clients (e.g., VS Code Copilot) don't handle JSON Schema $ref
    properly. This middleware inlines all $ref definitions so schemas are
    self-contained. Enabled by default via ``FastMCP(dereference_schemas=True)``.
    """

    @override
    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        return [_dereference_tool(tool) for tool in tools]

    @override
    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        templates = await call_next(context)
        return [_dereference_resource_template(t) for t in templates]


def _dereference_tool(tool: Tool) -> Tool:
    """Return a copy of the tool with dereferenced schemas."""
    updates: dict[str, object] = {}
    if "$defs" in tool.parameters or _has_ref(tool.parameters):
        updates["parameters"] = dereference_refs(tool.parameters)
    if tool.output_schema is not None and (
        "$defs" in tool.output_schema or _has_ref(tool.output_schema)
    ):
        updates["output_schema"] = dereference_refs(tool.output_schema)
    if updates:
        return tool.model_copy(update=updates)
    return tool


def _dereference_resource_template(template: ResourceTemplate) -> ResourceTemplate:
    """Return a copy of the template with dereferenced schemas."""
    if "$defs" in template.parameters or _has_ref(template.parameters):
        return template.model_copy(
            update={"parameters": dereference_refs(template.parameters)}
        )
    return template


def _has_ref(schema: dict[str, Any]) -> bool:
    """Check if a schema contains any $ref."""
    if "$ref" in schema:
        return True
    for value in schema.values():
        if isinstance(value, dict) and _has_ref(value):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _has_ref(item):
                    return True
    return False
