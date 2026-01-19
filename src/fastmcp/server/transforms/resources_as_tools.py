"""Transform that exposes resources as tools.

This transform generates tools for listing and reading resources, enabling
clients that only support tools to access resource functionality.

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

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class ResourcesAsTools(Transform):
    """Transform that adds tools for listing and reading resources.

    Generates two tools:
    - `list_resources`: Lists all resources and templates from the provider
    - `read_resource`: Reads a resource by URI

    The transform captures a provider reference at construction and queries it
    for resources when the generated tools are called. When used with FastMCP,
    the provider's auth and visibility filtering is automatically applied.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(ResourcesAsTools(mcp))
        # Now has list_resources and read_resource tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        """Initialize the transform with a provider reference.

        Args:
            provider: The provider to query for resources. Typically this is
                the same FastMCP server the transform is added to.
        """
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
        # Check if it's one of our generated tools
        if name == "list_resources":
            return self._make_list_resources_tool()
        if name == "read_resource":
            return self._make_read_resource_tool()

        # Otherwise delegate to downstream
        return await call_next(name, version=version)

    def _make_list_resources_tool(self) -> Tool:
        """Create the list_resources tool."""
        provider = self._provider

        async def list_resources() -> str:
            """List all available resources and resource templates.

            Returns JSON with resource metadata. Static resources have a 'uri' field,
            while templates have a 'uri_template' field with placeholders like {name}.
            """
            resources = await provider.list_resources()
            templates = await provider.list_resource_templates()

            result: list[dict[str, Any]] = []

            # Static resources
            for r in resources:
                result.append(
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description,
                        "mime_type": r.mime_type,
                    }
                )

            # Resource templates (URI contains placeholders like {name})
            for t in templates:
                result.append(
                    {
                        "uri_template": t.uri_template,
                        "name": t.name,
                        "description": t.description,
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_resources)

    def _make_read_resource_tool(self) -> Tool:
        """Create the read_resource tool."""
        provider = self._provider

        async def read_resource(
            uri: Annotated[str, "The URI of the resource to read"],
        ) -> str:
            """Read a resource by its URI.

            For static resources, provide the exact URI. For templated resources,
            provide the URI with template parameters filled in.

            Returns the resource content as a string. Binary content is
            base64-encoded.
            """
            from fastmcp import FastMCP

            # Use FastMCP.read_resource() if available - runs middleware chain
            if isinstance(provider, FastMCP):
                result = await provider.read_resource(uri)
                return _format_result(result)

            # Fallback for plain providers - no middleware
            resource = await provider.get_resource(uri)
            if resource is not None:
                result = await resource._read()
                return _format_result(result)

            template = await provider.get_resource_template(uri)
            if template is not None:
                params = template.matches(uri)
                if params is not None:
                    result = await template._read(uri, params)
                    return _format_result(result)

            raise ValueError(f"Resource not found: {uri}")

        return Tool.from_function(fn=read_resource)


def _format_result(result: Any) -> str:
    """Format ResourceResult for tool output.

    Single text content is returned as-is. Single binary content is base64-encoded.
    Multiple contents are JSON-encoded with each item containing content and mime_type.
    """
    # result is a ResourceResult with .contents list
    if len(result.contents) == 1:
        content = result.contents[0].content
        if isinstance(content, bytes):
            return base64.b64encode(content).decode()
        return content

    # Multiple contents - JSON encode
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
