"""Provides a base class and decorators for easy registration of class methods with FastMCP."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..server import FastMCP

_MCP_REGISTRATION_TOOL_ATTR = "_mcp_tool_registration"
_MCP_REGISTRATION_RESOURCE_ATTR = "_mcp_resource_registration"
_MCP_REGISTRATION_PROMPT_ATTR = "_mcp_prompt_registration"


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


class McpRegisterable:
    """Base class for objects that can register tools, resources, and prompts
    with a FastMCP server instance using decorators.
    """

    def _get_methods_to_register(self, registration_type: str):
        """Retrieves all registration info for the specified type."""

        return [
            (
                getattr(self, method_name),
                getattr(getattr(self, method_name), registration_type).copy(),
            )
            for method_name in dir(self)
            if hasattr(getattr(self, method_name), registration_type)
        ]

    def register_tools(self, mcp_server: "FastMCP", prefix: str | None = None) -> None:
        """Registers all methods marked with @mcp_tool with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register tools with.
        """

        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_TOOL_ATTR
        ):
            if prefix:
                registration_info["name"] = f"{prefix}_{registration_info['name']}"
            
            mcp_server.add_tool(fn=method, **registration_info)

    def register_resources(
        self, mcp_server: "FastMCP", prefix: str | None = None
    ) -> None:
        """Registers all methods marked with @mcp_resource with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register resources with.
        """

        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_RESOURCE_ATTR
        ):
            if prefix:
                registration_info["name"] = f"{prefix}_{registration_info['name']}"
                registration_info["uri"] = f"{prefix}+{registration_info['uri']}"
            
            mcp_server.add_resource_fn(fn=method, **registration_info)

    def register_prompts(
        self, mcp_server: "FastMCP", prefix: str | None = None
    ) -> None:
        """Registers all methods marked with @mcp_prompt with the FastMCP server.

        Args:
            mcp_server: The FastMCP server instance to register prompts with.
        """
        for method, registration_info in self._get_methods_to_register(
            _MCP_REGISTRATION_PROMPT_ATTR
        ):
            if prefix:
                registration_info["name"] = f"{prefix}_{registration_info['name']}"

            mcp_server.add_prompt(fn=method, **registration_info)

    def register_all(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        tools_prefix: str | None = None,
        resources_prefix: str | None = None,
        prompts_prefix: str | None = None,
    ) -> None:
        """Registers all marked tools, resources, and prompts."""
        self.register_tools(mcp_server, prefix=tools_prefix or prefix)
        self.register_resources(mcp_server, prefix=resources_prefix or prefix)
        self.register_prompts(mcp_server, prefix=prompts_prefix or prefix)
