"""Provides a base mixin class and decorators for easy registration of class methods with FastMCP."""

import inspect
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import fastmcp
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.tools.tool import Tool
from fastmcp.utilities.types import get_fn_name

if TYPE_CHECKING:
    from fastmcp.server import FastMCP

_MCP_REGISTRATION_TOOL_ATTR = "_mcp_tool_registration"
_MCP_REGISTRATION_RESOURCE_ATTR = "_mcp_resource_registration"
_MCP_REGISTRATION_PROMPT_ATTR = "_mcp_prompt_registration"

_DEFAULT_SEPARATOR_TOOL = "_"
_DEFAULT_SEPARATOR_RESOURCE = "+"
_DEFAULT_SEPARATOR_PROMPT = "_"

# Sentinel key stored in registration dicts for the mixin-only `enabled` flag.
# Prefixed with an underscore to avoid collisions with any from_function parameter.
_MIXIN_ENABLED_KEY = "_mixin_enabled"

# Valid keyword arguments for each from_function, derived once at import time
# directly from the live signatures.  They stay in sync automatically whenever
# the underlying signatures gain or lose parameters — no manual updates needed.
_TOOL_VALID_KWARGS: frozenset[str] = frozenset(
    p for p in inspect.signature(Tool.from_function).parameters if p != "fn"
)
_RESOURCE_VALID_KWARGS: frozenset[str] = frozenset(
    p
    for p in inspect.signature(Resource.from_function).parameters
    if p not in ("fn", "uri")
)
_PROMPT_VALID_KWARGS: frozenset[str] = frozenset(
    p for p in inspect.signature(Prompt.from_function).parameters if p != "fn"
)


