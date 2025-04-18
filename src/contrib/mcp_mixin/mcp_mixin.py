"""Provides a base mixin class and decorators for easy registration of class methods with FastMCP."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp.server import FastMCP

_MCP_REGISTRATION_TOOL_ATTR = "_mcp_tool_registration"
_MCP_REGISTRATION_RESOURCE_ATTR = "_mcp_resource_registration"
_MCP_REGISTRATION_PROMPT_ATTR = "_mcp_prompt_registration"

_DEFAULT_SEPARATOR_TOOL = "_"
_DEFAULT_SEPARATOR_RESOURCE = "+"
_DEFAULT_SEPARATOR_PROMPT = "_"


def mcp_tool(
    name: str | None = None,
    description: str | None = None,
    tags: set[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP tool for later registration."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args = {
            "name": name or func.__name__,
            "description": description,
            "tags": tags,
        }
        call_args = {k: v for k, v in call_args.items() if v is not None}
        setattr(func, _MCP_REGISTRATION_TOOL_ATTR, call_args)
        return func

    return decorator


def mcp_resource(
    uri: str,
    *,
    name: str | None = None,
    description: str | None = None,
    mime_type: str | None = None,
    tags: set[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP resource for later registration."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args = {
            "uri": uri,
            "name": name or func.__name__,
            "description": description,
            "mime_type": mime_type,
            "tags": tags,
        }
        call_args = {k: v for k, v in call_args.items() if v is not None}

        setattr(func, _MCP_REGISTRATION_RESOURCE_ATTR, call_args)

        return func

    return decorator


def mcp_prompt(
    name: str | None = None,
    description: str | None = None,
    tags: set[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a method as an MCP prompt for later registration."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_args = {
            "name": name or func.__name__,
            "description": description,
            "tags": tags,
        }

        call_args = {k: v for k, v in call_args.items() if v is not None}

        setattr(func, _MCP_REGISTRATION_PROMPT_ATTR, call_args)
        return func

    return decorator


class MCPMixin:
    """Base mixin class for objects that can register tools, resources, and prompts
    with a FastMCP server instance using decorators.

    This mixin provides methods like `register_all`, `register_tools`, etc.,
    which iterate over the methods of the inheriting class, find methods
    decorated with `@mcp_tool`, `@mcp_resource`, or `@mcp_prompt`, and
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
            prefix: Optional prefix to prepend to tool names. If provided, the
                final name will be f"{prefix}{separator}{original_name}".
            separator: The separator string used between prefix and original name.
                Defaults to '_'.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_TOOL_ATTR
        ):
            if prefix:
                registration_info["name"] = (
                    f"{prefix}{separator}{registration_info['name']}"
                )
            mcp_server.add_tool(fn=method, **registration_info)

    def register_resources(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_RESOURCE,
    ) -> None:
        """Registers all methods marked with @mcp_resource with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register resources with.
            prefix: Optional prefix to prepend to resource names and URIs. If provided,
                the final name will be f"{prefix}{separator}{original_name}" and the
                final URI will be f"{prefix}{separator}{original_uri}".
            separator: The separator string used between prefix and original name/URI.
                Defaults to '+'.
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
            mcp_server.add_resource_fn(fn=method, **registration_info)

    def register_prompts(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_PROMPT,
    ) -> None:
        """Registers all methods marked with @mcp_prompt with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register prompts with.
            prefix: Optional prefix to prepend to prompt names. If provided, the
                final name will be f"{prefix}{separator}{original_name}".
            separator: The separator string used between prefix and original name.
                Defaults to '_'.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_PROMPT_ATTR
        ):
            if prefix:
                registration_info["name"] = (
                    f"{prefix}{separator}{registration_info['name']}"
                )
            mcp_server.add_prompt(fn=method, **registration_info)

    def register_all(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        tool_separator: str = _DEFAULT_SEPARATOR_TOOL,
        resource_separator: str = _DEFAULT_SEPARATOR_RESOURCE,
        prompt_separator: str = _DEFAULT_SEPARATOR_PROMPT,
    ) -> None:
        """Registers all marked tools, resources, and prompts with the server.

        This method calls `register_tools`, `register_resources`, and `register_prompts`
        internally, passing the provided prefix and separators.

        Args:
            mcp_server: The FastMCP server instance to register with.
            prefix: Optional prefix applied to all registered items unless overridden
                by a specific separator argument.
            tool_separator: Separator for tool names (defaults to '_').
            resource_separator: Separator for resource names/URIs (defaults to '+').
            prompt_separator: Separator for prompt names (defaults to '_').
        """
        self.register_tools(mcp_server, prefix=prefix, separator=tool_separator)
        self.register_resources(mcp_server, prefix=prefix, separator=resource_separator)
        self.register_prompts(mcp_server, prefix=prefix, separator=prompt_separator)
