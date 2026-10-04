"""A middleware for injecting tools into the MCP server context."""

import warnings
from collections.abc import Sequence
from logging import Logger
from typing import Annotated, Any

import mcp.types
from mcp.types import Prompt
from pydantic import AnyUrl

import fastmcp
from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.resources.base import ResourceResult
from fastmcp.server.context import Context
from fastmcp.server.middleware.middleware import Middleware
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger

logger: Logger = get_logger(name=__name__)


class ToolInjectionMiddleware(Middleware):
    """A middleware for injecting tools into the context.

    The server this middleware is added to lists and calls the injected tools
    alongside its own. They are resolved by the server's tool lookup, so a
    tool's `auth` check and every middleware in the chain, including
    `AuthMiddleware`, apply to them as they do to registered tools, wherever
    this middleware sits in the chain. Server and session visibility settings
    do not apply to injected tools.

    An injected tool takes precedence over a registered tool with the same
    name. When several injection middleware provide the same name, the one
    added first takes precedence.
    """

    def __init__(self, tools: Sequence[Tool]):
        """Initialize the tool injection middleware."""
        self._tools_to_inject: Sequence[Tool] = tools
        self._tools_to_inject_by_name: dict[str, Tool] = {
            tool.name: tool for tool in tools
        }

    @property
    def injected_tools(self) -> Sequence[Tool]:
        """The tools this middleware injects, in the order they were given."""
        return self._tools_to_inject

    def get_injected_tool(self, name: str) -> Tool | None:
        """Return the injected tool with this name, if there is one."""
        return self._tools_to_inject_by_name.get(name)


async def list_prompts(context: Context) -> list[Prompt]:
    """List prompts available on the server."""
    return await context.list_prompts()


list_prompts_tool = Tool.from_function(
    fn=list_prompts,
)


async def get_prompt(
    context: Context,
    name: Annotated[str, "The name of the prompt to render."],
    arguments: Annotated[
        dict[str, Any] | None, "The arguments to pass to the prompt."
    ] = None,
) -> mcp.types.GetPromptResult:
    """Render a prompt available on the server."""
    return await context.get_prompt(name=name, arguments=arguments)


get_prompt_tool = Tool.from_function(
    fn=get_prompt,
)


class PromptToolMiddleware(ToolInjectionMiddleware):
    """A middleware for injecting prompts as tools into the context.

    .. deprecated::
        Use ``fastmcp.server.transforms.PromptsAsTools`` instead.
    """

    def __init__(self) -> None:
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "PromptToolMiddleware is deprecated. Use the PromptsAsTools transform instead: "
                "from fastmcp.server.transforms import PromptsAsTools",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        tools: list[Tool] = [list_prompts_tool, get_prompt_tool]
        super().__init__(tools=tools)


async def list_resources(context: Context) -> list[mcp.types.Resource]:
    """List resources available on the server."""
    return await context.list_resources()


list_resources_tool = Tool.from_function(
    fn=list_resources,
)


async def read_resource(
    context: Context,
    uri: Annotated[AnyUrl | str, "The URI of the resource to read."],
) -> ResourceResult:
    """Read a resource available on the server."""
    return await context.read_resource(uri=uri)


read_resource_tool = Tool.from_function(
    fn=read_resource,
)


class ResourceToolMiddleware(ToolInjectionMiddleware):
    """A middleware for injecting resources as tools into the context.

    .. deprecated::
        Use ``fastmcp.server.transforms.ResourcesAsTools`` instead.
    """

    def __init__(self) -> None:
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "ResourceToolMiddleware is deprecated. Use the ResourcesAsTools transform instead: "
                "from fastmcp.server.transforms import ResourcesAsTools",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        tools: list[Tool] = [list_resources_tool, read_resource_tool]
        super().__init__(tools=tools)
