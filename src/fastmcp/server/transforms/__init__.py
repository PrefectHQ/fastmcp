"""Transform system for component transformations.

Transforms modify components (tools, resources, prompts) using a middleware pattern.
Each transform wraps the next in the chain via `call_next`, allowing transforms to
intercept, modify, or replace component queries.

Unlike middleware (which operates on requests), transforms are observable by the
system for task registration, tag filtering, and component introspection.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms import Namespace

    server = FastMCP("Server")
    mount = server.mount(other_server)
    mount.add_transform(Namespace("api"))  # Tools become api_toolname
    ```
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool

# Type aliases for call_next signatures
ListToolsNext = Callable[[], Awaitable[Sequence["Tool"]]]
GetToolNext = Callable[[str], Awaitable["Tool | None"]]

ListResourcesNext = Callable[[], Awaitable[Sequence["Resource"]]]
GetResourceNext = Callable[[str], Awaitable["Resource | None"]]

ListResourceTemplatesNext = Callable[[], Awaitable[Sequence["ResourceTemplate"]]]
GetResourceTemplateNext = Callable[[str], Awaitable["ResourceTemplate | None"]]

ListPromptsNext = Callable[[], Awaitable[Sequence["Prompt"]]]
GetPromptNext = Callable[[str], Awaitable["Prompt | None"]]


class Transform:
    """Base class for component transformations.

    Transforms use a middleware pattern with `call_next` to chain operations.
    Each transform can intercept, modify, or pass through component queries.

    For list operations, call `call_next()` to get components from downstream,
    then transform the result. For get operations, optionally transform the
    name/uri before calling `call_next`, then transform the result.

    Example:
        ```python
        class MyTransform(Transform):
            async def list_tools(self, call_next):
                tools = await call_next()  # Get tools from downstream
                return [transform(t) for t in tools]  # Transform them

            async def get_tool(self, name, call_next):
                original = self.reverse_name(name)  # Map to original name
                tool = await call_next(original)  # Get from downstream
                return transform(tool) if tool else None
        ```
    """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def list_tools(self, call_next: ListToolsNext) -> Sequence[Tool]:
        """List tools with transformation applied.

        Args:
            call_next: Callable to get tools from downstream transforms/provider.

        Returns:
            Transformed sequence of tools.
        """
        return await call_next()

    async def get_tool(self, name: str, call_next: GetToolNext) -> Tool | None:
        """Get a tool by name.

        Args:
            name: The requested tool name (may be transformed).
            call_next: Callable to get tool from downstream.

        Returns:
            The tool if found, None otherwise.
        """
        return await call_next(name)

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def list_resources(self, call_next: ListResourcesNext) -> Sequence[Resource]:
        """List resources with transformation applied.

        Args:
            call_next: Callable to get resources from downstream transforms/provider.

        Returns:
            Transformed sequence of resources.
        """
        return await call_next()

    async def get_resource(
        self, uri: str, call_next: GetResourceNext
    ) -> Resource | None:
        """Get a resource by URI.

        Args:
            uri: The requested resource URI (may be transformed).
            call_next: Callable to get resource from downstream.

        Returns:
            The resource if found, None otherwise.
        """
        return await call_next(uri)

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def list_resource_templates(
        self, call_next: ListResourceTemplatesNext
    ) -> Sequence[ResourceTemplate]:
        """List resource templates with transformation applied.

        Args:
            call_next: Callable to get templates from downstream transforms/provider.

        Returns:
            Transformed sequence of resource templates.
        """
        return await call_next()

    async def get_resource_template(
        self, uri: str, call_next: GetResourceTemplateNext
    ) -> ResourceTemplate | None:
        """Get a resource template by URI.

        Args:
            uri: The requested template URI (may be transformed).
            call_next: Callable to get template from downstream.

        Returns:
            The resource template if found, None otherwise.
        """
        return await call_next(uri)

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def list_prompts(self, call_next: ListPromptsNext) -> Sequence[Prompt]:
        """List prompts with transformation applied.

        Args:
            call_next: Callable to get prompts from downstream transforms/provider.

        Returns:
            Transformed sequence of prompts.
        """
        return await call_next()

    async def get_prompt(self, name: str, call_next: GetPromptNext) -> Prompt | None:
        """Get a prompt by name.

        Args:
            name: The requested prompt name (may be transformed).
            call_next: Callable to get prompt from downstream.

        Returns:
            The prompt if found, None otherwise.
        """
        return await call_next(name)


# Re-export built-in transforms (must be after Transform class to avoid circular imports)
from fastmcp.server.transforms.namespace import Namespace  # noqa: E402
from fastmcp.server.transforms.tool_transform import ToolTransform  # noqa: E402
from fastmcp.server.transforms.visibility import Visibility  # noqa: E402

__all__ = [
    "GetPromptNext",
    "GetResourceNext",
    "GetResourceTemplateNext",
    "GetToolNext",
    "ListPromptsNext",
    "ListResourceTemplatesNext",
    "ListResourcesNext",
    "ListToolsNext",
    "Namespace",
    "ToolTransform",
    "Transform",
    "Visibility",
]
