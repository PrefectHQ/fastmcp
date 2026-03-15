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
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _provider_matches_scope(provider: Provider, scope: list[Provider]) -> bool:
    """Check if a provider matches any provider in the scope list.

    Handles wrapped providers by walking the inner chain — a
    WrappedProvider(Namespace, inner=MyProvider) matches if MyProvider
    is in the scope list.
    """
    from fastmcp.server.providers.wrapped_provider import _WrappedProvider

    if provider in scope:
        return True
    if isinstance(provider, _WrappedProvider):
        return _provider_matches_scope(provider._inner, scope)
    return False


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
        """Collect successful list results, logging any exceptions."""
        collected: list[T] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.debug(
                    f"Error during {operation} from provider "
                    f"{self.providers[i]}: {result}"
                )
                continue
            collected.extend(result)
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
                    logger.debug(
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

    def _providers_for_scope(self, scope: list[Provider] | None) -> list[Provider]:
        """Return providers filtered by scope.

        If scope is None or this aggregate itself is in the scope, returns
        all providers. Otherwise, returns only providers that match the
        scope — either directly or through wrapping (e.g., a
        WrappedProvider whose inner matches).
        """
        if scope is None or self in scope:
            return self.providers
        return [p for p in self.providers if _provider_matches_scope(p, scope)]

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
        return self._get_highest_version_result(results, f"get_tool({name!r})")  # type: ignore[return-value]

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

    async def list_resources(
        self, *, _scope: list[Provider] | None = None
    ) -> Sequence[Resource]:
        """List resources, optionally scoped to specific providers."""
        providers = self._providers_for_scope(_scope)
        results = await gather(
            *[p.list_resources() for p in providers],
            return_exceptions=True,
        )
        resources: Sequence[Resource] = self._collect_list_results(
            results, "list_resources"
        )
        for transform in self.transforms:
            resources = await transform.list_resources(resources)
        return resources

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource by URI from providers."""
        results = await gather(
            *[p.get_resource(uri, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_resource({uri!r})")  # type: ignore[return-value]

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

    async def list_resource_templates(
        self, *, _scope: list[Provider] | None = None
    ) -> Sequence[ResourceTemplate]:
        """List resource templates, optionally scoped to specific providers."""
        providers = self._providers_for_scope(_scope)
        results = await gather(
            *[p.list_resource_templates() for p in providers],
            return_exceptions=True,
        )
        templates: Sequence[ResourceTemplate] = self._collect_list_results(
            results, "list_resource_templates"
        )
        for transform in self.transforms:
            templates = await transform.list_resource_templates(templates)
        return templates

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
        )  # type: ignore[return-value]

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

    async def list_prompts(
        self, *, _scope: list[Provider] | None = None
    ) -> Sequence[Prompt]:
        """List prompts, optionally scoped to specific providers."""
        providers = self._providers_for_scope(_scope)
        results = await gather(
            *[p.list_prompts() for p in providers],
            return_exceptions=True,
        )
        prompts: Sequence[Prompt] = self._collect_list_results(results, "list_prompts")
        for transform in self.transforms:
            prompts = await transform.list_prompts(prompts)
        return prompts

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by name from providers."""
        results = await gather(
            *[p.get_prompt(name, version) for p in self.providers],
            return_exceptions=True,
        )
        return self._get_highest_version_result(results, f"get_prompt({name!r})")  # type: ignore[return-value]

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
