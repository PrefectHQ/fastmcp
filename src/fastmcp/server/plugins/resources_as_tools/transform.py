"""Low-level transform that powers the ResourcesAsTools plugin.

`ResourcesAsToolsTransform` appends two synthetic tools —
`list_resources` and `read_resource` — to the tool catalog, so clients
that only speak the tools protocol can still drive resource discovery
and reads. Both generated tools route through `get_context().fastmcp`
at request time, so middleware, auth, and visibility all apply exactly
as they would for direct `resources/*` calls.

Most users should configure this through the `ResourcesAsTools` plugin
(`fastmcp.server.plugins.resources_as_tools`). The transform is exposed
for advanced composition and for backcompat with the old
`fastmcp.server.transforms.resources_as_tools` path.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import ToolAnnotations

from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec

_DEFAULT_ANNOTATIONS = ToolAnnotations(readOnlyHint=True)

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class ResourcesAsToolsTransform(Transform):
    """Transform that adds `list_resources` and `read_resource` synthetic tools.

    The generated tools call back into the server via `ctx.fastmcp` at
    request time, so server middleware (auth, visibility, rate limiting)
    applies automatically.

    The `provider` argument exists purely for intent — if passed, it
    must be a FastMCP server instance (raw providers don't expose an
    `add_transform` path anyway). The plugin wrapper constructs this
    transform without a provider.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(ResourcesAsToolsTransform(mcp))
        ```
    """

    def __init__(self, provider: Provider | None = None) -> None:
        if provider is not None:
            from fastmcp.server.server import FastMCP

            if not isinstance(provider, FastMCP):
                raise TypeError(
                    "ResourcesAsToolsTransform accepts a FastMCP server instance, "
                    f"not a {type(provider).__name__}. The generated tools route "
                    "through the server's middleware chain at runtime for auth "
                    "and visibility. Pass your FastMCP server, or omit the "
                    "argument entirely when using the plugin wrapper."
                )
        self._provider = provider

    def __repr__(self) -> str:
        return f"ResourcesAsToolsTransform({self._provider!r})"

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            *tools,
            self._make_list_resources_tool(),
            self._make_read_resource_tool(),
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "list_resources":
            return self._make_list_resources_tool()
        if name == "read_resource":
            return self._make_read_resource_tool()
        return await call_next(name, version=version)

    def _make_list_resources_tool(self) -> Tool:
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
                result.append(  # noqa: PERF401
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description,
                        "mime_type": r.mime_type,
                    }
                )

            for t in templates:
                result.append(  # noqa: PERF401
                    {
                        "uri_template": t.uri_template,
                        "name": t.name,
                        "description": t.description,
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_resources, annotations=_DEFAULT_ANNOTATIONS)

    def _make_read_resource_tool(self) -> Tool:
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
