"""AggregateProvider for combining multiple providers into one.

This module provides `AggregateProvider` which presents multiple providers
as a single unified provider. Used internally by FastMCP for aggregating
components from all providers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypeVar

from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.tools.tool import Tool
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.components import FastMCPComponent

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AggregateProvider(Provider):
    """Presents multiple providers as a single provider.

    Components are aggregated from all providers. For get_* operations,
    providers are queried in parallel and the first non-None result is returned.

    Errors from individual providers are logged and skipped (graceful degradation).
    This matches the behavior of FastMCP's original provider iteration.
    """

    def __init__(self, providers: Sequence[Provider]) -> None:
        """Initialize with a sequence of providers.

        Args:
            providers: The providers to aggregate. Queried in order for lookups.
        """
        super().__init__()
        self._providers = list(providers)

    def _collect_list_results(
        self, results: list[Sequence[T] | BaseException], operation: str
    ) -> list[T]:
        """Collect successful list results, logging any exceptions."""
        collected: list[T] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.debug(
                    f"Error during {operation} from provider "
                    f"{self._providers[i]}: {result}"
                )
                continue
            collected.extend(result)
        return collected

    def _get_first_result(
        self, results: list[T | None | BaseException], operation: str
    ) -> T | None:
        """Get first successful non-None result, logging non-NotFoundError exceptions."""
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                # NotFoundError is expected - don't log it
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error during {operation} from provider "
                        f"{self._providers[i]}: {result}"
                    )
                continue
            if result is not None:
                return result
        return None

    def __repr__(self) -> str:
        return f"AggregateProvider(providers={self._providers!r})"

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """List all tools from all providers (with transforms applied)."""
        results = await gather(
            *[p._list_tools() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_tools")

    async def get_tool(self, name: str) -> Tool | None:
        """Get tool by name from first provider that has it (with transforms applied)."""
        results = await gather(
            *[p._get_tool(name) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_first_result(results, f"get_tool({name!r})")

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def list_resources(self) -> Sequence[Resource]:
        """List all resources from all providers (with transforms applied)."""
        results = await gather(
            *[p._list_resources() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resources")

    async def get_resource(self, uri: str) -> Resource | None:
        """Get resource by URI from first provider that has it (with transforms applied)."""
        results = await gather(
            *[p._get_resource(uri) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_first_result(results, f"get_resource({uri!r})")

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from all providers (with transforms applied)."""
        results = await gather(
            *[p._list_resource_templates() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resource_templates")

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get resource template by URI from first provider that has it (with transforms applied)."""
        results = await gather(
            *[p._get_resource_template(uri) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_first_result(results, f"get_resource_template({uri!r})")

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from all providers (with transforms applied)."""
        results = await gather(
            *[p._list_prompts() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_prompts")

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get prompt by name from first provider that has it (with transforms applied)."""
        results = await gather(
            *[p._get_prompt(name) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_first_result(results, f"get_prompt({name!r})")

    # -------------------------------------------------------------------------
    # Components
    # -------------------------------------------------------------------------

    async def get_component(
        self, key: str
    ) -> Tool | Resource | ResourceTemplate | Prompt | None:
        """Get component by key from first provider that has it (with transforms applied).

        Parses the key prefix and delegates to the appropriate get_* method
        so that transforms are applied correctly.
        """
        # Parse key prefix to route to correct method
        if key.startswith("tool:"):
            return await self.get_tool(key[5:])
        elif key.startswith("resource:"):
            return await self.get_resource(key[9:])
        elif key.startswith("template:"):
            return await self.get_resource_template(key[9:])
        elif key.startswith("prompt:"):
            return await self.get_prompt(key[7:])
        return None

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Get all task-eligible components from all providers."""
        results = await gather(
            *[p.get_tasks() for p in self._providers], return_exceptions=True
        )
        return self._collect_list_results(results, "get_tasks")

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Combine lifespans of all providers."""
        async with AsyncExitStack() as stack:
            for provider in self._providers:
                await stack.enter_async_context(provider.lifespan())
            yield
