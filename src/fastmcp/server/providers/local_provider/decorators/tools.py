"""Tool decorator mixin for LocalProvider.

This module provides the ToolDecoratorMixin class that adds tool
registration functionality to LocalProvider.
"""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, overload

import mcp.types
from mcp.types import AnyFunction, ToolAnnotations

import fastmcp
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import AuthCheckCallable, Tool
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from fastmcp.server.providers.local_provider import LocalProvider
    from fastmcp.tools.tool import ToolResultSerializerType

DuplicateBehavior = Literal["error", "warn", "replace", "ignore"]


class ToolDecoratorMixin:
    """Mixin class providing tool decorator functionality for LocalProvider.

    This mixin contains all methods related to:
    - Tool registration via add_tool()
    - Tool decorator (@provider.tool)
    """

    def add_tool(self: LocalProvider, tool: Tool | Callable[..., Any]) -> Tool:
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

    @overload
    def tool(
        self: LocalProvider,
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
        self: LocalProvider,
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
        self: LocalProvider,
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
