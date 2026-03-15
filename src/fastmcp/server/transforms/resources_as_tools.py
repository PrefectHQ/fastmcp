"""Transform that exposes resources as tools.

This transform generates tools for listing and reading resources, enabling
clients that only support tools to access resource functionality.

The generated tools route through `ctx.fastmcp` at runtime, so all server
middleware (auth, visibility, rate limiting, etc.) applies to resource
operations exactly as it would for direct `resources/read` calls.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms import ResourcesAsTools

    mcp = FastMCP("Server")
    mcp.add_transform(ResourcesAsTools(mcp))
    # Now has list_resources and read_resource tools
    ```
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import ToolAnnotations

from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

_DEFAULT_ANNOTATIONS = ToolAnnotations(readOnlyHint=True)

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class ResourcesAsTools(Transform):
    """Transform that adds tools for listing and reading resources.

    Generates two tools:
    - `list_resources`: Lists all resources and templates
    - `read_resource`: Reads a resource by URI

    The generated tools route through the server at runtime, so auth,
    middleware, and visibility apply automatically.

    This transform should be applied to a FastMCP server instance, not
    a raw Provider, because the generated tools need the server's
    middleware chain for auth and visibility filtering.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(ResourcesAsTools(mcp))
        # Now has list_resources and read_resource tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def __repr__(self) -> str:
        return f"ResourcesAsTools({self._provider!r})"

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Add resource tools to the tool list."""
        return [
            *tools,
            self._make_list_resources_tool(),
            self._make_read_resource_tool(),
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, including generated resource tools."""
        if name == "list_resources":
            return self._make_list_resources_tool()
        if name == "read_resource":
            return self._make_read_resource_tool()
        return await call_next(name, version=version)

    def _make_list_resources_tool(self) -> Tool:
        """Create the list_resources tool."""

        async def list_resources() -> str:
            """List all available resources and resource templates.

            Returns JSON with resource metadata. Static resources have a
            'uri' field, while templates have a 'uri_template' field with
            placeholders like {name}.
            """
            ctx = get_context()
            resources = await ctx.fastmcp.list_resources()
            templates = await ctx.fastmcp.list_resource_templates()

            result: list[dict[str, Any]] = []

            for r in resources:
                result.append(
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description,
                        "mime_type": r.mime_type,
                    }
                )

            for t in templates:
                result.append(
                    {
                        "uri_template": t.uri_template,
                        "name": t.name,
                        "description": t.description,
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_resources, annotations=_DEFAULT_ANNOTATIONS)

    def _make_read_resource_tool(self) -> Tool:
        """Create the read_resource tool."""

        async def read_resource(
            uri: Annotated[str, "The URI of the resource to read"],
        ) -> str:
            """Read a resource by its URI.

            For static resources, provide the exact URI. For templated
            resources, provide the URI with template parameters filled in.

            Returns the resource content as a string. Binary content is
            base64-encoded.
            """
            ctx = get_context()
            result = await ctx.fastmcp.read_resource(uri)
            return _format_result(result)

        return Tool.from_function(fn=read_resource, annotations=_DEFAULT_ANNOTATIONS)


def _format_result(result: Any) -> str:
    """Format ResourceResult for tool output.

    Single text content is returned as-is. Single binary content is
    base64-encoded. Multiple contents are JSON-encoded.
    """
    if len(result.contents) == 1:
        content = result.contents[0].content
        if isinstance(content, bytes):
            return base64.b64encode(content).decode()
        return content

    return json.dumps(
        [
            {
                "content": (
                    c.content
                    if isinstance(c.content, str)
                    else base64.b64encode(c.content).decode()
                ),
                "mime_type": c.mime_type,
            }
            for c in result.contents
        ]
    )
