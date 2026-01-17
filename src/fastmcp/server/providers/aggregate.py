"""AggregateProvider for combining multiple providers into one.

This module provides `AggregateProvider`, a utility class that presents
multiple providers as a single unified provider. Useful when you want to
combine custom providers without creating a full FastMCP server.

Example:
    ```python
    from fastmcp.server.providers import AggregateProvider

    # Combine multiple providers into one
    combined = AggregateProvider([provider1, provider2, provider3])

    # Use like any other provider
    tools = await combined.list_tools()
    ```
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
from fastmcp.utilities.versions import VersionSpec, version_sort_key

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AggregateProvider(Provider):
    """Utility provider that combines multiple providers into one.

    Components are aggregated from all providers. For get_* operations,
    providers are queried in parallel and the highest version is returned.

    Errors from individual providers are logged and skipped (graceful degradation).

    This is useful when you want to combine custom providers without creating
    a full FastMCP server.
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

    def _get_highest_version_result(
        self,
        results: list[FastMCPComponent | None | BaseException],
        operation: str,
    ) -> FastMCPComponent | None:
        """Get the highest version from successful non-None results.

        Used for versioned components where we want the highest version
        across all providers rather than the first match.
        """
        valid: list[FastMCPComponent] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error during {operation} from provider "
                        f"{self._providers[i]}: {result}"
                    )
                continue
            if result is not None:
                valid.append(result)
        if not valid:
            return None
        return max(valid, key=version_sort_key)

    def __repr__(self) -> str:
        return f"AggregateProvider(providers={self._providers!r})"

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """List all tools from all providers (with transforms applied)."""
        results = await gather(
            *[p.list_tools() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_tools")

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by name.

        Args:
            name: The tool name.
            version: If None, returns highest version across all providers.
                If specified, returns highest version matching the spec from any provider.
        """
        results = await gather(
            *[p.get_tool(name, version) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_tool({name!r})")  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List all resources from all providers (with transforms applied)."""
        results = await gather(
            *[p.list_resources() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resources")

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource by URI.

        Args:
            uri: The resource URI.
            version: If None, returns highest version across all providers.
                If specified, returns highest version matching the spec from any provider.
        """
        results = await gather(
            *[p.get_resource(uri, version) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_resource({uri!r})")  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from all providers (with transforms applied)."""
        results = await gather(
            *[p.list_resource_templates() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resource_templates")

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get resource template by URI.

        Args:
            uri: The template URI to match.
            version: If None, returns highest version across all providers.
                If specified, returns highest version matching the spec from any provider.
        """
        results = await gather(
            *[p.get_resource_template(uri, version) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(
            results, f"get_resource_template({uri!r})"
        )  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def _list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from all providers (with transforms applied)."""
        results = await gather(
            *[p.list_prompts() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_prompts")

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by name.

        Args:
            name: The prompt name.
            version: If None, returns highest version across all providers.
                If specified, returns highest version matching the spec from any provider.
        """
        results = await gather(
            *[p.get_prompt(name, version) for p in self._providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_prompt({name!r})")  # type: ignore[return-value]

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
