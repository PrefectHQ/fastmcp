"""Base class for transforms that need to read the real component catalog.

Some transforms replace ``list_tools()`` output with synthetic components
(e.g. a search interface) while still needing access to the *real*
(auth-filtered) catalog at call time.  ``CatalogTransform`` provides the
bypass machinery so subclasses can call ``get_tool_catalog()`` without
triggering their own replacement logic.

Re-entrancy problem
-------------------

When a synthetic tool handler calls ``get_tool_catalog()``, that calls
``ctx.fastmcp.list_tools()`` which re-enters the transform pipeline —
including *this* transform's ``list_tools()``.  If the subclass overrides
``list_tools()`` directly, the re-entrant call would hit the subclass's
replacement logic again (returning synthetic tools instead of the real
catalog).  A ``super()`` call can't prevent this because Python can't
short-circuit a method after ``super()`` returns.

Solution: ``CatalogTransform`` owns ``list_tools()`` and uses a
per-instance ``ContextVar`` to detect re-entrant calls.  During bypass,
it passes through to the base ``Transform.list_tools()`` (a no-op).
Otherwise, it delegates to ``transform_tools()`` — the subclass hook
where replacement logic lives.  Same pattern for resources, prompts,
and resource templates.

This is *not* the same as the ``Provider._list_tools()`` convention
(which produces raw components with no arguments).  ``transform_tools()``
receives the current catalog and returns a transformed version.  The
distinct name avoids confusion between the two patterns.

Usage::

    class MyTransform(CatalogTransform):
        async def transform_tools(self, tools):
            return [self._make_search_tool()]

        def _make_search_tool(self):
            async def search(ctx: Context = None):
                real_tools = await self.get_tool_catalog(ctx)
                ...
            return Tool.from_function(fn=search, name="search")
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastmcp.server.transforms import Transform

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.context import Context
    from fastmcp.tools.tool import Tool

_instance_counter = itertools.count()


class CatalogTransform(Transform):
    """Transform that needs access to the real component catalog.

    Subclasses override ``transform_tools()`` / ``transform_resources()``
    / ``transform_prompts()`` / ``transform_resource_templates()``
    instead of the ``list_*()`` methods.  The base class owns
    ``list_*()`` and handles re-entrant bypass automatically — subclasses
    never see re-entrant calls from ``get_*_catalog()``.

    The ``get_*_catalog()`` methods fetch the real (auth-filtered) catalog
    by temporarily setting a bypass flag so that this transform's
    ``list_*()`` passes through without calling the subclass hook.
    """

    def __init__(self) -> None:
        self._instance_id: int = next(_instance_counter)
        self._bypass: ContextVar[bool] = ContextVar(
            f"_catalog_bypass_{self._instance_id}", default=False
        )

    # ------------------------------------------------------------------
    # list_* (bypass-aware — subclasses override transform_* instead)
    # ------------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if self._bypass.get():
            return await super().list_tools(tools)
        return await self.transform_tools(tools)

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        if self._bypass.get():
            return await super().list_resources(resources)
        return await self.transform_resources(resources)

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        if self._bypass.get():
            return await super().list_resource_templates(templates)
        return await self.transform_resource_templates(templates)

    async def list_prompts(self, prompts: Sequence[Prompt]) -> Sequence[Prompt]:
        if self._bypass.get():
            return await super().list_prompts(prompts)
        return await self.transform_prompts(prompts)

    # ------------------------------------------------------------------
    # Subclass hooks (override these, not list_*)
    # ------------------------------------------------------------------

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Transform the tool catalog.

        Override this method to replace, filter, or augment the tool listing.
        The default implementation passes through unchanged.

        Do NOT override ``list_tools()`` directly — the base class uses it
        to handle re-entrant bypass when ``get_tool_catalog()`` reads the
        real catalog.
        """
        return tools

    async def transform_resources(
        self, resources: Sequence[Resource]
    ) -> Sequence[Resource]:
        """Transform the resource catalog.

        Override this method to replace, filter, or augment the resource listing.
        The default implementation passes through unchanged.

        Do NOT override ``list_resources()`` directly — the base class uses it
        to handle re-entrant bypass when ``get_resource_catalog()`` reads the
        real catalog.
        """
        return resources

    async def transform_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        """Transform the resource template catalog.

        Override this method to replace, filter, or augment the template listing.
        The default implementation passes through unchanged.

        Do NOT override ``list_resource_templates()`` directly — the base class
        uses it to handle re-entrant bypass when
        ``get_resource_template_catalog()`` reads the real catalog.
        """
        return templates

    async def transform_prompts(self, prompts: Sequence[Prompt]) -> Sequence[Prompt]:
        """Transform the prompt catalog.

        Override this method to replace, filter, or augment the prompt listing.
        The default implementation passes through unchanged.

        Do NOT override ``list_prompts()`` directly — the base class uses it
        to handle re-entrant bypass when ``get_prompt_catalog()`` reads the
        real catalog.
        """
        return prompts

    # ------------------------------------------------------------------
    # Catalog accessors
    # ------------------------------------------------------------------

    async def get_tool_catalog(
        self, ctx: Context, *, run_middleware: bool = True
    ) -> Sequence[Tool]:
        """Fetch the real tool catalog, bypassing this transform.

        Args:
            ctx: The current request context.
            run_middleware: Whether to run middleware on the inner call.
                Defaults to True because this is typically called from a
                tool handler where list_tools middleware has not yet run.
        """
        token = self._bypass.set(True)
        try:
            return await ctx.fastmcp.list_tools(run_middleware=run_middleware)
        finally:
            self._bypass.reset(token)

    async def get_resource_catalog(
        self, ctx: Context, *, run_middleware: bool = True
    ) -> Sequence[Resource]:
        """Fetch the real resource catalog, bypassing this transform.

        Args:
            ctx: The current request context.
            run_middleware: Whether to run middleware on the inner call.
                Defaults to True because this is typically called from a
                tool handler where list_resources middleware has not yet run.
        """
        token = self._bypass.set(True)
        try:
            return await ctx.fastmcp.list_resources(run_middleware=run_middleware)
        finally:
            self._bypass.reset(token)

    async def get_prompt_catalog(
        self, ctx: Context, *, run_middleware: bool = True
    ) -> Sequence[Prompt]:
        """Fetch the real prompt catalog, bypassing this transform.

        Args:
            ctx: The current request context.
            run_middleware: Whether to run middleware on the inner call.
                Defaults to True because this is typically called from a
                tool handler where list_prompts middleware has not yet run.
        """
        token = self._bypass.set(True)
        try:
            return await ctx.fastmcp.list_prompts(run_middleware=run_middleware)
        finally:
            self._bypass.reset(token)

    async def get_resource_template_catalog(
        self, ctx: Context, *, run_middleware: bool = True
    ) -> Sequence[ResourceTemplate]:
        """Fetch the real resource template catalog, bypassing this transform.

        Args:
            ctx: The current request context.
            run_middleware: Whether to run middleware on the inner call.
                Defaults to True because this is typically called from a
                tool handler where list_resource_templates middleware has
                not yet run.
        """
        token = self._bypass.set(True)
        try:
            return await ctx.fastmcp.list_resource_templates(
                run_middleware=run_middleware
            )
        finally:
            self._bypass.reset(token)
