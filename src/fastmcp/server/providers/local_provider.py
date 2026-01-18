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
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.function_resource import resource as standalone_resource
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import AuthCheckCallable, Tool
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT
from fastmcp.utilities.versions import VersionSpec, version_sort_key

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

    # =========================================================================
    # Storage methods
    # =========================================================================

    def _get_component_identity(self, component: FastMCPComponent) -> tuple[type, str]:
        """Get the identity (type, name/uri) for a component.

        Returns:
            A tuple of (component_type, logical_name) where logical_name is
            the name for tools/prompts or URI for resources/templates.
        """
        if isinstance(component, Tool):
            return (Tool, component.name)
        elif isinstance(component, ResourceTemplate):
            return (ResourceTemplate, component.uri_template)
        elif isinstance(component, Resource):
            return (Resource, str(component.uri))
        elif isinstance(component, Prompt):
            return (Prompt, component.name)
        else:
            # Fall back to key without version suffix
            key = component.key
            base_key = key.rsplit("@", 1)[0] if "@" in key else key
            return (type(component), base_key)

    def _check_version_mixing(self, component: _C) -> None:
        """Check that versioned and unversioned components aren't mixed.

        LocalProvider enforces a simple rule: for any given name/URI, all
        registered components must either be versioned or unversioned, not both.
        This prevents confusing situations where unversioned components can't
        be filtered out by version filters.

        Args:
            component: The component being added.

        Raises:
            ValueError: If adding would mix versioned and unversioned components.
        """
        comp_type, logical_name = self._get_component_identity(component)
        is_versioned = component.version is not None

        # Check all existing components of the same type and logical name
        for existing in self._components.values():
            if not isinstance(existing, comp_type):
                continue

            _, existing_name = self._get_component_identity(existing)
            if existing_name != logical_name:
                continue

            existing_versioned = existing.version is not None
            if is_versioned != existing_versioned:
                type_name = comp_type.__name__.lower()
                if is_versioned:
                    raise ValueError(
                        f"Cannot add versioned {type_name} {logical_name!r} "
                        f"(version={component.version!r}): an unversioned "
                        f"{type_name} with this name already exists. "
                        f"Either version all components or none."
                    )
                else:
                    raise ValueError(
                        f"Cannot add unversioned {type_name} {logical_name!r}: "
                        f"versioned {type_name}s with this name already exist "
                        f"(e.g., version={existing.version!r}). "
                        f"Either version all components or none."
                    )

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

        # Check for versioned/unversioned mixing before adding
        self._check_version_mixing(component)

        self._components[component.key] = component
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

    def _get_component(self, key: str) -> FastMCPComponent | None:
        """Get a component by its prefixed key.

        Args:
            key: The prefixed key (e.g., "tool:name", "resource:uri").

        Returns:
            The component, or None if not found.
        """
        return self._components.get(key)

    def add_tool(self, tool: Tool | Callable[..., Any]) -> Tool:
        """Add a tool to this provider's storage.

        Accepts either a Tool object or a decorated function with __fastmcp__ metadata.
        """
        enabled = True
        if not isinstance(tool, Tool):
            from fastmcp.decorators import get_fastmcp_meta
            from fastmcp.tools.function_tool import ToolMeta

            meta = get_fastmcp_meta(tool)
            if meta is not None and isinstance(meta, ToolMeta):
                resolved_task = meta.task if meta.task is not None else False
                enabled = meta.enabled
                tool = Tool.from_function(
                    tool,
                    name=meta.name,
                    version=meta.version,
                    title=meta.title,
                    description=meta.description,
                    icons=meta.icons,
                    tags=meta.tags,
                    output_schema=meta.output_schema,
                    annotations=meta.annotations,
                    meta=meta.meta,
                    task=resolved_task,
                    exclude_args=meta.exclude_args,
                    serializer=meta.serializer,
                    timeout=meta.timeout,
                    auth=meta.auth,
                )
            else:
                tool = Tool.from_function(tool)
        self._add_component(tool)
        if not enabled:
            self.disable(keys={tool.key})
        return tool

    def remove_tool(self, name: str, version: str | None = None) -> None:
        """Remove tool(s) from this provider's storage.

        Args:
            name: The tool name.
            version: If None, removes ALL versions. If specified, removes only that version.

        Raises:
            KeyError: If no matching tool is found.
        """
        if version is None:
            # Remove all versions
            keys_to_remove = [
                k
                for k, c in self._components.items()
                if isinstance(c, Tool) and c.name == name
            ]
            if not keys_to_remove:
                raise KeyError(f"Tool {name!r} not found")
            for key in keys_to_remove:
                self._remove_component(key)
        else:
            # Remove specific version - key format is "tool:name@version"
            key = f"{Tool.make_key(name)}@{version}"
            if key not in self._components:
                raise KeyError(f"Tool {name!r} version {version!r} not found")
            self._remove_component(key)

    def add_resource(
        self, resource: Resource | ResourceTemplate | Callable[..., Any]
    ) -> Resource | ResourceTemplate:
        """Add a resource to this provider's storage.

        Accepts either a Resource/ResourceTemplate object or a decorated function with __fastmcp__ metadata.
        """
        enabled = True
        if not isinstance(resource, (Resource, ResourceTemplate)):
            from fastmcp.decorators import get_fastmcp_meta
            from fastmcp.resources.function_resource import ResourceMeta
            from fastmcp.server.dependencies import without_injected_parameters

            meta = get_fastmcp_meta(resource)
            if meta is not None and isinstance(meta, ResourceMeta):
                resolved_task = meta.task if meta.task is not None else False
                enabled = meta.enabled
                has_uri_params = "{" in meta.uri and "}" in meta.uri
                wrapper_fn = without_injected_parameters(resource)
                has_func_params = bool(inspect.signature(wrapper_fn).parameters)

                if has_uri_params or has_func_params:
                    resource = ResourceTemplate.from_function(
                        fn=resource,
                        uri_template=meta.uri,
                        name=meta.name,
                        version=meta.version,
                        title=meta.title,
                        description=meta.description,
                        icons=meta.icons,
                        mime_type=meta.mime_type,
                        tags=meta.tags,
                        annotations=meta.annotations,
                        meta=meta.meta,
                        task=resolved_task,
                        auth=meta.auth,
                    )
                else:
                    resource = Resource.from_function(
                        fn=resource,
                        uri=meta.uri,
                        name=meta.name,
                        version=meta.version,
                        title=meta.title,
                        description=meta.description,
                        icons=meta.icons,
                        mime_type=meta.mime_type,
                        tags=meta.tags,
                        annotations=meta.annotations,
                        meta=meta.meta,
                        task=resolved_task,
                        auth=meta.auth,
                    )
            else:
                raise TypeError(
                    f"Expected Resource, ResourceTemplate, or @resource-decorated function, got {type(resource).__name__}. "
                    "Use @resource('uri') decorator or pass a Resource/ResourceTemplate instance."
                )
        self._add_component(resource)
        if not enabled:
            self.disable(keys={resource.key})
        return resource

    def remove_resource(self, uri: str, version: str | None = None) -> None:
        """Remove resource(s) from this provider's storage.

        Args:
            uri: The resource URI.
            version: If None, removes ALL versions. If specified, removes only that version.

        Raises:
            KeyError: If no matching resource is found.
        """
        if version is None:
            # Remove all versions
            keys_to_remove = [
                k
                for k, c in self._components.items()
                if isinstance(c, Resource) and str(c.uri) == uri
            ]
            if not keys_to_remove:
                raise KeyError(f"Resource {uri!r} not found")
            for key in keys_to_remove:
                self._remove_component(key)
        else:
            # Remove specific version
            key = f"{Resource.make_key(uri)}@{version}"
            if key not in self._components:
                raise KeyError(f"Resource {uri!r} version {version!r} not found")
            self._remove_component(key)

    def add_template(self, template: ResourceTemplate) -> ResourceTemplate:
        """Add a resource template to this provider's storage."""
        return self._add_component(template)

    def remove_template(self, uri_template: str, version: str | None = None) -> None:
        """Remove resource template(s) from this provider's storage.

        Args:
            uri_template: The template URI pattern.
            version: If None, removes ALL versions. If specified, removes only that version.

        Raises:
            KeyError: If no matching template is found.
        """
        if version is None:
            # Remove all versions
            keys_to_remove = [
                k
                for k, c in self._components.items()
                if isinstance(c, ResourceTemplate) and c.uri_template == uri_template
            ]
            if not keys_to_remove:
                raise KeyError(f"Template {uri_template!r} not found")
            for key in keys_to_remove:
                self._remove_component(key)
        else:
            # Remove specific version
            key = f"{ResourceTemplate.make_key(uri_template)}@{version}"
            if key not in self._components:
                raise KeyError(
                    f"Template {uri_template!r} version {version!r} not found"
                )
            self._remove_component(key)

    def add_prompt(self, prompt: Prompt | Callable[..., Any]) -> Prompt:
        """Add a prompt to this provider's storage.

        Accepts either a Prompt object or a decorated function with __fastmcp__ metadata.
        """
        enabled = True
        if not isinstance(prompt, Prompt):
            from fastmcp.decorators import get_fastmcp_meta
            from fastmcp.prompts.function_prompt import PromptMeta

            meta = get_fastmcp_meta(prompt)
            if meta is not None and isinstance(meta, PromptMeta):
                resolved_task = meta.task if meta.task is not None else False
                enabled = meta.enabled
                prompt = Prompt.from_function(
                    prompt,
                    name=meta.name,
                    version=meta.version,
                    title=meta.title,
                    description=meta.description,
                    icons=meta.icons,
                    tags=meta.tags,
                    meta=meta.meta,
                    task=resolved_task,
                    auth=meta.auth,
                )
            else:
                raise TypeError(
                    f"Expected Prompt or @prompt-decorated function, got {type(prompt).__name__}. "
                    "Use @prompt decorator or pass a Prompt instance."
                )
        self._add_component(prompt)
        if not enabled:
            self.disable(keys={prompt.key})
        return prompt

    def remove_prompt(self, name: str, version: str | None = None) -> None:
        """Remove prompt(s) from this provider's storage.

        Args:
            name: The prompt name.
            version: If None, removes ALL versions. If specified, removes only that version.

        Raises:
            KeyError: If no matching prompt is found.
        """
        if version is None:
            # Remove all versions
            keys_to_remove = [
                k
                for k, c in self._components.items()
                if isinstance(c, Prompt) and c.name == name
            ]
            if not keys_to_remove:
                raise KeyError(f"Prompt {name!r} not found")
            for key in keys_to_remove:
                self._remove_component(key)
        else:
            # Remove specific version
            key = f"{Prompt.make_key(name)}@{version}"
            if key not in self._components:
                raise KeyError(f"Prompt {name!r} version {version!r} not found")
            self._remove_component(key)

    # =========================================================================
    # Provider interface implementation
    # =========================================================================

    async def _list_tools(self) -> Sequence[Tool]:
        """Return all tools."""
        return [v for v in self._components.values() if isinstance(v, Tool)]

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name.

        Args:
            name: The tool name.
            version: Optional version filter. If None, returns highest version.
        """
        matching = [
            v
            for v in self._components.values()
            if isinstance(v, Tool) and v.name == name
        ]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_resources(self) -> Sequence[Resource]:
        """Return all resources."""
        return [v for v in self._components.values() if isinstance(v, Resource)]

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI.

        Args:
            uri: The resource URI.
            version: Optional version filter. If None, returns highest version.
        """
        matching = [
            v
            for v in self._components.values()
            if isinstance(v, Resource) and str(v.uri) == uri
        ]
        if version:
            matching = [r for r in matching if version.matches(r.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Return all resource templates."""
        return [v for v in self._components.values() if isinstance(v, ResourceTemplate)]

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI.

        Args:
            uri: The URI to match against templates.
            version: Optional version filter. If None, returns highest version.
        """
        # Find all templates that match the URI
        matching = [
            component
            for component in self._components.values()
            if isinstance(component, ResourceTemplate)
            and component.matches(uri) is not None
        ]
        if version:
            matching = [t for t in matching if version.matches(t.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

    async def _list_prompts(self) -> Sequence[Prompt]:
        """Return all prompts."""
        return [v for v in self._components.values() if isinstance(v, Prompt)]

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a prompt by name.

        Args:
            name: The prompt name.
            version: Optional version filter. If None, returns highest version.
        """
        matching = [
            v
            for v in self._components.values()
            if isinstance(v, Prompt) and v.name == name
        ]
        if version:
            matching = [p for p in matching if version.matches(p.version)]
        if not matching:
            return None
        return max(matching, key=version_sort_key)  # type: ignore[type-var]

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
        version: str | int | None = None,
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
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
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
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    # NOTE: This method mirrors fastmcp.tools.tool() but adds registration,
    # the `enabled` param, and supports deprecated params (serializer, exclude_args).
    # When deprecated params are removed, this should delegate to the standalone
    # decorator to reduce duplication.
    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
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
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
            raise TypeError(
                "To decorate a classmethod, use @classmethod above @tool. "
                "See https://gofastmcp.com/servers/tools#using-with-methods"
            )

        def decorate_and_register(
            fn: AnyFunction, tool_name: str | None
        ) -> FunctionTool | AnyFunction:
            # Check for unbound method
            try:
                params = list(inspect.signature(fn).parameters.keys())
            except (ValueError, TypeError):
                params = []
            if params and params[0] in ("self", "cls"):
                fn_name = getattr(fn, "__name__", "function")
                raise TypeError(
                    f"The function '{fn_name}' has '{params[0]}' as its first parameter. "
                    f"Use the standalone @tool decorator and register the bound method:\n\n"
                    f"    from fastmcp.tools import tool\n\n"
                    f"    class MyClass:\n"
                    f"        @tool\n"
                    f"        def {fn_name}(...):\n"
                    f"            ...\n\n"
                    f"    obj = MyClass()\n"
                    f"    mcp.add_tool(obj.{fn_name})\n\n"
                    f"See https://gofastmcp.com/servers/tools#using-with-methods"
                )

            resolved_task: bool | TaskConfig = task if task is not None else False

            if fastmcp.settings.decorator_mode == "object":
                tool_obj = Tool.from_function(
                    fn,
                    name=tool_name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    output_schema=output_schema,
                    annotations=annotations,
                    exclude_args=exclude_args,
                    meta=meta,
                    serializer=serializer,
                    task=resolved_task,
                    timeout=timeout,
                    auth=auth,
                )
                self._add_component(tool_obj)
                if not enabled:
                    self.disable(keys={tool_obj.key})
                return tool_obj
            else:
                from fastmcp.tools.function_tool import ToolMeta

                metadata = ToolMeta(
                    name=tool_name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    output_schema=output_schema,
                    annotations=annotations,
                    meta=meta,
                    task=task,
                    exclude_args=exclude_args,
                    serializer=serializer,
                    timeout=timeout,
                    auth=auth,
                    enabled=enabled,
                )
                target = fn.__func__ if hasattr(fn, "__func__") else fn
                target.__fastmcp__ = metadata  # type: ignore[attr-defined]
                tool_obj = self.add_tool(fn)
                return fn

        if inspect.isroutine(name_or_fn):
            return decorate_and_register(name_or_fn, name)

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
            version=version,
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
            timeout=timeout,
            auth=auth,
        )

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        annotations: Annotations | dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], Resource | ResourceTemplate | AnyFunction]:
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
            auth: Optional authorization checks for the resource

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

        if inspect.isroutine(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "It requires a URI as the first argument. "
                "Use @resource('uri') instead of @resource"
            )

        resolved_task: bool | TaskConfig = task if task is not None else False

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate | AnyFunction:
            # Check for unbound method
            try:
                params = list(inspect.signature(fn).parameters.keys())
            except (ValueError, TypeError):
                params = []
            if params and params[0] in ("self", "cls"):
                fn_name = getattr(fn, "__name__", "function")
                raise TypeError(
                    f"The function '{fn_name}' has '{params[0]}' as its first parameter. "
                    f"Use the standalone @resource decorator and register the bound method:\n\n"
                    f"    from fastmcp.resources import resource\n\n"
                    f"    class MyClass:\n"
                    f"        @resource('{uri}')\n"
                    f"        def {fn_name}(...):\n"
                    f"            ...\n\n"
                    f"    obj = MyClass()\n"
                    f"    mcp.add_resource(obj.{fn_name})\n\n"
                    f"See https://gofastmcp.com/servers/resources#using-with-methods"
                )

            if fastmcp.settings.decorator_mode == "object":
                create_resource = standalone_resource(
                    uri,
                    name=name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    mime_type=mime_type,
                    tags=tags,
                    annotations=annotations,
                    meta=meta,
                    task=resolved_task,
                    auth=auth,
                )
                obj = create_resource(fn)
                # In legacy mode, standalone_resource always returns a component
                assert isinstance(obj, (Resource, ResourceTemplate))
                if isinstance(obj, ResourceTemplate):
                    self.add_template(obj)
                    if not enabled:
                        self.disable(keys={obj.key})
                else:
                    self.add_resource(obj)
                    if not enabled:
                        self.disable(keys={obj.key})
                return obj
            else:
                from fastmcp.resources.function_resource import ResourceMeta

                metadata = ResourceMeta(
                    uri=uri,
                    name=name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    mime_type=mime_type,
                    annotations=annotations,
                    meta=meta,
                    task=task,
                    auth=auth,
                    enabled=enabled,
                )
                target = fn.__func__ if hasattr(fn, "__func__") else fn
                target.__fastmcp__ = metadata  # type: ignore[attr-defined]
                self.add_resource(fn)
                return fn

        return decorator

    @overload
    def prompt(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
            auth: Optional authorization checks for the prompt

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
            raise TypeError(
                "To decorate a classmethod, use @classmethod above @prompt. "
                "See https://gofastmcp.com/servers/prompts#using-with-methods"
            )

        def decorate_and_register(
            fn: AnyFunction, prompt_name: str | None
        ) -> FunctionPrompt | AnyFunction:
            # Check for unbound method
            try:
                params = list(inspect.signature(fn).parameters.keys())
            except (ValueError, TypeError):
                params = []
            if params and params[0] in ("self", "cls"):
                fn_name = getattr(fn, "__name__", "function")
                raise TypeError(
                    f"The function '{fn_name}' has '{params[0]}' as its first parameter. "
                    f"Use the standalone @prompt decorator and register the bound method:\n\n"
                    f"    from fastmcp.prompts import prompt\n\n"
                    f"    class MyClass:\n"
                    f"        @prompt\n"
                    f"        def {fn_name}(...):\n"
                    f"            ...\n\n"
                    f"    obj = MyClass()\n"
                    f"    mcp.add_prompt(obj.{fn_name})\n\n"
                    f"See https://gofastmcp.com/servers/prompts#using-with-methods"
                )

            resolved_task: bool | TaskConfig = task if task is not None else False

            if fastmcp.settings.decorator_mode == "object":
                prompt_obj = Prompt.from_function(
                    fn,
                    name=prompt_name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    meta=meta,
                    task=resolved_task,
                    auth=auth,
                )
                self._add_component(prompt_obj)
                if not enabled:
                    self.disable(keys={prompt_obj.key})
                return prompt_obj
            else:
                from fastmcp.prompts.function_prompt import PromptMeta

                metadata = PromptMeta(
                    name=prompt_name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    meta=meta,
                    task=task,
                    auth=auth,
                    enabled=enabled,
                )
                target = fn.__func__ if hasattr(fn, "__func__") else fn
                target.__fastmcp__ = metadata  # type: ignore[attr-defined]
                self.add_prompt(fn)
                return fn

        if inspect.isroutine(name_or_fn):
            return decorate_and_register(name_or_fn, name)

        elif isinstance(name_or_fn, str):
            if name is not None:
                raise TypeError(
                    f"Cannot specify both a name as first argument and as keyword argument. "
                    f"Use either @prompt('{name_or_fn}') or @prompt(name='{name}'), not both."
                )
            prompt_name = name_or_fn
        elif name_or_fn is None:
            prompt_name = name
        else:
            raise TypeError(f"Invalid first argument: {type(name_or_fn)}")

        return partial(
            self.prompt,
            name=prompt_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            enabled=enabled,
            task=task,
            auth=auth,
        )
