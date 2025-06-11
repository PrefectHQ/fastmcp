"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import datetime
import inspect
import re
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, overload

import anyio
import httpx
import uvicorn
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import LifespanResultT, NotificationOptions
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.stdio import stdio_server
from mcp.types import (
    AnyFunction,
    EmbeddedResource,
    GetPromptResult,
    ImageContent,
    TextContent,
    ToolAnnotations,
)
from mcp.types import Prompt as MCPPrompt
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import Tool as MCPTool
from pydantic import AnyUrl
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

import fastmcp
import fastmcp.server
from fastmcp.exceptions import DisabledError, NotFoundError
from fastmcp.prompts import Prompt, PromptManager
from fastmcp.prompts.prompt import FunctionPrompt
from fastmcp.resources import Resource, ResourceManager
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth.auth import OAuthProvider
from fastmcp.server.auth.providers.bearer_env import EnvBearerAuthProvider
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.settings import Settings
from fastmcp.tools import ToolManager
from fastmcp.tools.tool import FunctionTool, Tool
from fastmcp.utilities.cache import TimedCache
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_config import MCPConfig

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.client.transports import ClientTransport, ClientTransportT
    from fastmcp.server.openapi import ComponentFn as OpenAPIComponentFn
    from fastmcp.server.openapi import FastMCPOpenAPI, RouteMap
    from fastmcp.server.openapi import RouteMapFn as OpenAPIRouteMapFn
    from fastmcp.server.proxy import FastMCPProxy
logger = get_logger(__name__)

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]

# Compiled URI parsing regex to split a URI into protocol and path components
URI_PATTERN = re.compile(r"^([^:]+://)(.*?)$")


@asynccontextmanager
async def default_lifespan(server: FastMCP[LifespanResultT]) -> AsyncIterator[Any]:
    """Default lifespan context manager that does nothing.

    Args:
        server: The server instance this lifespan is managing

    Returns:
        An empty context object
    """
    yield {}


