"""WrappedProvider for immutable transform composition.

This module provides `_WrappedProvider`, an internal class that wraps a provider
with an additional transform. Created by `Provider.wrap_transform()`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from fastmcp.server.providers.base import Provider
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.transforms import Transform
    from fastmcp.tools.tool import Tool
    from fastmcp.utilities.components import FastMCPComponent


class _WrappedProvider(Provider):
    """Internal provider that wraps another provider with a transform.

    Created by Provider.wrap_transform(). Delegates all component sourcing
    to the inner provider's public methods (which apply inner's transforms),
    then applies the wrapper's transform on top.

    This enables immutable transform composition - the inner provider is
    unchanged, and the wrapper adds its transform layer.
    """

    def __init__(self, inner: Provider, transform: Transform) -> None:
        """Initialize wrapped provider.

        Args:
            inner: The provider to wrap.
            transform: The transform to apply on top of inner's results.
        """
        super().__init__()
        self._inner = inner
        # Add the transform to this provider's transform list
        # It will be applied via the normal transform chain
        self._transforms.append(transform)

    def __repr__(self) -> str:
        return f"_WrappedProvider({self._inner!r}, transforms={self._transforms!r})"

    # -------------------------------------------------------------------------
    # Delegate to inner provider's public methods (which apply inner's transforms)
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """Delegate to inner's list_tools (includes inner's transforms)."""
        return await self._inner.list_tools()

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Delegate to inner's get_tool (includes inner's transforms)."""
        return await self._inner.get_tool(name, version)

    async def _list_resources(self) -> Sequence[Resource]:
        """Delegate to inner's list_resources (includes inner's transforms)."""
        return await self._inner.list_resources()

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Delegate to inner's get_resource (includes inner's transforms)."""
        return await self._inner.get_resource(uri, version)

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Delegate to inner's list_resource_templates (includes inner's transforms)."""
        return await self._inner.list_resource_templates()

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Delegate to inner's get_resource_template (includes inner's transforms)."""
        return await self._inner.get_resource_template(uri, version)

    async def _list_prompts(self) -> Sequence[Prompt]:
        """Delegate to inner's list_prompts (includes inner's transforms)."""
        return await self._inner.list_prompts()

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Delegate to inner's get_prompt (includes inner's transforms)."""
        return await self._inner.get_prompt(name, version)

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Delegate to inner's get_tasks and apply wrapper's transforms."""
        # Import here to avoid circular imports
        from fastmcp.prompts.prompt import Prompt
        from fastmcp.resources.resource import Resource
        from fastmcp.resources.template import ResourceTemplate
        from fastmcp.tools.tool import Tool

        # Get tasks from inner (already has inner's transforms)
        components = list(await self._inner.get_tasks())

        # Apply this wrapper's transforms to the components
        # We need to apply transforms per component type
        tools = [c for c in components if isinstance(c, Tool)]
        resources = [c for c in components if isinstance(c, Resource)]
        templates = [c for c in components if isinstance(c, ResourceTemplate)]
        prompts = [c for c in components if isinstance(c, Prompt)]

        async def tools_base() -> Sequence[Tool]:
            return tools

        async def resources_base() -> Sequence[Resource]:
            return resources

        async def templates_base() -> Sequence[ResourceTemplate]:
            return templates

        async def prompts_base() -> Sequence[Prompt]:
            return prompts

        # Apply this wrapper's transforms
        tools_chain = tools_base
        resources_chain = resources_base
        templates_chain = templates_base
        prompts_chain = prompts_base

        for transform in self.transforms:
            tools_chain = partial(transform.list_tools, call_next=tools_chain)
            resources_chain = partial(
                transform.list_resources, call_next=resources_chain
            )
            templates_chain = partial(
                transform.list_resource_templates, call_next=templates_chain
            )
            prompts_chain = partial(transform.list_prompts, call_next=prompts_chain)

        transformed_tools = await tools_chain()
        transformed_resources = await resources_chain()
        transformed_templates = await templates_chain()
        transformed_prompts = await prompts_chain()

        return [
            c
            for c in [
                *transformed_tools,
                *transformed_resources,
                *transformed_templates,
                *transformed_prompts,
            ]
            if c.task_config.supports_tasks()
        ]

    # -------------------------------------------------------------------------
    # Lifecycle - combine with inner
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Combine lifespan with inner provider."""
        async with self._inner.lifespan():
            yield
