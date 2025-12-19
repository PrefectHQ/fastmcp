"""TransformingProvider for applying component transformations.

This module provides the `TransformingProvider` class that wraps any Provider
and applies transformations like namespace prefixes and tool renames.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceContent
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider, TaskComponents
from fastmcp.tools.tool import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import FunctionPrompt
    from fastmcp.resources.resource import FunctionResource
    from fastmcp.resources.template import FunctionResourceTemplate
    from fastmcp.tools.tool import FunctionTool


# Pattern for matching URIs: protocol://path
_URI_PATTERN = re.compile(r"^([^:]+://)(.*?)$")


class TransformingProvider(Provider):
    """Wraps any provider and applies component transformations.

    Users typically use `provider.with_transforms()` rather than instantiating
    this class directly. Multiple `.with_transforms()` calls stack - each
    creates a new wrapper that composes with the previous.

    Transformation rules:
        - Tools/prompts with explicit renames use the rename (bypasses namespace)
        - Tools/prompts without renames get namespace prefix: "namespace_name"
        - Resources get path-style namespace: "protocol://namespace/path"

    Example:
        ```python
        # Via with_transforms() method (preferred)
        provider = SomeProvider().with_transforms(
            namespace="api",
            tool_renames={"verbose_tool_name": "short"}
        )

        # Stacking composes transformations:
        provider = (
            SomeProvider()
            .with_transforms(namespace="api")
            .with_transforms(tool_renames={"api_foo": "bar"})
        )
        # "foo" → "api_foo" (inner) → "bar" (outer)
        ```
    """

    def __init__(
        self,
        provider: Provider,
        *,
        namespace: str | None = None,
        tool_renames: dict[str, str] | None = None,
    ):
        """Initialize a TransformingProvider.

        Args:
            provider: The provider to wrap.
            namespace: Prefix for tools/prompts, path segment for resources.
            tool_renames: Map of original_name → final_name. Tools in this map
                use the specified name instead of namespace prefixing.
        """
        super().__init__()
        self._wrapped = provider
        self.namespace = namespace
        self.tool_renames = tool_renames or {}

        # Validate that renames are reversible (no duplicate target names)
        if len(self.tool_renames) != len(set(self.tool_renames.values())):
            seen: dict[str, str] = {}
            for orig, renamed in self.tool_renames.items():
                if renamed in seen:
                    raise ValueError(
                        f"tool_renames has duplicate target name {renamed!r}: "
                        f"both {seen[renamed]!r} and {orig!r} map to it"
                    )
                seen[renamed] = orig

        self._tool_renames_reverse = {v: k for k, v in self.tool_renames.items()}

    # -------------------------------------------------------------------------
    # Tool name transformation
    # -------------------------------------------------------------------------

    def _transform_tool_name(self, name: str) -> str:
        """Apply transformation to tool name."""
        # Explicit rename takes precedence (bypasses namespace)
        if name in self.tool_renames:
            return self.tool_renames[name]
        # Otherwise apply namespace
        if self.namespace:
            return f"{self.namespace}_{name}"
        return name

    def _reverse_tool_name(self, name: str) -> str | None:
        """Reverse tool name transformation, or None if no match."""
        # Check explicit renames first
        if name in self._tool_renames_reverse:
            return self._tool_renames_reverse[name]
        # Check namespace prefix
        if self.namespace:
            prefix = f"{self.namespace}_"
            if name.startswith(prefix):
                return name[len(prefix) :]
            return None
        return name

    # -------------------------------------------------------------------------
    # Prompt name transformation
    # -------------------------------------------------------------------------

    def _transform_prompt_name(self, name: str) -> str:
        """Apply transformation to prompt name."""
        if self.namespace:
            return f"{self.namespace}_{name}"
        return name

    def _reverse_prompt_name(self, name: str) -> str | None:
        """Reverse prompt name transformation, or None if no match."""
        if self.namespace:
            prefix = f"{self.namespace}_"
            if name.startswith(prefix):
                return name[len(prefix) :]
            return None
        return name

    # -------------------------------------------------------------------------
    # Resource URI transformation
    # -------------------------------------------------------------------------

    def _transform_resource_uri(self, uri: str) -> str:
        """Apply transformation to resource URI."""
        if not self.namespace:
            return uri
        match = _URI_PATTERN.match(uri)
        if match:
            protocol, path = match.groups()
            return f"{protocol}{self.namespace}/{path}"
        return uri

    def _reverse_resource_uri(self, uri: str) -> str | None:
        """Reverse resource URI transformation, or None if no match."""
        if not self.namespace:
            return uri
        match = _URI_PATTERN.match(uri)
        if match:
            protocol, path = match.groups()
            prefix = f"{self.namespace}/"
            if path.startswith(prefix):
                return f"{protocol}{path[len(prefix) :]}"
            return None
        return None

    # -------------------------------------------------------------------------
    # Tool methods
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """List tools with transformations applied."""
        tools = await self._wrapped.list_tools()
        return [
            t.model_copy(update={"name": self._transform_tool_name(t.name)})
            for t in tools
        ]

    async def get_tool(self, name: str) -> Tool | None:
        """Get tool by transformed name."""
        original = self._reverse_tool_name(name)
        if original is None:
            return None
        tool = await self._wrapped.get_tool(original)
        if tool:
            return tool.model_copy(update={"name": name})
        return None

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolResult | None:
        """Call tool by transformed name."""
        original = self._reverse_tool_name(name)
        if original is None:
            return None
        return await self._wrapped.call_tool(original, arguments)

    # -------------------------------------------------------------------------
    # Resource methods
    # -------------------------------------------------------------------------

    async def list_resources(self) -> Sequence[Resource]:
        """List resources with URI transformations applied."""
        resources = await self._wrapped.list_resources()
        return [
            r.model_copy(update={"uri": self._transform_resource_uri(str(r.uri))})
            for r in resources
        ]

    async def get_resource(self, uri: str) -> Resource | None:
        """Get resource by transformed URI."""
        original = self._reverse_resource_uri(uri)
        if original is None:
            return None
        resource = await self._wrapped.get_resource(original)
        if resource:
            return resource.model_copy(update={"uri": uri})
        return None

    async def read_resource(self, uri: str) -> ResourceContent | None:
        """Read resource by transformed URI."""
        original = self._reverse_resource_uri(uri)
        if original is None:
            return None
        return await self._wrapped.read_resource(original)

    # -------------------------------------------------------------------------
    # Resource template methods
    # -------------------------------------------------------------------------

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List resource templates with URI transformations applied."""
        templates = await self._wrapped.list_resource_templates()
        return [
            t.model_copy(
                update={"uri_template": self._transform_resource_uri(t.uri_template)}
            )
            for t in templates
        ]

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get resource template by transformed URI."""
        original = self._reverse_resource_uri(uri)
        if original is None:
            return None
        template = await self._wrapped.get_resource_template(original)
        if template:
            return template.model_copy(
                update={
                    "uri_template": self._transform_resource_uri(template.uri_template)
                }
            )
        return None

    async def read_resource_template(self, uri: str) -> ResourceContent | None:
        """Read resource template by transformed URI."""
        original = self._reverse_resource_uri(uri)
        if original is None:
            return None
        return await self._wrapped.read_resource_template(original)

    # -------------------------------------------------------------------------
    # Prompt methods
    # -------------------------------------------------------------------------

    async def list_prompts(self) -> Sequence[Prompt]:
        """List prompts with transformations applied."""
        prompts = await self._wrapped.list_prompts()
        return [
            p.model_copy(update={"name": self._transform_prompt_name(p.name)})
            for p in prompts
        ]

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get prompt by transformed name."""
        original = self._reverse_prompt_name(name)
        if original is None:
            return None
        prompt = await self._wrapped.get_prompt(original)
        if prompt:
            return prompt.model_copy(update={"name": name})
        return None

    async def render_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> PromptResult | None:
        """Render prompt by transformed name."""
        original = self._reverse_prompt_name(name)
        if original is None:
            return None
        return await self._wrapped.render_prompt(original, arguments)

    # -------------------------------------------------------------------------
    # Task registration
    # -------------------------------------------------------------------------

    async def get_tasks(self) -> TaskComponents:
        """Get tasks with transformations applied to all components."""

        tasks = await self._wrapped.get_tasks()

        # Apply transforms to tools
        transformed_tools: list[FunctionTool] = []
        for t in tasks.tools:
            transformed_tools.append(
                t.model_copy(update={"name": self._transform_tool_name(t.name)})  # type: ignore[arg-type]
            )

        # Apply transforms to resources
        transformed_resources: list[FunctionResource] = []
        for r in tasks.resources:
            transformed_resources.append(
                r.model_copy(update={"uri": self._transform_resource_uri(str(r.uri))})  # type: ignore[arg-type]
            )

        # Apply transforms to templates
        transformed_templates: list[FunctionResourceTemplate] = []
        for t in tasks.templates:
            transformed_templates.append(
                t.model_copy(
                    update={
                        "uri_template": self._transform_resource_uri(t.uri_template)
                    }
                )  # type: ignore[arg-type]
            )

        # Apply transforms to prompts
        transformed_prompts: list[FunctionPrompt] = []
        for p in tasks.prompts:
            transformed_prompts.append(
                p.model_copy(update={"name": self._transform_prompt_name(p.name)})  # type: ignore[arg-type]
            )

        return TaskComponents(
            tools=transformed_tools,
            resources=transformed_resources,
            templates=transformed_templates,
            prompts=transformed_prompts,
        )

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Delegate lifespan to wrapped provider."""
        async with self._wrapped.lifespan():
            yield