def _lifespan_wrapper(
    app: FastMCP[LifespanResultT],
    lifespan: Callable[
        [FastMCP[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]
    ],
) -> Callable[
    [MCPServer[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]
]:
    @asynccontextmanager
    async def wrap(s: MCPServer[LifespanResultT]) -> AsyncIterator[LifespanResultT]:
        async with AsyncExitStack() as stack:
            context = await stack.enter_async_context(lifespan(app))
            yield context

    return wrap


class FastMCP(Generic[LifespanResultT]):
    def __init__(
        self,
        name: str | None = None,
        instructions: str | None = None,
        auth: OAuthProvider | None = None,
        lifespan: (
            Callable[
                [FastMCP[LifespanResultT]],
                AbstractAsyncContextManager[LifespanResultT],
            ]
            | None
        ) = None,
        tool_serializer: Callable[[Any], str] | None = None,
        cache_expiration_seconds: float | None = None,
        on_duplicate_tools: DuplicateBehavior | None = None,
        on_duplicate_resources: DuplicateBehavior | None = None,
        on_duplicate_prompts: DuplicateBehavior | None = None,
        resource_prefix_format: Literal["protocol", "path"] | None = None,
        mask_error_details: bool | None = None,
        tools: list[Tool | Callable[..., Any]] | None = None,
        dependencies: list[str] | None = None,
        include_tags: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        # ---
        # ---
        # --- The following arguments are DEPRECATED ---
        # ---
        # ---
        log_level: str | None = None,
        debug: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        sse_path: str | None = None,
        message_path: str | None = None,
        streamable_http_path: str | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
    ):
        self.resource_prefix_format: Literal["protocol", "path"] = (
            resource_prefix_format or fastmcp.settings.resource_prefix_format
        )

        self._cache = TimedCache(
            expiration=datetime.timedelta(seconds=cache_expiration_seconds or 0)
        )
        self._mounted_servers: dict[str, MountedServer] = {}
        self._additional_http_routes: list[BaseRoute] = []
        self._tool_manager = ToolManager(
            duplicate_behavior=on_duplicate_tools,
            mask_error_details=mask_error_details,
        )
        self._resource_manager = ResourceManager(
            duplicate_behavior=on_duplicate_resources,
            mask_error_details=mask_error_details,
        )
        self._prompt_manager = PromptManager(
            duplicate_behavior=on_duplicate_prompts,
            mask_error_details=mask_error_details,
        )
        self._tool_serializer = tool_serializer

        if lifespan is None:
            self._has_lifespan = False
            lifespan = default_lifespan
        else:
            self._has_lifespan = True
        self._mcp_server = MCPServer[LifespanResultT](
            name=name or "FastMCP",
            instructions=instructions,
            lifespan=_lifespan_wrapper(self, lifespan),
        )

        if auth is None and fastmcp.settings.default_auth_provider == "bearer_env":
            auth = EnvBearerAuthProvider()
        self.auth = auth

        if tools:
            for tool in tools:
                if not isinstance(tool, Tool):
                    tool = Tool.from_function(tool, serializer=self._tool_serializer)
                self.add_tool(tool)

        self.include_tags = include_tags
        self.exclude_tags = exclude_tags

        # Set up MCP protocol handlers
        self._setup_handlers()
        self.dependencies = dependencies or fastmcp.settings.server_dependencies

        # handle deprecated settings
        self._handle_deprecated_settings(
            log_level=log_level,
            debug=debug,
            host=host,
            port=port,
            sse_path=sse_path,
            message_path=message_path,
            streamable_http_path=streamable_http_path,
            json_response=json_response,
            stateless_http=stateless_http,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    def _handle_deprecated_settings(
        self,
        log_level: str | None,
        debug: bool | None,
        host: str | None,
        port: int | None,
        sse_path: str | None,
        message_path: str | None,
        streamable_http_path: str | None,
        json_response: bool | None,
        stateless_http: bool | None,
    ) -> None:
        """Handle deprecated settings. Deprecated in 2.8.0."""
        deprecated_settings: dict[str, Any] = {}

        for name, arg in [
            ("log_level", log_level),
            ("debug", debug),
            ("host", host),
            ("port", port),
            ("sse_path", sse_path),
            ("message_path", message_path),
            ("streamable_http_path", streamable_http_path),
            ("json_response", json_response),
            ("stateless_http", stateless_http),
        ]:
            if arg is not None:
                # Deprecated in 2.8.0
                warnings.warn(
                    f"Providing `{name}` when creating a server is deprecated. Provide it when calling `run` or as a global setting instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                deprecated_settings[name] = arg

        combined_settings = fastmcp.settings.model_dump() | deprecated_settings
        self._deprecated_settings = Settings(**combined_settings)

    @property
    def name(self) -> str:
        return self._mcp_server.name

    @property
    def instructions(self) -> str | None:
        return self._mcp_server.instructions

    async def run_async(
        self,
        transport: Literal["stdio", "streamable-http", "sse"] | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio", "sse", or "streamable-http")
        """
        if transport is None:
            transport = "stdio"
        if transport not in {"stdio", "streamable-http", "sse"}:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async(**transport_kwargs)
        elif transport in {"streamable-http", "sse"}:
            await self.run_http_async(transport=transport, **transport_kwargs)
        else:
            raise ValueError(f"Unknown transport: {transport}")

    def run(
        self,
        transport: Literal["stdio", "streamable-http", "sse"] | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("stdio", "sse", or "streamable-http")
        """

        anyio.run(partial(self.run_async, transport, **transport_kwargs))

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self._mcp_list_tools)
        self._mcp_server.list_resources()(self._mcp_list_resources)
        self._mcp_server.list_resource_templates()(self._mcp_list_resource_templates)
        self._mcp_server.list_prompts()(self._mcp_list_prompts)
        self._mcp_server.call_tool()(self._mcp_call_tool)
        self._mcp_server.read_resource()(self._mcp_read_resource)
        self._mcp_server.get_prompt()(self._mcp_get_prompt)

    async def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        if (tools := self._cache.get("tools")) is self._cache.NOT_FOUND:
            tools: dict[str, Tool] = {}
            for prefix, server in self._mounted_servers.items():
                try:
                    server_tools = await server.get_tools()
                    tools.update(server_tools)
                except Exception as e:
                    logger.warning(
                        f"Failed to get tools from mounted server '{prefix}': {e}"
                    )
                    continue
            tools.update(self._tool_manager.get_tools())
            self._cache.set("tools", tools)
        return tools

    async def get_tool(self, key: str) -> Tool:
        tools = await self.get_tools()
        if key not in tools:
            raise NotFoundError(f"Unknown tool: {key}")
        return tools[key]

    async def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, indexed by registered key."""
        if (resources := self._cache.get("resources")) is self._cache.NOT_FOUND:
            resources: dict[str, Resource] = {}
            for prefix, server in self._mounted_servers.items():
                try:
                    server_resources = await server.get_resources()
                    resources.update(server_resources)
                except Exception as e:
                    logger.warning(
                        f"Failed to get resources from mounted server '{prefix}': {e}"
                    )
                    continue
            resources.update(self._resource_manager.get_resources())
            self._cache.set("resources", resources)
        return resources

    async def get_resource(self, key: str) -> Resource:
        resources = await self.get_resources()
        if key not in resources:
            raise NotFoundError(f"Unknown resource: {key}")
        return resources[key]

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered resource templates, indexed by registered key."""
        if (
            templates := self._cache.get("resource_templates")
        ) is self._cache.NOT_FOUND:
            templates: dict[str, ResourceTemplate] = {}
            for prefix, server in self._mounted_servers.items():
                try:
                    server_templates = await server.get_resource_templates()
                    templates.update(server_templates)
                except Exception as e:
                    logger.warning(
                        "Failed to get resource templates from mounted server "
                        f"'{prefix}': {e}"
                    )
                    continue
            templates.update(self._resource_manager.get_templates())
            self._cache.set("resource_templates", templates)
        return templates

    async def get_resource_template(self, key: str) -> ResourceTemplate:
        templates = await self.get_resource_templates()
        if key not in templates:
            raise NotFoundError(f"Unknown resource template: {key}")
        return templates[key]

    async def get_prompts(self) -> dict[str, Prompt]:
        """
        List all available prompts.
        """
        if (prompts := self._cache.get("prompts")) is self._cache.NOT_FOUND:
            prompts: dict[str, Prompt] = {}
            for prefix, server in self._mounted_servers.items():
                try:
                    server_prompts = await server.get_prompts()
                    prompts.update(server_prompts)
                except Exception as e:
                    logger.warning(
                        f"Failed to get prompts from mounted server '{prefix}': {e}"
                    )
                    continue
            prompts.update(self._prompt_manager.get_prompts())
            self._cache.set("prompts", prompts)
        return prompts

    async def get_prompt(self, key: str) -> Prompt:
        prompts = await self.get_prompts()
        if key not in prompts:
            raise NotFoundError(f"Unknown prompt: {key}")
        return prompts[key]

    def custom_route(
        self,
        path: str,
        methods: list[str],
        name: str | None = None,
        include_in_schema: bool = True,
    ):
        """
        Decorator to register a custom HTTP route on the FastMCP server.

        Allows adding arbitrary HTTP endpoints outside the standard MCP protocol,
        which can be useful for OAuth callbacks, health checks, or admin APIs.
        The handler function must be an async function that accepts a Starlette
        Request and returns a Response.

        Args:
            path: URL path for the route (e.g., "/oauth/callback")
            methods: List of HTTP methods to support (e.g., ["GET", "POST"])
            name: Optional name for the route (to reference this route with
                Starlette's reverse URL lookup feature)
            include_in_schema: Whether to include in OpenAPI schema, defaults to True

        Example:
            @server.custom_route("/health", methods=["GET"])
            async def health_check(request: Request) -> Response:
                return JSONResponse({"status": "ok"})
        """

        def decorator(
            fn: Callable[[Request], Awaitable[Response]],
        ) -> Callable[[Request], Awaitable[Response]]:
            self._additional_http_routes.append(
                Route(
                    path,
                    endpoint=fn,
                    methods=methods,
                    name=name,
                    include_in_schema=include_in_schema,
                )
            )
            return fn

        return decorator

    async def _mcp_list_tools(self) -> list[MCPTool]:
        """
        List all available tools, in the format expected by the low-level MCP
        server.

        """
        tools = await self.get_tools()

        mcp_tools: list[MCPTool] = []
        for key, tool in tools.items():
            if self._should_enable_component(tool):
                mcp_tools.append(tool.to_mcp_tool(name=key))

        return mcp_tools

    async def _mcp_list_resources(self) -> list[MCPResource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.

        """
        resources = await self.get_resources()
        mcp_resources: list[MCPResource] = []
        for key, resource in resources.items():
            if self._should_enable_component(resource):
                mcp_resources.append(resource.to_mcp_resource(uri=key))
        return mcp_resources

    async def _mcp_list_resource_templates(self) -> list[MCPResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level
        MCP server.

        """
        templates = await self.get_resource_templates()
        mcp_templates: list[MCPResourceTemplate] = []
        for key, template in templates.items():
            if self._should_enable_component(template):
                mcp_templates.append(template.to_mcp_template(uriTemplate=key))
        return mcp_templates

    async def _mcp_list_prompts(self) -> list[MCPPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.

        """
        prompts = await self.get_prompts()
        mcp_prompts: list[MCPPrompt] = []
        for key, prompt in prompts.items():
            if self._should_enable_component(prompt):
                mcp_prompts.append(prompt.to_mcp_prompt(name=key))
        return mcp_prompts

    async def _mcp_call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """
        Handle MCP 'callTool' requests.

        Delegates to _call_tool, which should be overridden by FastMCP subclasses.

        Args:
            key: The name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            List of MCP Content objects containing the tool results
        """
        logger.debug("Call tool: %s with %s", key, arguments)

        # Create and use context for the entire call
        with fastmcp.server.context.Context(fastmcp=self):
            try:
                return await self._call_tool(key, arguments)
            except DisabledError:
                # convert to NotFoundError to avoid leaking tool presence
                raise NotFoundError(f"Unknown tool: {key}")
            except NotFoundError:
                # standardize NotFound message
                raise NotFoundError(f"Unknown tool: {key}")

    async def _call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """
        Call a tool with raw MCP arguments. FastMCP subclasses should override
        this method, not _mcp_call_tool.

        Args:
            key: The name of the tool to call arguments: Arguments to pass to
            the tool

        Returns:
            List of MCP Content objects containing the tool results
        """

        # Get tool, checking first from our tools, then from the mounted servers
        if self._tool_manager.has_tool(key):
            tool = self._tool_manager.get_tool(key)
            if not self._should_enable_component(tool):
                raise DisabledError(f"Tool {key!r} is disabled")
            return await self._tool_manager.call_tool(key, arguments)

        # Check mounted servers to see if they have the tool
        for server in self._mounted_servers.values():
            if server.match_tool(key):
                tool_key = server.strip_tool_prefix(key)
                return await server.server._call_tool(tool_key, arguments)

        raise NotFoundError(f"Unknown tool: {key!r}")

    async def _mcp_read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """
        Handle MCP 'readResource' requests.

        Delegates to _read_resource, which should be overridden by FastMCP subclasses.
        """
        logger.debug("Read resource: %s", uri)

        with fastmcp.server.context.Context(fastmcp=self):
            try:
                return await self._read_resource(uri)
            except DisabledError:
                # convert to NotFoundError to avoid leaking resource presence
                raise NotFoundError(f"Unknown resource: {str(uri)!r}")
            except NotFoundError:
                # standardize NotFound message
                raise NotFoundError(f"Unknown resource: {str(uri)!r}")

    async def _read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """
        Read a resource by URI, in the format expected by the low-level MCP
        server.
        """
        if self._resource_manager.has_resource(uri):
            resource = await self._resource_manager.get_resource(uri)
            if not self._should_enable_component(resource):
                raise DisabledError(f"Resource {str(uri)!r} is disabled")
            content = await self._resource_manager.read_resource(uri)
            return [
                ReadResourceContents(
                    content=content,
                    mime_type=resource.mime_type,
                )
            ]
        else:
            for server in self._mounted_servers.values():
                if server.match_resource(str(uri)):
                    new_uri = server.strip_resource_prefix(str(uri))
                    return await server.server._mcp_read_resource(new_uri)
            else:
                raise NotFoundError(f"Unknown resource: {uri}")

    async def _mcp_get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """
        Handle MCP 'getPrompt' requests.

        Delegates to _get_prompt, which should be overridden by FastMCP subclasses.
        """
        logger.debug("Get prompt: %s with %s", name, arguments)

        with fastmcp.server.context.Context(fastmcp=self):
            try:
                return await self._get_prompt(name, arguments)
            except DisabledError:
                # convert to NotFoundError to avoid leaking prompt presence
                raise NotFoundError(f"Unknown prompt: {name}")
            except NotFoundError:
                # standardize NotFound message
                raise NotFoundError(f"Unknown prompt: {name}")

    async def _get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """Handle MCP 'getPrompt' requests.

        Args:
            name: The name of the prompt to render
            arguments: Arguments to pass to the prompt

        Returns:
            GetPromptResult containing the rendered prompt messages
        """
        logger.debug("Get prompt: %s with %s", name, arguments)

        # Get prompt, checking first from our prompts, then from the mounted servers
        if self._prompt_manager.has_prompt(name):
            prompt = self._prompt_manager.get_prompt(name)
            if not self._should_enable_component(prompt):
                raise DisabledError(f"Prompt {name!r} is disabled")
            return await self._prompt_manager.render_prompt(name, arguments)

        # Check mounted servers to see if they have the prompt
        for server in self._mounted_servers.values():
            if server.match_prompt(name):
                prompt_name = server.strip_prompt_prefix(name)
                return await server.server._mcp_get_prompt(prompt_name, arguments)

        raise NotFoundError(f"Unknown prompt: {name}")

    def add_tool(self, tool: Tool) -> None:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            tool: The Tool instance to register
        """
        self._tool_manager.add_tool(tool)
        self._cache.clear()

    def remove_tool(self, name: str) -> None:
        """Remove a tool from the server.

        Args:
            name: The name of the tool to remove

        Raises:
            NotFoundError: If the tool is not found
        """
        self._tool_manager.remove_tool(name)
        self._cache.clear()

    @overload
    def tool(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], FunctionTool] | FunctionTool:
        """Decorator to register a tool.

        Tools can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and resource access.

        This decorator supports multiple calling patterns:
        - @server.tool (without parentheses)
        - @server.tool (with empty parentheses)
        - @server.tool("custom_name") (with name as first argument)
        - @server.tool(name="custom_name") (with name as keyword argument)
        - server.tool(function, name="custom_name") (direct function call)

        Args:
            name_or_fn: Either a function (when used as @tool), a string name, or None
            name: Optional name for the tool (keyword-only, alternative to name_or_fn)
            description: Optional description of what the tool does
            tags: Optional set of tags for categorizing the tool
            annotations: Optional annotations about the tool's behavior (e.g. {"is_async": True})
            exclude_args: Optional list of argument names to exclude from the tool schema
            enabled: Optional boolean to enable or disable the tool

        Example:
            @server.tool
            def my_tool(x: int) -> str:
                return str(x)

            @server.tool
            def my_tool(x: int) -> str:
                return str(x)

            @server.tool("custom_name")
            def my_tool(x: int) -> str:
                return str(x)

            @server.tool(name="custom_name")
            def my_tool(x: int) -> str:
                return str(x)

            # Direct function call
            server.tool(my_function, name="custom_name")
        """
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

            # Register the tool immediately and return the tool object
            tool = Tool.from_function(
                fn,
                name=tool_name,
                description=description,
                tags=tags,
                annotations=annotations,
                exclude_args=exclude_args,
                serializer=self._tool_serializer,
                enabled=enabled,
            )
            self.add_tool(tool)
            return tool

        elif isinstance(name_or_fn, str):
            # Case 3: @tool("custom_name") - name passed as first argument
            if name is not None:
                raise TypeError(
                    "Cannot specify both a name as first argument and as keyword argument. "
                    f"Use either @tool('{name_or_fn}') or @tool(name='{name}'), not both."
                )
            tool_name = name_or_fn
        elif name_or_fn is None:
            # Case 4: @tool or @tool(name="something") - use keyword name
            tool_name = name
        else:
            raise TypeError(
                f"First argument to @tool must be a function, string, or None, got {type(name_or_fn)}"
            )

        # Return partial for cases where we need to wait for the function
        return partial(
            self.tool,
            name=tool_name,
            description=description,
            tags=tags,
            annotations=annotations,
            exclude_args=exclude_args,
            enabled=enabled,
        )

    def add_resource(self, resource: Resource, key: str | None = None) -> None:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add
        """

        self._resource_manager.add_resource(resource, key=key)
        self._cache.clear()

    def add_template(self, template: ResourceTemplate, key: str | None = None) -> None:
        """Add a resource template to the server.

        Args:
            template: A ResourceTemplate instance to add
        """
        self._resource_manager.add_template(template, key=key)

    def add_resource_fn(
        self,
        fn: AnyFunction,
        uri: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> None:
        """Add a resource or template to the server from a function.

        If the URI contains parameters (e.g. "resource://{param}") or the function
        has parameters, it will be registered as a template resource.

        Args:
            fn: The function to register as a resource
            uri: The URI for the resource
            name: Optional name for the resource
            description: Optional description of the resource
            mime_type: Optional MIME type for the resource
            tags: Optional set of tags for categorizing the resource
        """
        # deprecated since 2.7.0
        warnings.warn(
            "The add_resource_fn method is deprecated. Use the resource decorator instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._resource_manager.add_resource_or_template_from_fn(
            fn=fn,
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )
        self._cache.clear()

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], Resource | ResourceTemplate]:
        """Decorator to register a function as a resource.

        The function will be called when the resource is read to generate its content.
        The function can return:
        - str for text content
        - bytes for binary content
        - other types will be converted to JSON

        Resources can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and session information.

        If the URI contains parameters (e.g. "resource://{param}") or the function
        has parameters, it will be registered as a template resource.

        Args:
            uri: URI for the resource (e.g. "resource://my-resource" or "resource://{param}")
            name: Optional name for the resource
            description: Optional description of the resource
            mime_type: Optional MIME type for the resource
            tags: Optional set of tags for categorizing the resource
            enabled: Optional boolean to enable or disable the resource

        Example:
            @server.resource("resource://my-resource")
            def get_data() -> str:
                return "Hello, world!"

            @server.resource("resource://my-resource")
            async get_data() -> str:
                data = await fetch_data()
                return f"Hello, world! {data}"

            @server.resource("resource://{city}/weather")
            def get_weather(city: str) -> str:
                return f"Weather for {city}"

            @server.resource("resource://{city}/weather")
            def get_weather_with_context(city: str, ctx: Context) -> str:
                ctx.info(f"Fetching weather for {city}")
                return f"Weather for {city}"

            @server.resource("resource://{city}/weather")
            async def get_weather(city: str) -> str:
                data = await fetch_weather(city)
                return f"Weather for {city}: {data}"
        """
        # Check if user passed function directly instead of calling decorator
        if inspect.isroutine(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "Did you forget to call it? Use @resource('uri') instead of @resource"
            )

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate:
            from fastmcp.server.context import Context

            if isinstance(fn, classmethod):  # type: ignore[reportUnnecessaryIsInstance]
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

            # Check if this should be a template
            has_uri_params = "{" in uri and "}" in uri
            # check if the function has any parameters (other than injected context)
            has_func_params = any(
                p
                for p in inspect.signature(fn).parameters.values()
                if p.annotation is not Context
            )

            if has_uri_params or has_func_params:
                template = ResourceTemplate.from_function(
                    fn=fn,
                    uri_template=uri,
                    name=name,
                    description=description,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                )
                self.add_template(template)
                return template
            elif not has_uri_params and not has_func_params:
                resource = Resource.from_function(
                    fn=fn,
                    uri=uri,
                    name=name,
                    description=description,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                )
                self.add_resource(resource)
                return resource
            else:
                raise ValueError(
                    "Invalid resource or template definition due to a "
                    "mismatch between URI parameters and function parameters."
                )

        return decorator

    def add_prompt(self, prompt: Prompt) -> None:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance to add
        """
        self._prompt_manager.add_prompt(prompt)
        self._cache.clear()

    @overload
    def prompt(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt] | FunctionPrompt:
        """Decorator to register a prompt.

        Prompts can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and session information.

        This decorator supports multiple calling patterns:
        - @server.prompt (without parentheses)
        - @server.prompt() (with empty parentheses)
        - @server.prompt("custom_name") (with name as first argument)
        - @server.prompt(name="custom_name") (with name as keyword argument)
        - server.prompt(function, name="custom_name") (direct function call)

        Args:
            name_or_fn: Either a function (when used as @prompt), a string name, or None
            name: Optional name for the prompt (keyword-only, alternative to name_or_fn)
            description: Optional description of what the prompt does
            tags: Optional set of tags for categorizing the prompt
            enabled: Optional boolean to enable or disable the prompt

        Example:
            @server.prompt
            def analyze_table(table_name: str) -> list[Message]:
                schema = read_table_schema(table_name)
                return [
                    {
                        "role": "user",
                        "content": f"Analyze this schema:\n{schema}"
                    }
                ]

            @server.prompt()
            def analyze_with_context(table_name: str, ctx: Context) -> list[Message]:
                ctx.info(f"Analyzing table {table_name}")
                schema = read_table_schema(table_name)
                return [
                    {
                        "role": "user",
                        "content": f"Analyze this schema:\n{schema}"
                    }
                ]

            @server.prompt("custom_name")
            def analyze_file(path: str) -> list[Message]:
                content = await read_file(path)
                return [
                    {
                        "role": "user",
                        "content": {
                            "type": "resource",
                            "resource": {
                                "uri": f"file://{path}",
                                "text": content
                            }
                        }
                    }
                ]

            @server.prompt(name="custom_name")
            def another_prompt(data: str) -> list[Message]:
                return [{"role": "user", "content": data}]

            # Direct function call
            server.prompt(my_function, name="custom_name")
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
            # Case 1: @prompt (without parens) - function passed directly as decorator
            # Case 2: direct call like prompt(fn, name="something")
            fn = name_or_fn
            prompt_name = name  # Use keyword name if provided, otherwise None

            # Register the prompt immediately
            prompt = Prompt.from_function(
                fn=fn,
                name=prompt_name,
                description=description,
                tags=tags,
                enabled=enabled,
            )
            self.add_prompt(prompt)

            return prompt

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
            description=description,
            tags=tags,
            enabled=enabled,
        )

    async def run_stdio_async(self) -> None:
        """Run the server using stdio transport."""
        async with stdio_server() as (read_stream, write_stream):
            logger.info(f"Starting MCP server {self.name!r} with transport 'stdio'")
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(
                    NotificationOptions(tools_changed=True)
                ),
            )

    async def run_http_async(
        self,
        transport: Literal["streamable-http", "sse"] = "streamable-http",
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        uvicorn_config: dict[str, Any] | None = None,
        middleware: list[Middleware] | None = None,
    ) -> None:
        """Run the server using HTTP transport.

        Args:
            transport: Transport protocol to use - either "streamable-http" (default) or "sse"
            host: Host address to bind to (defaults to settings.host)
            port: Port to bind to (defaults to settings.port)
            log_level: Log level for the server (defaults to settings.log_level)
            path: Path for the endpoint (defaults to settings.streamable_http_path or settings.sse_path)
            uvicorn_config: Additional configuration for the Uvicorn server
        """
        host = host or self._deprecated_settings.host
        port = port or self._deprecated_settings.port
        default_log_level_to_use = (
            log_level or self._deprecated_settings.log_level
        ).lower()

        app = self.http_app(path=path, transport=transport, middleware=middleware)

        _uvicorn_config_from_user = uvicorn_config or {}

        config_kwargs: dict[str, Any] = {
            "timeout_graceful_shutdown": 0,
            "lifespan": "on",
        }
        config_kwargs.update(_uvicorn_config_from_user)

        if "log_config" not in config_kwargs and "log_level" not in config_kwargs:
            config_kwargs["log_level"] = default_log_level_to_use

        config = uvicorn.Config(app, host=host, port=port, **config_kwargs)
        server = uvicorn.Server(config)
        path = app.state.path.lstrip("/")  # type: ignore
        logger.info(
            f"Starting MCP server {self.name!r} with transport {transport!r} on http://{host}:{port}/{path}"
        )
        await server.serve()

    async def run_sse_async(
        self,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        message_path: str | None = None,
        uvicorn_config: dict[str, Any] | None = None,
    ) -> None:
        """Run the server using SSE transport."""

        # Deprecated since 2.3.2
        warnings.warn(
            "The run_sse_async method is deprecated (as of 2.3.2). Use run_http_async for a "
            "modern (non-SSE) alternative, or create an SSE app with "
            "`fastmcp.server.http.create_sse_app` and run it directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        await self.run_http_async(
            transport="sse",
            host=host,
            port=port,
            log_level=log_level,
            path=path,
            uvicorn_config=uvicorn_config,
        )

    def sse_app(
        self,
        path: str | None = None,
        message_path: str | None = None,
        middleware: list[Middleware] | None = None,
    ) -> StarletteWithLifespan:
        """
        Create a Starlette app for the SSE server.

        Args:
            path: The path to the SSE endpoint
            message_path: The path to the message endpoint
            middleware: A list of middleware to apply to the app
        """
        # Deprecated since 2.3.2
        warnings.warn(
            "The sse_app method is deprecated (as of 2.3.2). Use http_app as a modern (non-SSE) "
            "alternative, or call `fastmcp.server.http.create_sse_app` directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        return create_sse_app(
            server=self,
            message_path=message_path or self._deprecated_settings.message_path,
            sse_path=path or self._deprecated_settings.sse_path,
            auth=self.auth,
            debug=self._deprecated_settings.debug,
            middleware=middleware,
        )

    def streamable_http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
    ) -> StarletteWithLifespan:
        """
        Create a Starlette app for the StreamableHTTP server.

        Args:
            path: The path to the StreamableHTTP endpoint
            middleware: A list of middleware to apply to the app
        """
        # Deprecated since 2.3.2
        warnings.warn(
            "The streamable_http_app method is deprecated (as of 2.3.2). Use http_app() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.http_app(path=path, middleware=middleware)

    def http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["streamable-http", "sse"] = "streamable-http",
    ) -> StarletteWithLifespan:
        """Create a Starlette app using the specified HTTP transport.

        Args:
            path: The path for the HTTP endpoint
            middleware: A list of middleware to apply to the app
            transport: Transport protocol to use - either "streamable-http" (default) or "sse"

        Returns:
            A Starlette application configured with the specified transport
        """

        if transport == "streamable-http":
            return create_streamable_http_app(
                server=self,
                streamable_http_path=path
                or self._deprecated_settings.streamable_http_path,
                event_store=None,
                auth=self.auth,
                json_response=self._deprecated_settings.json_response,
                stateless_http=self._deprecated_settings.stateless_http,
                debug=self._deprecated_settings.debug,
                middleware=middleware,
            )
        elif transport == "sse":
            return create_sse_app(
                server=self,
                message_path=self._deprecated_settings.message_path,
                sse_path=path or self._deprecated_settings.sse_path,
                auth=self.auth,
                debug=self._deprecated_settings.debug,
                middleware=middleware,
            )

    async def run_streamable_http_async(
        self,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        uvicorn_config: dict[str, Any] | None = None,
    ) -> None:
        # Deprecated since 2.3.2
        warnings.warn(
            "The run_streamable_http_async method is deprecated (as of 2.3.2). "
            "Use run_http_async instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        await self.run_http_async(
            transport="streamable-http",
            host=host,
            port=port,
            log_level=log_level,
            path=path,
            uvicorn_config=uvicorn_config,
        )

    def mount(
        self,
        prefix: str,
        server: FastMCP[LifespanResultT],
        as_proxy: bool | None = None,
        *,
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
    ) -> None:
        """Mount another FastMCP server on this server with the given prefix.

        Unlike importing (with import_server), mounting establishes a dynamic connection
        between servers. When a client interacts with a mounted server's objects through
        the parent server, requests are forwarded to the mounted server in real-time.
        This means changes to the mounted server are immediately reflected when accessed
        through the parent.

        When a server is mounted:
        - Tools from the mounted server are accessible with prefixed names.
          Example: If server has a tool named "get_weather", it will be available as "prefix_get_weather".
        - Resources are accessible with prefixed URIs.
          Example: If server has a resource with URI "weather://forecast", it will be available as
          "weather://prefix/forecast".
        - Templates are accessible with prefixed URI templates.
          Example: If server has a template with URI "weather://location/{id}", it will be available
          as "weather://prefix/location/{id}".
        - Prompts are accessible with prefixed names.
          Example: If server has a prompt named "weather_prompt", it will be available as
          "prefix_weather_prompt".

        There are two modes for mounting servers:
        1. Direct mounting (default when server has no custom lifespan): The parent server
           directly accesses the mounted server's objects in-memory for better performance.
           In this mode, no client lifecycle events occur on the mounted server, including
           lifespan execution.

        2. Proxy mounting (default when server has a custom lifespan): The parent server
           treats the mounted server as a separate entity and communicates with it via a
           Client transport. This preserves all client-facing behaviors, including lifespan
           execution, but with slightly higher overhead.

        Args:
            prefix: Prefix to use for the mounted server's objects.
            server: The FastMCP server to mount.
            as_proxy: Whether to treat the mounted server as a proxy. If None (default),
                automatically determined based on whether the server has a custom lifespan
                (True if it has a custom lifespan, False otherwise).
            tool_separator: Deprecated. Separator character for tool names.
            resource_separator: Deprecated. Separator character for resource URIs.
            prompt_separator: Deprecated. Separator character for prompt names.
        """
        from fastmcp import Client
        from fastmcp.client.transports import FastMCPTransport
        from fastmcp.server.proxy import FastMCPProxy

        if tool_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The tool_separator parameter is deprecated and will be removed in a future version. "
                "Tools are now prefixed using 'prefix_toolname' format.",
                DeprecationWarning,
                stacklevel=2,
            )

        if resource_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The resource_separator parameter is deprecated and ignored. "
                "Resource prefixes are now added using the protocol://prefix/path format.",
                DeprecationWarning,
                stacklevel=2,
            )

        if prompt_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The prompt_separator parameter is deprecated and will be removed in a future version. "
                "Prompts are now prefixed using 'prefix_promptname' format.",
                DeprecationWarning,
                stacklevel=2,
            )

        # if as_proxy is not specified and the server has a custom lifespan,
        # we should treat it as a proxy
        if as_proxy is None:
            as_proxy = server._has_lifespan

        if as_proxy and not isinstance(server, FastMCPProxy):
            server = FastMCPProxy(Client(transport=FastMCPTransport(server)))

        mounted_server = MountedServer(
            server=server,
            prefix=prefix,
        )
        self._mounted_servers[prefix] = mounted_server
        self._cache.clear()

    def unmount(self, prefix: str) -> None:
        self._mounted_servers.pop(prefix)
        self._cache.clear()

    async def import_server(
        self,
        prefix: str,
        server: FastMCP[LifespanResultT],
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
    ) -> None:
        """
        Import the MCP objects from another FastMCP server into this one,
        optionally with a given prefix.

        Note that when a server is *imported*, its objects are immediately
        registered to the importing server. This is a one-time operation and
        future changes to the imported server will not be reflected in the
        importing server. Server-level configurations and lifespans are not imported.

        When a server is imported:
        - The tools are imported with prefixed names
          Example: If server has a tool named "get_weather", it will be
          available as "prefix_get_weather"
        - The resources are imported with prefixed URIs using the new format
          Example: If server has a resource with URI "weather://forecast", it will
          be available as "weather://prefix/forecast"
        - The templates are imported with prefixed URI templates using the new format
          Example: If server has a template with URI "weather://location/{id}", it will
          be available as "weather://prefix/location/{id}"
        - The prompts are imported with prefixed names
          Example: If server has a prompt named "weather_prompt", it will be available as
          "prefix_weather_prompt"

        Args:
            prefix: The prefix to use for the imported server
            server: The FastMCP server to import
            tool_separator: Deprecated. Separator for tool names.
            resource_separator: Deprecated and ignored. Prefix is now
              applied using the protocol://prefix/path format
            prompt_separator: Deprecated. Separator for prompt names.
        """
        if tool_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The tool_separator parameter is deprecated and will be removed in a future version. "
                "Tools are now prefixed using 'prefix_toolname' format.",
                DeprecationWarning,
                stacklevel=2,
            )

        if resource_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The resource_separator parameter is deprecated and ignored. "
                "Resource prefixes are now added using the protocol://prefix/path format.",
                DeprecationWarning,
                stacklevel=2,
            )

        if prompt_separator is not None:
            # Deprecated since 2.4.0
            warnings.warn(
                "The prompt_separator parameter is deprecated and will be removed in a future version. "
                "Prompts are now prefixed using 'prefix_promptname' format.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Import tools from the mounted server
        tool_prefix = f"{prefix}_"
        for key, tool in (await server.get_tools()).items():
            self._tool_manager.add_tool(tool, key=f"{tool_prefix}{key}")

        # Import resources and templates from the mounted server
        for key, resource in (await server.get_resources()).items():
            prefixed_key = add_resource_prefix(key, prefix, self.resource_prefix_format)
            self._resource_manager.add_resource(resource, key=prefixed_key)

        for key, template in (await server.get_resource_templates()).items():
            prefixed_key = add_resource_prefix(key, prefix, self.resource_prefix_format)
            self._resource_manager.add_template(template, key=prefixed_key)

        # Import prompts from the mounted server
        prompt_prefix = f"{prefix}_"
        for key, prompt in (await server.get_prompts()).items():
            self._prompt_manager.add_prompt(prompt, key=f"{prompt_prefix}{key}")

        logger.info(f"Imported server {server.name} with prefix '{prefix}'")
        logger.debug(f"Imported tools with prefix '{tool_prefix}'")
        logger.debug(f"Imported resources and templates with prefix '{prefix}/'")
        logger.debug(f"Imported prompts with prefix '{prompt_prefix}'")

        self._cache.clear()

    @classmethod
    def from_openapi(
        cls,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: OpenAPIRouteMapFn | None = None,
        mcp_component_fn: OpenAPIComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        **settings: Any,
    ) -> FastMCPOpenAPI:
        """
        Create a FastMCP server from an OpenAPI specification.
        """
        from .openapi import FastMCPOpenAPI

        return FastMCPOpenAPI(
            openapi_spec=openapi_spec,
            client=client,
            route_maps=route_maps,
            route_map_fn=route_map_fn,
            mcp_component_fn=mcp_component_fn,
            mcp_names=mcp_names,
            tags=tags,
            **settings,
        )

    @classmethod
    def from_fastapi(
        cls,
        app: Any,
        name: str | None = None,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: OpenAPIRouteMapFn | None = None,
        mcp_component_fn: OpenAPIComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        httpx_client_kwargs: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        **settings: Any,
    ) -> FastMCPOpenAPI:
        """
        Create a FastMCP server from a FastAPI application.
        """

        from .openapi import FastMCPOpenAPI

        if httpx_client_kwargs is None:
            httpx_client_kwargs = {}
        httpx_client_kwargs.setdefault("base_url", "http://fastapi")

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            **httpx_client_kwargs,
        )

        name = name or app.title

        return FastMCPOpenAPI(
            openapi_spec=app.openapi(),
            client=client,
            name=name,
            route_maps=route_maps,
            route_map_fn=route_map_fn,
            mcp_component_fn=mcp_component_fn,
            mcp_names=mcp_names,
            tags=tags,
            **settings,
        )

    @classmethod
    def as_proxy(
        cls,
        backend: Client[ClientTransportT]
        | ClientTransport
        | FastMCP[Any]
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str,
        **settings: Any,
    ) -> FastMCPProxy:
        """Create a FastMCP proxy server for the given backend.

        The ``backend`` argument can be either an existing :class:`~fastmcp.client.Client`
        instance or any value accepted as the ``transport`` argument of
        :class:`~fastmcp.client.Client`. This mirrors the convenience of the
        ``Client`` constructor.
        """
        from fastmcp.client.client import Client
        from fastmcp.server.proxy import FastMCPProxy

        if isinstance(backend, Client):
            client = backend
        else:
            client = Client(backend)

        return FastMCPProxy(client=client, **settings)

    @classmethod
    def from_client(
        cls, client: Client[ClientTransportT], **settings: Any
    ) -> FastMCPProxy:
        """
        Create a FastMCP proxy server from a FastMCP client.
        """
        # Deprecated since 2.3.5
        warnings.warn(
            "FastMCP.from_client() is deprecated; use FastMCP.as_proxy() instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        return cls.as_proxy(client, **settings)

    def _should_enable_component(
        self,
        component: FastMCPComponent,
    ) -> bool:
        """
        Given a component, determine if it should be enabled. Returns True if it should be enabled; False if it should not.

        Rules:
            • If the component's enabled property is False, always return False.
            • If both include_tags and exclude_tags are None, return True.
            • If exclude_tags is provided, check each exclude tag:
                - If the exclude tag is a string, it must be present in the input tags to exclude.
            • If include_tags is provided, check each include tag:
                - If the include tag is a string, it must be present in the input tags to include.
            • If include_tags is provided and none of the include tags match, return False.
            • If include_tags is not provided, return True.
        """
        if not component.enabled:
            return False

        if self.include_tags is None and self.exclude_tags is None:
            return True

        if self.exclude_tags is not None:
            if any(etag in component.tags for etag in self.exclude_tags):
                return False

        if self.include_tags is not None:
            if any(itag in component.tags for itag in self.include_tags):
                return True
            else:
                return False

        return True


class MountedServer:
    def __init__(
        self,
        prefix: str,
        server: FastMCP[LifespanResultT],
    ):
        self.server = server
        self.prefix = prefix

    async def get_tools(self) -> dict[str, Tool]:
        tools = await self.server.get_tools()
        return {f"{self.prefix}_{key}": tool for key, tool in tools.items()}

    async def get_resources(self) -> dict[str, Resource]:
        resources = await self.server.get_resources()
        return {
            add_resource_prefix(
                key, self.prefix, self.server.resource_prefix_format
            ): resource
            for key, resource in resources.items()
        }

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        templates = await self.server.get_resource_templates()
        return {
            add_resource_prefix(
                key, self.prefix, self.server.resource_prefix_format
            ): template
            for key, template in templates.items()
        }

    async def get_prompts(self) -> dict[str, Prompt]:
        prompts = await self.server.get_prompts()
        return {f"{self.prefix}_{key}": prompt for key, prompt in prompts.items()}

    def match_tool(self, key: str) -> bool:
        return key.startswith(f"{self.prefix}_")

    def strip_tool_prefix(self, key: str) -> str:
        return key.removeprefix(f"{self.prefix}_")

    def match_resource(self, key: str) -> bool:
        return has_resource_prefix(key, self.prefix, self.server.resource_prefix_format)

    def strip_resource_prefix(self, key: str) -> str:
        return remove_resource_prefix(
            key, self.prefix, self.server.resource_prefix_format
        )

    def match_prompt(self, key: str) -> bool:
        return key.startswith(f"{self.prefix}_")

    def strip_prompt_prefix(self, key: str) -> str:
        return key.removeprefix(f"{self.prefix}_")


def add_resource_prefix(
    uri: str, prefix: str, prefix_format: Literal["protocol", "path"] | None = None
) -> str:
    """Add a prefix to a resource URI.

    Args:
        uri: The original resource URI
        prefix: The prefix to add

    Returns:
        The resource URI with the prefix added

    Examples:
        >>> add_resource_prefix("resource://path/to/resource", "prefix")
        "resource://prefix/path/to/resource"  # with new style
        >>> add_resource_prefix("resource://path/to/resource", "prefix")
        "prefix+resource://path/to/resource"  # with legacy style
        >>> add_resource_prefix("resource:///absolute/path", "prefix")
        "resource://prefix//absolute/path"  # with new style

    Raises:
        ValueError: If the URI doesn't match the expected protocol://path format
    """
    if not prefix:
        return uri

    # Get the server settings to check for legacy format preference

    if prefix_format is None:
        prefix_format = fastmcp.settings.resource_prefix_format

    if prefix_format == "protocol":
        # Legacy style: prefix+protocol://path
        return f"{prefix}+{uri}"
    elif prefix_format == "path":
        # New style: protocol://prefix/path
        # Split the URI into protocol and path
        match = URI_PATTERN.match(uri)
        if not match:
            raise ValueError(
                f"Invalid URI format: {uri}. Expected protocol://path format."
            )

        protocol, path = match.groups()

        # Add the prefix to the path
        return f"{protocol}{prefix}/{path}"
    else:
        raise ValueError(f"Invalid prefix format: {prefix_format}")


def remove_resource_prefix(
    uri: str, prefix: str, prefix_format: Literal["protocol", "path"] | None = None
) -> str:
    """Remove a prefix from a resource URI.

    Args:
        uri: The resource URI with a prefix
        prefix: The prefix to remove
        prefix_format: The format of the prefix to remove
    Returns:
        The resource URI with the prefix removed

    Examples:
        >>> remove_resource_prefix("resource://prefix/path/to/resource", "prefix")
        "resource://path/to/resource"  # with new style
        >>> remove_resource_prefix("prefix+resource://path/to/resource", "prefix")
        "resource://path/to/resource"  # with legacy style
        >>> remove_resource_prefix("resource://prefix//absolute/path", "prefix")
        "resource:///absolute/path"  # with new style

    Raises:
        ValueError: If the URI doesn't match the expected protocol://path format
    """
    if not prefix:
        return uri

    if prefix_format is None:
        prefix_format = fastmcp.settings.resource_prefix_format

    if prefix_format == "protocol":
        # Legacy style: prefix+protocol://path
        legacy_prefix = f"{prefix}+"
        if uri.startswith(legacy_prefix):
            return uri[len(legacy_prefix) :]
        return uri
    elif prefix_format == "path":
        # New style: protocol://prefix/path
        # Split the URI into protocol and path
        match = URI_PATTERN.match(uri)
        if not match:
            raise ValueError(
                f"Invalid URI format: {uri}. Expected protocol://path format."
            )

        protocol, path = match.groups()

        # Check if the path starts with the prefix followed by a /
        prefix_pattern = f"^{re.escape(prefix)}/(.*?)$"
        path_match = re.match(prefix_pattern, path)
        if not path_match:
            return uri

        # Return the URI without the prefix
        return f"{protocol}{path_match.group(1)}"
    else:
        raise ValueError(f"Invalid prefix format: {prefix_format}")


def has_resource_prefix(
    uri: str, prefix: str, prefix_format: Literal["protocol", "path"] | None = None
) -> bool:
    """Check if a resource URI has a specific prefix.

    Args:
        uri: The resource URI to check
        prefix: The prefix to look for

    Returns:
        True if the URI has the specified prefix, False otherwise

    Examples:
        >>> has_resource_prefix("resource://prefix/path/to/resource", "prefix")
        True  # with new style
        >>> has_resource_prefix("prefix+resource://path/to/resource", "prefix")
        True  # with legacy style
        >>> has_resource_prefix("resource://other/path/to/resource", "prefix")
        False

    Raises:
        ValueError: If the URI doesn't match the expected protocol://path format
    """
    if not prefix:
        return False

    # Get the server settings to check for legacy format preference

    if prefix_format is None:
        prefix_format = fastmcp.settings.resource_prefix_format

    if prefix_format == "protocol":
        # Legacy style: prefix+protocol://path
        legacy_prefix = f"{prefix}+"
        return uri.startswith(legacy_prefix)
    elif prefix_format == "path":
        # New style: protocol://prefix/path
        # Split the URI into protocol and path
        match = URI_PATTERN.match(uri)
        if not match:
            raise ValueError(
                f"Invalid URI format: {uri}. Expected protocol://path format."
            )

        _, path = match.groups()

        # Check if the path starts with the prefix followed by a /
        prefix_pattern = f"^{re.escape(prefix)}/"
        return bool(re.match(prefix_pattern, path))
    else:
        raise ValueError(f"Invalid prefix format: {prefix_format}")
