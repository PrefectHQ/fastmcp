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

from mcp.types import ToolAnnotations

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import AuthContext, run_auth_checks
from fastmcp.server.context import _current_transport
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.server.transforms.visibility import (
    apply_session_transforms,
    is_enabled,
)
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

_DEFAULT_ANNOTATIONS = ToolAnnotations(readOnlyHint=True)

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken
    from fastmcp.server.providers.base import Provider


def _get_auth_context() -> tuple[bool, AccessToken | None]:
    """Get auth context for the current request.

    Returns (skip_auth, token) where skip_auth=True for STDIO transport.
    Mirrors FastMCP._get_auth_context() to ensure consistent behavior.
    """
    if _current_transport.get() == "stdio":
        return (True, None)
    return (False, get_access_token())


class ResourcesAsTools(Transform):
    """Transform that adds tools for listing and reading resources.

    Generates two tools:
    - `list_resources`: Lists all resources and templates from the provider
    - `read_resource`: Reads a resource by URI

    The transform captures a provider reference at construction and queries it
    for resources when the generated tools are called. Auth and visibility
    filtering is applied regardless of provider type.

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
            from fastmcp.server.server import FastMCP

            if isinstance(provider, FastMCP):
                all_resources = await provider.list_resources()
                all_templates = await provider.list_resource_templates()
            else:
                transformed_resources = await apply_session_transforms(
                    list(await provider.list_resources())
                )
                all_resources = list(
                    await _filter_authorized(
                        [r for r in transformed_resources if is_enabled(r)]
                    )
                )
                transformed_templates = await apply_session_transforms(
                    list(await provider.list_resource_templates())
                )
                all_templates = list(
                    await _filter_authorized(
                        [t for t in transformed_templates if is_enabled(t)]
                    )
                )

            result: list[dict[str, Any]] = []

            for r in all_resources:
                result.append(
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description,
                        "mime_type": r.mime_type,
                    }
                )

            for t in all_templates:
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
            from fastmcp.server.server import FastMCP

            # Use FastMCP.read_resource() if available - runs middleware chain
            if isinstance(provider, FastMCP):
                result = await provider.read_resource(uri)
                return _format_result(result)

            # Plain providers: apply visibility and auth checks
            resource = await provider.get_resource(uri)
            if resource is not None:
                await _check_component_access(resource)
                result = await resource._read()
                return _format_result(result)

            template = await provider.get_resource_template(uri)
            if template is not None:
                await _check_component_access(template)
                params = template.matches(uri)
                if params is not None:
                    result = await template._read(uri, params)
                    return _format_result(result)

            raise ValueError(f"Resource not found: {uri}")

        return Tool.from_function(fn=read_resource, annotations=_DEFAULT_ANNOTATIONS)


async def _check_component_access(component: Any) -> None:
    """Check visibility and auth for a single component.

    Applies session transforms, then checks is_enabled and runs auth checks.
    Skips auth checks for STDIO transport (consistent with FastMCP server).
    Raises ValueError if the component is disabled or unauthorized.
    """
    marked = await apply_session_transforms([component])
    if not marked or not is_enabled(marked[0]):
        raise ValueError(f"Resource not found: {component.name}")

    skip_auth, token = _get_auth_context()
    if not skip_auth and component.auth is not None:
        ctx = AuthContext(token=token, component=component)
        try:
            if not await run_auth_checks(component.auth, ctx):
                raise ValueError(f"Resource not found: {component.name}")
        except AuthorizationError:
            raise ValueError(f"Resource not found: {component.name}") from None


async def _filter_authorized(components: Sequence[Any]) -> Sequence[Any]:
    """Filter components by auth checks.

    Skips auth checks for STDIO transport (consistent with FastMCP server).
    Returns only components that pass auth checks.
    """
    skip_auth, token = _get_auth_context()
    if skip_auth:
        return components

    authorized: list[Any] = []
    for component in components:
        if component.auth is not None:
            ctx = AuthContext(token=token, component=component)
            try:
                if not await run_auth_checks(component.auth, ctx):
                    continue
            except AuthorizationError:
                continue
        authorized.append(component)
    return authorized


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
