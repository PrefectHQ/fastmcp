"""LocalProvider for locally-defined MCP components.

This module provides the `LocalProvider` class that manages tools, resources,
templates, and prompts registered via decorators or direct methods.

LocalProvider can be used standalone and attached to multiple servers:

```python
from fastmcp.server.providers import LocalProvider

# Create a reusable provider with tools
provider = LocalProvider()

@provider.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

# Attach to any server
from fastmcp import FastMCP
server1 = FastMCP("Server1", providers=[provider])
server2 = FastMCP("Server2", providers=[provider])
```
"""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

import mcp.types
from mcp.types import Annotations, AnyFunction, ToolAnnotations

import fastmcp
from fastmcp.prompts.prompt import FunctionPrompt, Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import FunctionTool, Tool
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
    apply_transformations_to_tools,
)
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from fastmcp.tools.tool import ToolResultSerializerType

logger = get_logger(__name__)

DuplicateBehavior = Literal["error", "warn", "replace", "ignore"]

_C = TypeVar("_C", bound=FastMCPComponent)


class LocalProvider(Provider):
    """Provider for locally-defined components.

    Supports decorator-based registration (`@provider.tool`, `@provider.resource`,
    `@provider.prompt`) and direct object registration methods.

    When used standalone, LocalProvider uses default settings. When attached
    to a FastMCP server via the server's decorators, server-level settings
    like `_tool_serializer` and `_support_tasks_by_default` are injected.

    Example:
        ```python
        from fastmcp.server.providers import LocalProvider

        # Standalone usage
        provider = LocalProvider()

        @provider.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        @provider.resource("data://config")
        def get_config() -> str:
            return '{"setting": "value"}'

        @provider.prompt
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        # Attach to server(s)
        from fastmcp import FastMCP
        server = FastMCP("MyServer", providers=[provider])
        ```
    """

    def __init__(
        self,
        on_duplicate: DuplicateBehavior = "error",
    ) -> None:
        """Initialize a LocalProvider with empty storage.

        Args:
            on_duplicate: Behavior when adding a component that already exists:
                - "error": Raise ValueError
                - "warn": Log warning and replace
                - "replace": Silently replace
                - "ignore": Keep existing, return it
        """
        super().__init__()
        self._on_duplicate = on_duplicate
        # Unified component storage - keyed by prefixed key (e.g., "tool:name", "resource:uri")
        self._components: dict[str, FastMCPComponent] = {}
        self._tool_transformations: dict[str, ToolTransformConfig] = {}

    def _send_list_changed_notification(self, component: FastMCPComponent) -> None:
        """Send a list changed notification for the component type."""
        from fastmcp.server.context import _current_context

        context = _current_context.get()
        if context is None:
            return

        if isinstance(component, Tool):
            context.send_notification_sync(mcp.types.ToolListChangedNotification())
        elif isinstance(component, (Resource, ResourceTemplate)):
            context.send_notification_sync(mcp.types.ResourceListChangedNotification())
        elif isinstance(component, Prompt):
            context.send_notification_sync(mcp.types.PromptListChangedNotification())

    # =========================================================================
    # Storage methods
    # =========================================================================

    def _add_component(self, component: _C) -> _C:
        """Add a component to unified storage.

        Args:
            component: The component to add.

        Returns:
            The component that was added (or existing if on_duplicate="ignore").
        """
        existing = self._components.get(component.key)
        if existing:
            if self._on_duplicate == "error":
                raise ValueError(f"Component already exists: {component.key}")
            elif self._on_duplicate == "warn":
                logger.warning(f"Component already exists: {component.key}")
            elif self._on_duplicate == "ignore":
                return existing  # type: ignore[return-value]
            # "replace" and "warn" fall through to add

        self._components[component.key] = component
        self._send_list_changed_notification(component)
        return component

    def _remove_component(self, key: str) -> None:
        """Remove a component from unified storage.

        Args:
            key: The prefixed key of the component.

        Raises:
            KeyError: If the component is not found.
        """
        component = self._components.get(key)
        if component is None:
            raise KeyError(f"Component {key!r} not found")

        del self._components[key]
        self._send_list_changed_notification(component)

    def _get_component(self, key: str) -> FastMCPComponent | None:
        """Get a component by its prefixed key.

        Args:
            key: The prefixed key (e.g., "tool:name", "resource:uri").

        Returns:
            The component, or None if not found.
        """
        return self._components.get(key)

    def add_tool(self, tool: Tool) -> Tool:
        """Add a tool to this provider's storage."""
        return self._add_component(tool)

    def remove_tool(self, name: str) -> None:
        """Remove a tool from this provider's storage."""
        self._remove_component(Tool.make_key(name))

    def add_resource(self, resource: Resource) -> Resource:
        """Add a resource to this provider's storage."""
        return self._add_component(resource)

    def remove_resource(self, uri: str) -> None:
        """Remove a resource from this provider's storage."""
        self._remove_component(Resource.make_key(uri))

    def add_template(self, template: ResourceTemplate) -> ResourceTemplate:
        """Add a resource template to this provider's storage."""
        return self._add_component(template)

    def remove_template(self, uri_template: str) -> None:
        """Remove a resource template from this provider's storage."""
        self._remove_component(ResourceTemplate.make_key(uri_template))

    def add_prompt(self, prompt: Prompt) -> Prompt:
        """Add a prompt to this provider's storage."""
        return self._add_component(prompt)

    def remove_prompt(self, name: str) -> None:
        """Remove a prompt from this provider's storage."""
        self._remove_component(Prompt.make_key(name))

    # =========================================================================
    # Tool transformation methods
    # =========================================================================

    def add_tool_transformation(
        self, tool_name: str, transformation: ToolTransformConfig
    ) -> None:
        """Add a tool transformation.

        Args:
            tool_name: The name of the tool to transform.
            transformation: The transformation configuration.
        """
        self._tool_transformations[tool_name] = transformation

    def get_tool_transformation(self, tool_name: str) -> ToolTransformConfig | None:
        """Get a tool transformation.

        Args:
            tool_name: The name of the tool.

        Returns:
            The transformation config, or None if not found.
        """
        return self._tool_transformations.get(tool_name)

    def remove_tool_transformation(self, tool_name: str) -> None:
        """Remove a tool transformation.

        Args:
            tool_name: The name of the tool.
        """
        if tool_name in self._tool_transformations:
            del self._tool_transformations[tool_name]

    # =========================================================================
    # Provider interface implementation
    # =========================================================================

    async def list_tools(self) -> Sequence[Tool]:
        """Return all visible tools with transformations applied."""
        tools = {k: v for k, v in self._components.items() if isinstance(v, Tool)}
        transformed = apply_transformations_to_tools(
            tools=tools,
            transformations=self._tool_transformations,
        )
        return [t for t in transformed.values() if self._is_component_enabled(t)]

    async def get_tool(self, name: str) -> Tool | None:
        """Get a tool by name, with transformations applied."""
        tools = await self.list_tools()
        return next((t for t in tools if t.name == name), None)

    async def list_resources(self) -> Sequence[Resource]:
        """Return all visible resources."""
        return [
            v
            for v in self._components.values()
            if isinstance(v, Resource) and self._is_component_enabled(v)
        ]

    async def get_resource(self, uri: str) -> Resource | None:
        """Get a resource by URI if visible."""
        component = self._components.get(Resource.make_key(uri))
        if isinstance(component, Resource) and self._is_component_enabled(component):
            return component
        return None

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all visible resource templates."""
        return [
            v
            for v in self._components.values()
            if isinstance(v, ResourceTemplate) and self._is_component_enabled(v)
        ]

    async def get_resource_template(self, uri: str) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI if visible."""
        for component in self._components.values():
            if (
                isinstance(component, ResourceTemplate)
                and component.matches(uri) is not None
                and self._is_component_enabled(component)
            ):
                return component
        return None

    async def list_prompts(self) -> Sequence[Prompt]:
        """Return all visible prompts."""
        return [
            v
            for v in self._components.values()
            if isinstance(v, Prompt) and self._is_component_enabled(v)
        ]

    async def get_prompt(self, name: str) -> Prompt | None:
        """Get a prompt by name if visible."""
        component = self._components.get(Prompt.make_key(name))
        if isinstance(component, Prompt) and self._is_component_enabled(component):
            return component
        return None

    async def get_component(
        self, key: str
    ) -> Tool | Resource | ResourceTemplate | Prompt | None:
        """Get a component by its prefixed key.

        Efficient O(1) lookup in the unified components dict.
        """
        component = self._get_component(key)
        if component and self._is_component_enabled(component):
            return component  # type: ignore[return-value]
        return None

    # =========================================================================
    # Task registration
    # =========================================================================

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Return components eligible for background task execution.

        Returns components that have task_config.mode != 'forbidden'.
        This includes both FunctionTool/Resource/Prompt instances created via
        decorators and custom Tool/Resource/Prompt subclasses.
        """
        return [c for c in self._components.values() if c.task_config.supports_tasks()]

    # =========================================================================
    # Decorator methods
    # =========================================================================

    @overload
    def tool(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool = True,
        task: bool | TaskConfig | None = None,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool = True,
        task: bool | TaskConfig | None = None,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool = True,
        task: bool | TaskConfig | None = None,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
    ) -> (
        Callable[[AnyFunction], FunctionTool]
        | FunctionTool
        | partial[Callable[[AnyFunction], FunctionTool] | FunctionTool]
    ):
        """Decorator to register a tool.

        This decorator supports multiple calling patterns:
        - @provider.tool (without parentheses)
        - @provider.tool() (with empty parentheses)
        - @provider.tool("custom_name") (with name as first argument)
        - @provider.tool(name="custom_name") (with name as keyword argument)
        - provider.tool(function, name="custom_name") (direct function call)

        Args:
            name_or_fn: Either a function (when used as @tool), a string name, or None
            name: Optional name for the tool (keyword-only, alternative to name_or_fn)
            title: Optional title for the tool
            description: Optional description of what the tool does
            icons: Optional icons for the tool
            tags: Optional set of tags for categorizing the tool
            output_schema: Optional JSON schema for the tool's output
            annotations: Optional annotations about the tool's behavior
            exclude_args: Optional list of argument names to exclude from the tool schema
            meta: Optional meta information about the tool
            enabled: Whether the tool is enabled (default True). If False, adds to blocklist.
            task: Optional task configuration for background execution
            serializer: Deprecated. Return ToolResult from your tools for full control over serialization.

        Returns:
            The registered FunctionTool or a decorator function.

        Example:
            ```python
            provider = LocalProvider()

            @provider.tool
            def greet(name: str) -> str:
                return f"Hello, {name}!"

            @provider.tool("custom_name")
            def my_tool(x: int) -> str:
                return str(x)
            ```
        """
        if serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                DeprecationWarning,
                stacklevel=2,
            )
        if isinstance(annotations, dict):
            annotations = ToolAnnotations(**annotations)

        if isinstance(name_or_fn, classmethod):
            raise ValueError(
                inspect.cleandoc(
                    """
                    To decorate a classmethod, first define the method and then call
                    tool() directly on the method instead of using it as a
                    decorator. See https://gofastmcp.com/patterns/decorating-methods
                    for examples and more information.
                    """
                )
            )

        # Determine the actual name and function based on the calling pattern
        if inspect.isroutine(name_or_fn):
            # Case 1: @tool (without parens) - function passed directly
            # Case 2: direct call like tool(fn, name="something")
            fn = name_or_fn
            tool_name = name  # Use keyword name if provided, otherwise None

            # Resolve task parameter - default to False for standalone usage
            supports_task: bool | TaskConfig = task if task is not None else False

            # Register the tool immediately and return the tool object
            tool_obj = Tool.from_function(
                fn,
                name=tool_name,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                output_schema=output_schema,
                annotations=annotations,
                exclude_args=exclude_args,
                meta=meta,
                serializer=serializer,
                task=supports_task,
            )
            self.add_tool(tool_obj)
            # If disabled, add to blocklist
            if not enabled:
                self.disable(keys=[tool_obj.key])
            return tool_obj

        elif isinstance(name_or_fn, str):
            # Case 3: @tool("custom_name") - name passed as first argument
            if name is not None:
                raise TypeError(
                    "Cannot specify both a name as first argument and as keyword argument. "
                    f"Use either @tool('{name_or_fn}') or @tool(name='{name}'), not both."
                )
            tool_name = name_or_fn
        elif name_or_fn is None:
            # Case 4: @tool() or @tool(name="something") - use keyword name
            tool_name = name
        else:
            raise TypeError(
                f"First argument to @tool must be a function, string, or None, got {type(name_or_fn)}"
            )

        # Return partial for cases where we need to wait for the function
        return partial(
            self.tool,
            name=tool_name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            exclude_args=exclude_args,
            meta=meta,
            enabled=enabled,
            task=task,
            serializer=serializer,
        )

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        annotations: Annotations | dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> Callable[[AnyFunction], Resource | ResourceTemplate]:
        """Decorator to register a function as a resource.

        If the URI contains parameters (e.g. "resource://{param}") or the function
        has parameters, it will be registered as a template resource.

        Args:
            uri: URI for the resource (e.g. "resource://my-resource" or "resource://{param}")
            name: Optional name for the resource
            title: Optional title for the resource
            description: Optional description of the resource
            icons: Optional icons for the resource
            mime_type: Optional MIME type for the resource
            tags: Optional set of tags for categorizing the resource
            enabled: Whether the resource is enabled (default True). If False, adds to blocklist.
            annotations: Optional annotations about the resource's behavior
            meta: Optional meta information about the resource
            task: Optional task configuration for background execution

        Returns:
            A decorator function.

        Example:
            ```python
            provider = LocalProvider()

            @provider.resource("data://config")
            def get_config() -> str:
                return '{"setting": "value"}'

            @provider.resource("data://{city}/weather")
            def get_weather(city: str) -> str:
                return f"Weather for {city}"
            ```
        """
        if isinstance(annotations, dict):
            annotations = Annotations(**annotations)

        # Check if user passed function directly instead of calling decorator
        if inspect.isroutine(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "Did you forget to call it? Use @resource('uri') instead of @resource"
            )

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate:
            if isinstance(fn, classmethod):
                raise ValueError(
                    inspect.cleandoc(
                        """
                        To decorate a classmethod, first define the method and then call
                        resource() directly on the method instead of using it as a
                        decorator. See https://gofastmcp.com/patterns/decorating-methods
                        for examples and more information.
                        """
                    )
                )

            # Resolve task parameter - default to False for standalone usage
            supports_task: bool | TaskConfig = task if task is not None else False

            # Check if this should be a template
            has_uri_params = "{" in uri and "}" in uri
            # Use wrapper to check for user-facing parameters
            from fastmcp.server.dependencies import without_injected_parameters

            wrapper_fn = without_injected_parameters(fn)
            has_func_params = bool(inspect.signature(wrapper_fn).parameters)

            if has_uri_params or has_func_params:
                template = ResourceTemplate.from_function(
                    fn=fn,
                    uri_template=uri,
                    name=name,
                    title=title,
                    description=description,
                    icons=icons,
                    mime_type=mime_type,
                    tags=tags,
                    annotations=annotations,
                    meta=meta,
                    task=supports_task,
                )
                self.add_template(template)
                # If disabled, add to blocklist
                if not enabled:
                    self.disable(keys=[template.key])
                return template
            elif not has_uri_params and not has_func_params:
                resource_obj = Resource.from_function(
                    fn=fn,
                    uri=uri,
                    name=name,
                    title=title,
                    description=description,
                    icons=icons,
                    mime_type=mime_type,
                    tags=tags,
                    annotations=annotations,
                    meta=meta,
                    task=supports_task,
                )
                self.add_resource(resource_obj)
                # If disabled, add to blocklist
                if not enabled:
                    self.disable(keys=[resource_obj.key])
                return resource_obj
            else:
                raise ValueError(
                    "Invalid resource or template definition due to a "
                    "mismatch between URI parameters and function parameters."
                )

        return decorator

    @overload
    def prompt(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> (
        Callable[[AnyFunction], FunctionPrompt]
        | FunctionPrompt
        | partial[Callable[[AnyFunction], FunctionPrompt] | FunctionPrompt]
    ):
        """Decorator to register a prompt.

        This decorator supports multiple calling patterns:
        - @provider.prompt (without parentheses)
        - @provider.prompt() (with empty parentheses)
        - @provider.prompt("custom_name") (with name as first argument)
        - @provider.prompt(name="custom_name") (with name as keyword argument)
        - provider.prompt(function, name="custom_name") (direct function call)

        Args:
            name_or_fn: Either a function (when used as @prompt), a string name, or None
            name: Optional name for the prompt (keyword-only, alternative to name_or_fn)
            title: Optional title for the prompt
            description: Optional description of what the prompt does
            icons: Optional icons for the prompt
            tags: Optional set of tags for categorizing the prompt
            enabled: Whether the prompt is enabled (default True). If False, adds to blocklist.
            meta: Optional meta information about the prompt
            task: Optional task configuration for background execution

        Returns:
            The registered FunctionPrompt or a decorator function.

        Example:
            ```python
            provider = LocalProvider()

            @provider.prompt
            def analyze(topic: str) -> list:
                return [{"role": "user", "content": f"Analyze: {topic}"}]

            @provider.prompt("custom_name")
            def my_prompt(data: str) -> list:
                return [{"role": "user", "content": data}]
            ```
        """
        if isinstance(name_or_fn, classmethod):
            raise ValueError(
                inspect.cleandoc(
                    """
                    To decorate a classmethod, first define the method and then call
                    prompt() directly on the method instead of using it as a
                    decorator. See https://gofastmcp.com/patterns/decorating-methods
                    for examples and more information.
                    """
                )
            )

        # Determine the actual name and function based on the calling pattern
        if inspect.isroutine(name_or_fn):
            # Case 1: @prompt (without parens) - function passed directly
            # Case 2: direct call like prompt(fn, name="something")
            fn = name_or_fn
            prompt_name = name  # Use keyword name if provided, otherwise None

            # Resolve task parameter - default to False for standalone usage
            supports_task: bool | TaskConfig = task if task is not None else False

            # Register the prompt immediately
            prompt_obj = Prompt.from_function(
                fn=fn,
                name=prompt_name,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                meta=meta,
                task=supports_task,
            )
            self.add_prompt(prompt_obj)
            # If disabled, add to blocklist
            if not enabled:
                self.disable(keys=[prompt_obj.key])
            return prompt_obj

        elif isinstance(name_or_fn, str):
            # Case 3: @prompt("custom_name") - name passed as first argument
            if name is not None:
                raise TypeError(
                    "Cannot specify both a name as first argument and as keyword argument. "
                    f"Use either @prompt('{name_or_fn}') or @prompt(name='{name}'), not both."
                )
            prompt_name = name_or_fn
        elif name_or_fn is None:
            # Case 4: @prompt() or @prompt(name="something") - use keyword name
            prompt_name = name
        else:
            raise TypeError(
                f"First argument to @prompt must be a function, string, or None, got {type(name_or_fn)}"
            )

        # Return partial for cases where we need to wait for the function
        return partial(
            self.prompt,
            name=prompt_name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            enabled=enabled,
            meta=meta,
            task=task,
        )
