"""AggregateProvider for combining multiple providers into one.

This module provides `AggregateProvider`, a utility class that presents
multiple providers as a single unified provider. Useful when you want to
combine custom providers without creating a full FastMCP server.

Example:
    ```python
    from fastmcp.server.providers import AggregateProvider

    # Combine multiple providers into one
    combined = AggregateProvider()
    combined.add_provider(provider1)
    combined.add_provider(provider2, namespace="api")  # Tools become "api_foo"

    # Use like any other provider
    tools = await combined.list_tools()
    ```
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, TypeVar

from fastmcp.exceptions import NotFoundError
from fastmcp.server.providers.base import Provider
from fastmcp.server.transforms import Namespace
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.versions import VersionSpec, version_sort_key

if TYPE_CHECKING:
    from fastmcp.prompts.base import Prompt
    from fastmcp.resources.base import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.base import Tool

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AggregateProvider(Provider):
    """Utility provider that combines multiple providers into one.

    Components are aggregated from all providers. For get_* operations,
    providers are queried in parallel and the highest version is returned.

    When adding providers with a namespace, wrap_transform() is used to apply
    the Namespace transform. This means namespace transformation is handled
    by the wrapped provider, not by AggregateProvider.

    Errors from individual providers are logged and skipped (graceful degradation).

    Example:
        ```python
        combined = AggregateProvider()
        combined.add_provider(db_provider)
        combined.add_provider(api_provider, namespace="api")
        # db_provider's tools keep original names
        # api_provider's tools become "api_foo", "api_bar", etc.
        ```
    """

    def __init__(self, providers: Sequence[Provider] | None = None) -> None:
        """Initialize with an optional sequence of providers.

        Args:
            providers: Optional initial providers (without namespacing).
                For namespaced providers, use add_provider() instead.
        """
        super().__init__()
        self.providers: list[Provider] = list(providers or [])
        self._on_duplicate: str = "warn"

    def add_provider(self, provider: Provider, *, namespace: str = "") -> None:
        """Add a provider with optional namespace.

        If the provider is a FastMCP server, it's automatically wrapped in
        FastMCPProvider to ensure middleware is invoked correctly.

        Args:
            provider: The provider to add.
            namespace: Optional namespace prefix. When set:
                - Tools become "namespace_toolname"
                - Resources become "protocol://namespace/path"
                - Prompts become "namespace_promptname"
        """
        # Import here to avoid circular imports
        from fastmcp.server.server import FastMCP

        # Auto-wrap FastMCP servers to ensure middleware is invoked
        if isinstance(provider, FastMCP):
            from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

            provider = FastMCPProvider(provider)

        # Apply namespace via wrap_transform if specified
        if namespace:
            provider = provider.wrap_transform(Namespace(namespace))

        self.providers.append(provider)

    def _collect_list_results(
        self, results: list[Sequence[T] | BaseException], operation: str
    ) -> list[T]:
        """Collect successful list results, logging any exceptions.

        Detects duplicate component names across providers and applies
        the on_duplicate behavior (error/warn/replace/ignore).
        """
        collected: list[T] = []
        # Track (name, provider_index) to allow version variants from same provider
        seen_names: dict[str, int] = {}  # name -> provider index of first occurrence
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"Error during {operation} from provider "
                    f"{self.providers[i]}: {result}"
                )
                continue
            for item in result:
                # Extract identity key: name, uri, or uri_template
                name = (
                    getattr(item, "name", None)
                    or getattr(item, "uri", None)
                    or getattr(item, "uri_template", None)
                )
                if name is not None:
                    name = str(name)
                    if name in seen_names and seen_names[name] != i:
                        # Duplicate from a DIFFERENT provider — flag it
                        msg = (
                            f"Duplicate {operation} component '{name}' "
                            f"from provider {self.providers[i]} "
                            f"(first seen from provider "
                            f"{self.providers[seen_names[name]]})"
                        )
                        if self._on_duplicate == "error":
                            raise ValueError(msg)
                        elif self._on_duplicate == "warn":
                            logger.warning(msg)
                    seen_names.setdefault(name, i)
                collected.append(item)
        return collected

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
                    logger.warning(
                        f"Error during {operation} from provider "
                        f"{self.providers[i]}: {result}"
                    )
                continue
            if result is not None:
                valid.append(result)
        if not valid:
            return None
        return max(valid, key=version_sort_key)

    def __repr__(self) -> str:
        return f"AggregateProvider(providers={self.providers!r})"

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        """List all tools from all providers."""
        results = await gather(
            *[p.list_tools() for p in self.providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_tools")

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by name from providers."""
        results = await gather(
            *[p.get_tool(name, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_tool({name!r})")  # type: ignore[return-value]  # ty:ignore[invalid-argument-type, invalid-return-type]

    async def get_app_tool(self, app_name: str, tool_name: str) -> Tool | None:
        """Query all child providers for an app tool."""
        results = await gather(
            *[p.get_app_tool(app_name, tool_name) for p in self.providers],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                continue
            if r is not None:
                return r
        return None

    async def get_tool_by_hash(self, tool_hash: str, tool_name: str) -> Tool | None:
        """Query all child providers for a tool matching a hash."""
        results = await gather(
            *[p.get_tool_by_hash(tool_hash, tool_name) for p in self.providers],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                continue
            if r is not None:
                return r
        return None

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List all resources from all providers."""
        results = await gather(
            *[p.list_resources() for p in self.providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resources")

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource by URI from providers."""
        results = await gather(
            *[p.get_resource(uri, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_resource({uri!r})")  # type: ignore[return-value]  # ty:ignore[invalid-argument-type, invalid-return-type]

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List all resource templates from all providers."""
        results = await gather(
            *[p.list_resource_templates() for p in self.providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resource_templates")

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get resource template by URI from providers."""
        results = await gather(
            *[p.get_resource_template(uri, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(
            list(results), f"get_resource_template({uri!r})"
        )  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def _list_prompts(self) -> Sequence[Prompt]:
        """List all prompts from all providers."""
        results = await gather(
            *[p.list_prompts() for p in self.providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_prompts")

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by name from providers."""
        results = await gather(
            *[p.get_prompt(name, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_prompt({name!r})")  # type: ignore[return-value]  # ty:ignore[invalid-argument-type, invalid-return-type]

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Get all task-eligible components from all providers."""
        results = await gather(
            *[p.get_tasks() for p in self.providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "get_tasks")

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Combine lifespans of all providers."""
        async with AsyncExitStack() as stack:
            for p in self.providers:
                await stack.enter_async_context(p.lifespan())
            yield