def mcp_tool(
    name: str | None = None,
    *,
    enabled: bool | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP tool for later registration.

    Accepts all parameters supported by ``Tool.from_function``.  Any new
    parameters added to ``Tool.from_function`` are automatically forwarded
    without requiring changes here.

    Args:
        name: Tool name.  Defaults to the decorated method name.
        enabled: If ``False``, the tool is skipped during registration.
        **kwargs: Additional keyword arguments forwarded verbatim to
            ``Tool.from_function`` (e.g. ``description``, ``tags``,
            ``annotations``, ``auth``, ``timeout``, ``version``, …).

    Raises:
        TypeError: If an unrecognised keyword argument is supplied.  The error
            is raised immediately at decoration time rather than later.
    """
    unknown = set(kwargs) - _TOOL_VALID_KWARGS
    if unknown:
        raise TypeError(
            f"mcp_tool() got unexpected keyword argument(s): {sorted(unknown)!r}. "
            f"Valid keyword arguments are: {sorted(_TOOL_VALID_KWARGS)}"
        )

    if "serializer" in kwargs and fastmcp.settings.deprecation_warnings:
        warnings.warn(
            "The `serializer` parameter is deprecated. "
            "Return ToolResult from your tools for full control over serialization. "
            "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
            DeprecationWarning,
            stacklevel=2,
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args: dict[str, Any] = {"name": name or get_fn_name(func), **kwargs}
        if enabled is not None:
            call_args[_MIXIN_ENABLED_KEY] = enabled
        setattr(func, _MCP_REGISTRATION_TOOL_ATTR, call_args)
        return func

    return decorator


def mcp_resource(
    uri: str,
    *,
    name: str | None = None,
    enabled: bool | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP resource for later registration.

    Accepts all parameters supported by ``Resource.from_function``.  Any new
    parameters added to ``Resource.from_function`` are automatically forwarded
    without requiring changes here.

    Args:
        uri: Resource URI (required).
        name: Resource name.  Defaults to the decorated method name.
        enabled: If ``False``, the resource is skipped during registration.
        **kwargs: Additional keyword arguments forwarded verbatim to
            ``Resource.from_function`` (e.g. ``description``, ``tags``,
            ``mime_type``, ``auth``, ``version``, …).

    Raises:
        TypeError: If an unrecognised keyword argument is supplied.  The error
            is raised immediately at decoration time rather than later.
    """
    unknown = set(kwargs) - _RESOURCE_VALID_KWARGS
    if unknown:
        raise TypeError(
            f"mcp_resource() got unexpected keyword argument(s): {sorted(unknown)!r}. "
            f"Valid keyword arguments are: {sorted(_RESOURCE_VALID_KWARGS)}"
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args: dict[str, Any] = {
            "uri": uri,
            "name": name or get_fn_name(func),
            **kwargs,
        }
        if enabled is not None:
            call_args[_MIXIN_ENABLED_KEY] = enabled
        setattr(func, _MCP_REGISTRATION_RESOURCE_ATTR, call_args)
        return func

    return decorator


def mcp_prompt(
    name: str | None = None,
    *,
    enabled: bool | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP prompt for later registration.

    Accepts all parameters supported by ``Prompt.from_function``.  Any new
    parameters added to ``Prompt.from_function`` are automatically forwarded
    without requiring changes here.

    Args:
        name: Prompt name.  Defaults to the decorated method name.
        enabled: If ``False``, the prompt is skipped during registration.
        **kwargs: Additional keyword arguments forwarded verbatim to
            ``Prompt.from_function`` (e.g. ``description``, ``tags``,
            ``auth``, ``version``, …).

    Raises:
        TypeError: If an unrecognised keyword argument is supplied.  The error
            is raised immediately at decoration time rather than later.
    """
    unknown = set(kwargs) - _PROMPT_VALID_KWARGS
    if unknown:
        raise TypeError(
            f"mcp_prompt() got unexpected keyword argument(s): {sorted(unknown)!r}. "
            f"Valid keyword arguments are: {sorted(_PROMPT_VALID_KWARGS)}"
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args: dict[str, Any] = {"name": name or get_fn_name(func), **kwargs}
        if enabled is not None:
            call_args[_MIXIN_ENABLED_KEY] = enabled
        setattr(func, _MCP_REGISTRATION_PROMPT_ATTR, call_args)
        return func

    return decorator


class MCPMixin:
    """Base mixin class for objects that can register tools, resources, and prompts
    with a FastMCP server instance using decorators.

    This mixin provides methods like ``register_all``, ``register_tools``, etc.,
    which iterate over the methods of the inheriting class, find methods
    decorated with ``@mcp_tool``, ``@mcp_resource``, or ``@mcp_prompt``, and
    register them with the provided FastMCP server instance.
    """

    def _get_methods_to_register(self, registration_type: str):
        """Retrieves all methods marked for a specific registration type."""
        return [
            (
                getattr(self, method_name),
                getattr(getattr(self, method_name), registration_type).copy(),
            )
            for method_name in dir(self)
            if callable(getattr(self, method_name))
            and hasattr(getattr(self, method_name), registration_type)
        ]

    def register_tools(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_TOOL,
    ) -> None:
        """Registers all methods marked with @mcp_tool with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register tools with.
            prefix: Optional prefix to prepend to tool names.  If provided, the
                final name will be ``f"{prefix}{separator}{original_name}"``.
            separator: The separator string used between prefix and original name.
                Defaults to ``'_'``.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_TOOL_ATTR
        ):
            if prefix:
                registration_info["name"] = (
                    f"{prefix}{separator}{registration_info['name']}"
                )

            enabled = registration_info.pop(_MIXIN_ENABLED_KEY, True)
            if enabled is False:
                continue

            tool = Tool.from_function(fn=method, **registration_info)
            mcp_server.add_tool(tool)

    def register_resources(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_RESOURCE,
    ) -> None:
        """Registers all methods marked with @mcp_resource with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register resources with.
            prefix: Optional prefix to prepend to resource names and URIs.  If
                provided, the final name will be
                ``f"{prefix}{separator}{original_name}"`` and the final URI will
                be ``f"{prefix}{separator}{original_uri}"``.
            separator: The separator string used between prefix and original
                name/URI.  Defaults to ``'+'``.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_RESOURCE_ATTR
        ):
            if prefix:
                registration_info["name"] = (
                    f"{prefix}{separator}{registration_info['name']}"
                )
                registration_info["uri"] = (
                    f"{prefix}{separator}{registration_info['uri']}"
                )

            enabled = registration_info.pop(_MIXIN_ENABLED_KEY, True)
            if enabled is False:
                continue

            resource = Resource.from_function(fn=method, **registration_info)
            mcp_server.add_resource(resource)

    def register_prompts(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_PROMPT,
    ) -> None:
        """Registers all methods marked with @mcp_prompt with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register prompts with.
            prefix: Optional prefix to prepend to prompt names.  If provided,
                the final name will be ``f"{prefix}{separator}{original_name}"``.
            separator: The separator string used between prefix and original name.
                Defaults to ``'_'``.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_PROMPT_ATTR
        ):
            if prefix:
                registration_info["name"] = (
                    f"{prefix}{separator}{registration_info['name']}"
                )

            enabled = registration_info.pop(_MIXIN_ENABLED_KEY, True)
            if enabled is False:
                continue

            prompt = Prompt.from_function(fn=method, **registration_info)
            mcp_server.add_prompt(prompt)

    def register_all(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        tool_separator: str = _DEFAULT_SEPARATOR_TOOL,
        resource_separator: str = _DEFAULT_SEPARATOR_RESOURCE,
        prompt_separator: str = _DEFAULT_SEPARATOR_PROMPT,
    ) -> None:
        """Registers all marked tools, resources, and prompts with the server.

        This method calls ``register_tools``, ``register_resources``, and
        ``register_prompts`` internally, passing the provided prefix and
        separators.

        Args:
            mcp_server: The FastMCP server instance to register with.
            prefix: Optional prefix applied to all registered items.
            tool_separator: Separator for tool names (defaults to ``'_'``).
            resource_separator: Separator for resource names/URIs (defaults to ``'+'``).
            prompt_separator: Separator for prompt names (defaults to ``'_'``).
        """
        self.register_tools(mcp_server, prefix=prefix, separator=tool_separator)
        self.register_resources(mcp_server, prefix=prefix, separator=resource_separator)
        self.register_prompts(mcp_server, prefix=prefix, separator=prompt_separator)
