"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import datetime
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from functools import partial
from typing import TYPE_CHECKING, Any, Generic, Literal

import anyio
import httpx
import pydantic
import uvicorn
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
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
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

import fastmcp.server
import fastmcp.settings
from fastmcp.exceptions import NotFoundError
from fastmcp.prompts import Prompt, PromptManager
from fastmcp.prompts.prompt import PromptResult
from fastmcp.resources import Resource, ResourceManager
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.http import create_sse_app
from fastmcp.tools import ToolManager
from fastmcp.tools.tool import Tool
from fastmcp.utilities.cache import TimedCache
from fastmcp.utilities.decorators import DecoratedFunction
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.server.openapi import FastMCPOpenAPI
    from fastmcp.server.proxy import FastMCPProxy

logger = get_logger(__name__)

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]


@asynccontextmanager
async def default_lifespan(server: FastMCP) -> AsyncIterator[Any]:
    """Default lifespan context manager that does nothing.

    Args:
        server: The server instance this lifespan is managing

    Returns:
        An empty context object
    """
    yield {}


def _lifespan_wrapper(
    app: FastMCP,
    lifespan: Callable[[FastMCP], AbstractAsyncContextManager[LifespanResultT]],
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
        auth_server_provider: OAuthAuthorizationServerProvider[Any, Any, Any]
        | None = None,
        lifespan: (
            Callable[
                [FastMCP[LifespanResultT]],
                AbstractAsyncContextManager[LifespanResultT],
            ]
            | None
        ) = None,
        tags: set[str] | None = None,
        dependencies: list[str] | None = None,
        tool_serializer: Callable[[Any], str] | None = None,
        cache_expiration_seconds: float | None = None,
        on_duplicate_tools: DuplicateBehavior | None = None,
        on_duplicate_resources: DuplicateBehavior | None = None,
        on_duplicate_prompts: DuplicateBehavior | None = None,
        **settings: Any,
    ):
        if settings:
            # TODO: remove settings. Deprecated since 2.3.4
            warnings.warn(
                "Passing runtime and transport-specific settings as kwargs "
                "to the FastMCP constructor is deprecated (as of 2.3.4), "
                "including most transport settings. If possible, provide settings when calling "
                "run() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self.settings = fastmcp.settings.ServerSettings(**settings)

        self.tags: set[str] = tags or set()
        self.dependencies = dependencies
        self._cache = TimedCache(
            expiration=datetime.timedelta(seconds=cache_expiration_seconds or 0)
        )
        self._mounted_servers: dict[str, MountedServer] = {}
        self._additional_http_routes: list[BaseRoute] = []
        self._tool_manager = ToolManager(
            duplicate_behavior=on_duplicate_tools,
            serializer=tool_serializer,
        )
        self._resource_manager = ResourceManager(
            duplicate_behavior=on_duplicate_resources
        )
        self._prompt_manager = PromptManager(duplicate_behavior=on_duplicate_prompts)

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

        if (self.settings.auth is not None) != (auth_server_provider is not None):
            # TODO: after we support separate authorization servers (see
            raise ValueError(
                "settings.auth must be specified if and only if auth_server_provider "
                "is specified"
            )
        self._auth_server_provider = auth_server_provider

        # Set up MCP protocol handlers
        self._setup_handlers()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

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
        if transport not in ["stdio", "streamable-http", "sse"]:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async(**transport_kwargs)
        elif transport == "streamable-http":
            await self.run_http_async(transport="streamable-http", **transport_kwargs)
        elif transport == "sse":
            await self.run_http_async(transport="sse", **transport_kwargs)
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
        logger.info(f'Starting server "{self.name}"...')

        anyio.run(partial(self.run_async, transport, **transport_kwargs))

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self._mcp_list_tools)
        self._mcp_server.call_tool()(self._mcp_call_tool)
        self._mcp_server.list_resources()(self._mcp_list_resources)
        self._mcp_server.read_resource()(self._mcp_read_resource)
        self._mcp_server.list_prompts()(self._mcp_list_prompts)
        self._mcp_server.get_prompt()(self._mcp_get_prompt)
        self._mcp_server.list_resource_templates()(self._mcp_list_resource_templates)

    async def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        if (tools := self._cache.get("tools")) is self._cache.NOT_FOUND:
            tools = {}
            for server in self._mounted_servers.values():
                server_tools = await server.get_tools()
                tools.update(server_tools)
            tools.update(self._tool_manager.get_tools())
            self._cache.set("tools", tools)
        return tools

    async def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, indexed by registered key."""
        if (resources := self._cache.get("resources")) is self._cache.NOT_FOUND:
            resources = {}
            for server in self._mounted_servers.values():
                server_resources = await server.get_resources()
                resources.update(server_resources)
            resources.update(self._resource_manager.get_resources())
            self._cache.set("resources", resources)
        return resources

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered resource templates, indexed by registered key."""
        if (
            templates := self._cache.get("resource_templates")
        ) is self._cache.NOT_FOUND:
            templates = {}
            for server in self._mounted_servers.values():
                server_templates = await server.get_resource_templates()
                templates.update(server_templates)
            templates.update(self._resource_manager.get_templates())
            self._cache.set("resource_templates", templates)
        return templates

    async def get_prompts(self) -> dict[str, Prompt]:
        """
        List all available prompts.
        """
        if (prompts := self._cache.get("prompts")) is self._cache.NOT_FOUND:
            prompts = {}
            for server in self._mounted_servers.values():
                server_prompts = await server.get_prompts()
                prompts.update(server_prompts)
            prompts.update(self._prompt_manager.get_prompts())
            self._cache.set("prompts", prompts)
        return prompts

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
            func: Callable[[Request], Awaitable[Response]],
        ) -> Callable[[Request], Awaitable[Response]]:
            self._additional_http_routes.append(
                Route(
                    path,
                    endpoint=func,
                    methods=methods,
                    name=name,
                    include_in_schema=include_in_schema,
                )
            )
            return func

        return decorator

    async def _mcp_list_tools(self) -> list[MCPTool]:
        """
        List all available tools, in the format expected by the low-level MCP
        server.

        """
        tools = await self.get_tools()
        return [tool.to_mcp_tool(name=key) for key, tool in tools.items()]

    async def _mcp_list_resources(self) -> list[MCPResource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.

        """
        resources = await self.get_resources()
        return [
            resource.to_mcp_resource(uri=key) for key, resource in resources.items()
        ]

    async def _mcp_list_resource_templates(self) -> list[MCPResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level
        MCP server.

        """
        templates = await self.get_resource_templates()
        return [
            template.to_mcp_template(uriTemplate=key)
            for key, template in templates.items()
        ]

    async def _mcp_list_prompts(self) -> list[MCPPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.

        """
        prompts = await self.get_prompts()
        return [prompt.to_mcp_prompt(name=key) for key, prompt in prompts.items()]

    async def _mcp_call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Call a tool by name with arguments."""

        with fastmcp.server.context.Context(fastmcp=self):
            if self._tool_manager.has_tool(key):
                result = await self._tool_manager.call_tool(key, arguments)

            else:
                for server in self._mounted_servers.values():
                    if server.match_tool(key):
                        new_key = server.strip_tool_prefix(key)
                        result = await server.server._mcp_call_tool(new_key, arguments)
                        break
                else:
                    raise NotFoundError(f"Unknown tool: {key}")
            return result

    async def _mcp_read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """
        Read a resource by URI, in the format expected by the low-level MCP
        server.
        """
        with fastmcp.server.context.Context(fastmcp=self):
            if self._resource_manager.has_resource(uri):
                resource = await self._resource_manager.get_resource(uri)
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
        Get a prompt by name with arguments, in the format expected by the low-level
        MCP server.

        """
        with fastmcp.server.context.Context(fastmcp=self):
            if self._prompt_manager.has_prompt(name):
                prompt_result = await self._prompt_manager.render_prompt(
                    name, arguments=arguments or {}
                )
                return prompt_result
            else:
                for server in self._mounted_servers.values():
                    if server.match_prompt(name):
                        new_key = server.strip_prompt_prefix(name)
                        return await server.server._mcp_get_prompt(new_key, arguments)
                else:
                    raise NotFoundError(f"Unknown prompt: {name}")

    def add_tool(
        self,
        fn: AnyFunction,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
    ) -> None:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            fn: The function to register as a tool
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does
            tags: Optional set of tags for categorizing the tool
            annotations: Optional annotations about the tool's behavior
        """
        if isinstance(annotations, dict):
            annotations = ToolAnnotations(**annotations)

        self._tool_manager.add_tool_from_fn(
            fn,
            name=name,
            description=description,
            tags=tags,
            annotations=annotations,
        )
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

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a tool.

        Tools can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and resource access.

        Args:
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does
            tags: Optional set of tags for categorizing the tool
            annotations: Optional annotations about the tool's behavior

        Example:
            @server.tool()
            def my_tool(x: int) -> str:
                return str(x)

            @server.tool()
            def tool_with_context(x: int, ctx: Context) -> str:
                ctx.info(f"Processing {x}")
                return str(x)

            @server.tool()
            async def async_tool(x: int, context: Context) -> str:
                await context.report_progress(50, 100)
                return str(x)
        """

        # Check if user passed function directly instead of calling decorator
        if callable(name):
            raise TypeError(
                "The @tool decorator was used incorrectly. "
                "Did you forget to call it? Use @tool() instead of @tool"
            )

        def decorator(fn: AnyFunction) -> AnyFunction:
            self.add_tool(
                fn,
                name=name,
                description=description,
                tags=tags,
                annotations=annotations,
            )
            return fn

        return decorator

    def add_resource(self, resource: Resource, key: str | None = None) -> None:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add
        """

        self._resource_manager.add_resource(resource, key=key)
        self._cache.clear()

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
    ) -> Callable[[AnyFunction], AnyFunction]:
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
        if callable(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "Did you forget to call it? Use @resource('uri') instead of @resource"
            )

        def decorator(fn: AnyFunction) -> AnyFunction:
            self.add_resource_fn(
                fn=fn,
                uri=uri,
                name=name,
                description=description,
                mime_type=mime_type,
                tags=tags,
            )
            return fn

        return decorator

    def add_prompt(
        self,
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> None:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance to add
        """
        self._prompt_manager.add_prompt_from_fn(
            fn=fn,
            name=name,
            description=description,
            tags=tags,
        )
        self._cache.clear()

    def prompt(
        self,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a prompt.

        Prompts can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and session information.

        Args:
            name: Optional name for the prompt (defaults to function name)
            description: Optional description of what the prompt does
            tags: Optional set of tags for categorizing the prompt

        Example:
            @server.prompt()
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

            @server.prompt()
            async def analyze_file(path: str) -> list[Message]:
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
        """
        # Check if user passed function directly instead of calling decorator
        if callable(name):
            raise TypeError(
                "The @prompt decorator was used incorrectly. "
                "Did you forget to call it? Use @prompt() instead of @prompt"
            )

        def decorator(func: AnyFunction) -> AnyFunction:
            self.add_prompt(func, name=name, description=description, tags=tags)
            return DecoratedFunction(func)

        return decorator

    async def run_stdio_async(self) -> None:
        """Run the server using stdio transport."""
        async with stdio_server() as (read_stream, write_stream):
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
        uvicorn_config: dict | None = None,
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
        uvicorn_config = uvicorn_config or {}
        uvicorn_config.setdefault("timeout_graceful_shutdown", 0)
        # lifespan is required for streamable http
        uvicorn_config["lifespan"] = "on"

        app = self.http_app(path=path, transport=transport)

        config = uvicorn.Config(
            app,
            host=host or self.settings.host,
            port=port or self.settings.port,
            log_level=log_level or self.settings.log_level.lower(),
            **uvicorn_config,
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def run_sse_async(
        self,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        message_path: str | None = None,
        uvicorn_config: dict | None = None,
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
    ) -> Starlette:
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
            message_path=message_path or self.settings.message_path,
            sse_path=path or self.settings.sse_path,
            auth_server_provider=self._auth_server_provider,
            auth_settings=self.settings.auth,
            debug=self.settings.debug,
            routes=self._additional_http_routes,
            middleware=middleware,
        )

    def streamable_http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
    ) -> Starlette:
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
        transport: Literal["streamable-http", "sse"] = "streamable-http",
    ) -> Starlette:
        """Create a Starlette app using the specified HTTP transport.

        Args:
            path: The path for the HTTP endpoint
            middleware: A list of middleware to apply to the app
            transport: Transport protocol to use - either "streamable-http" (default) or "sse"

        Returns:
            A Starlette application configured with the specified transport
        """
        from fastmcp.server.http import create_streamable_http_app

        if transport == "streamable-http":
            return create_streamable_http_app(
                server=self,
                streamable_http_path=path or self.settings.streamable_http_path,
                event_store=None,
                auth_server_provider=self._auth_server_provider,
                auth_settings=self.settings.auth,
                json_response=self.settings.json_response,
                stateless_http=self.settings.stateless_http,
                debug=self.settings.debug,
                routes=self._additional_http_routes,
                middleware=middleware,
            )
        elif transport == "sse":
            return create_sse_app(
                server=self,
                message_path=self.settings.message_path,
                sse_path=path or self.settings.sse_path,
                auth_server_provider=self._auth_server_provider,
                auth_settings=self.settings.auth,
                debug=self.settings.debug,
                routes=self._additional_http_routes,
                middleware=middleware,
            )

    async def run_streamable_http_async(
        self,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        uvicorn_config: dict | None = None,
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
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
        as_proxy: bool | None = None,
    ) -> None:
        """Mount another FastMCP server on this server with the given prefix.

        Unlike importing (with import_server), mounting establishes a dynamic connection
        between servers. When a client interacts with a mounted server's objects through
        the parent server, requests are forwarded to the mounted server in real-time.
        This means changes to the mounted server are immediately reflected when accessed
        through the parent.

        When a server is mounted:
        - Tools from the mounted server are accessible with prefixed names using the tool_separator.
          Example: If server has a tool named "get_weather", it will be available as "prefix_get_weather".
        - Resources are accessible with prefixed URIs using the resource_separator.
          Example: If server has a resource with URI "weather://forecast", it will be available as
          "prefix+weather://forecast".
        - Templates are accessible with prefixed URI templates using the resource_separator.
          Example: If server has a template with URI "weather://location/{id}", it will be available
          as "prefix+weather://location/{id}".
        - Prompts are accessible with prefixed names using the prompt_separator.
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
            tool_separator: Separator character for tool names (defaults to "_").
            resource_separator: Separator character for resource URIs (defaults to "+").
            prompt_separator: Separator character for prompt names (defaults to "_").
            as_proxy: Whether to treat the mounted server as a proxy. If None (default),
                automatically determined based on whether the server has a custom lifespan
                (True if it has a custom lifespan, False otherwise).
        """
        from fastmcp import Client
        from fastmcp.client.transports import FastMCPTransport
        from fastmcp.server.proxy import FastMCPProxy

        # if as_proxy is not specified and the server has a custom lifespan,
        # we should treat it as a proxy
        if as_proxy is None:
            as_proxy = server._has_lifespan

        if as_proxy and not isinstance(server, FastMCPProxy):
            server = FastMCPProxy(Client(transport=FastMCPTransport(server)))

        mounted_server = MountedServer(
            server=server,
            prefix=prefix,
            tool_separator=tool_separator,
            resource_separator=resource_separator,
            prompt_separator=prompt_separator,
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

        When a server is mounted: - The tools are imported with prefixed names
        using the tool_separator
          Example: If server has a tool named "get_weather", it will be
          available as "weatherget_weather"
        - The resources are imported with prefixed URIs using the
          resource_separator Example: If server has a resource with URI
          "weather://forecast", it will be available as
          "weather+weather://forecast"
        - The templates are imported with prefixed URI templates using the
          resource_separator Example: If server has a template with URI
          "weather://location/{id}", it will be available as
          "weather+weather://location/{id}"
        - The prompts are imported with prefixed names using the
          prompt_separator Example: If server has a prompt named
          "weather_prompt", it will be available as "weather_weather_prompt"
        - The mounted server's lifespan will be executed when the parent
          server's lifespan runs, ensuring that any setup needed by the mounted
          server is performed

        Args:
            prefix: The prefix to use for the mounted server server: The FastMCP
            server to mount tool_separator: Separator for tool names (defaults
            to "_") resource_separator: Separator for resource URIs (defaults to
            "+") prompt_separator: Separator for prompt names (defaults to "_")
        """
        if tool_separator is None:
            tool_separator = "_"
        if resource_separator is None:
            resource_separator = "+"
        if prompt_separator is None:
            prompt_separator = "_"

        # Import tools from the mounted server
        tool_prefix = f"{prefix}{tool_separator}"
        for key, tool in (await server.get_tools()).items():
            self._tool_manager.add_tool(tool, key=f"{tool_prefix}{key}")

        # Import resources and templates from the mounted server
        resource_prefix = f"{prefix}{resource_separator}"
        _validate_resource_prefix(resource_prefix)
        for key, resource in (await server.get_resources()).items():
            self._resource_manager.add_resource(resource, key=f"{resource_prefix}{key}")
        for key, template in (await server.get_resource_templates()).items():
            self._resource_manager.add_template(template, key=f"{resource_prefix}{key}")

        # Import prompts from the mounted server
        prompt_prefix = f"{prefix}{prompt_separator}"
        for key, prompt in (await server.get_prompts()).items():
            self._prompt_manager.add_prompt(prompt, key=f"{prompt_prefix}{key}")

        logger.info(f"Imported server {server.name} with prefix '{prefix}'")
        logger.debug(f"Imported tools with prefix '{tool_prefix}'")
        logger.debug(f"Imported resources with prefix '{resource_prefix}'")
        logger.debug(f"Imported templates with prefix '{resource_prefix}'")
        logger.debug(f"Imported prompts with prefix '{prompt_prefix}'")

        self._cache.clear()

    @classmethod
    def from_openapi(
        cls, openapi_spec: dict[str, Any], client: httpx.AsyncClient, **settings: Any
    ) -> FastMCPOpenAPI:
        """
        Create a FastMCP server from an OpenAPI specification.
        """
        from .openapi import FastMCPOpenAPI

        return FastMCPOpenAPI(openapi_spec=openapi_spec, client=client, **settings)

    @classmethod
    def from_fastapi(
        cls, app: Any, name: str | None = None, **settings: Any
    ) -> FastMCPOpenAPI:
        """
        Create a FastMCP server from a FastAPI application.
        """

        from .openapi import FastMCPOpenAPI

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fastapi"
        )

        name = name or app.title

        return FastMCPOpenAPI(
            openapi_spec=app.openapi(), client=client, name=name, **settings
        )

    @classmethod
    def from_client(cls, client: Client, **settings: Any) -> FastMCPProxy:
        """
        Create a FastMCP proxy server from a FastMCP client.
        """
        from fastmcp.server.proxy import FastMCPProxy

        return FastMCPProxy(client=client, **settings)


def _validate_resource_prefix(prefix: str) -> None:
    valid_resource = "resource://path/to/resource"
    test_case = f"{prefix}{valid_resource}"
    try:
        AnyUrl(test_case)
    except pydantic.ValidationError as e:
        raise ValueError(
            "Resource prefix or separator would result in an "
            f"invalid resource URI (test case was {test_case!r}): {e}"
        )


class MountedServer:
    def __init__(
        self,
        prefix: str,
        server: FastMCP,
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
    ):
        if tool_separator is None:
            tool_separator = "_"
        if resource_separator is None:
            resource_separator = "+"
        if prompt_separator is None:
            prompt_separator = "_"

        _validate_resource_prefix(f"{prefix}{resource_separator}")

        self.server = server
        self.prefix = prefix
        self.tool_separator = tool_separator
        self.resource_separator = resource_separator
        self.prompt_separator = prompt_separator

    async def get_tools(self) -> dict[str, Tool]:
        tools = await self.server.get_tools()
        return {
            f"{self.prefix}{self.tool_separator}{key}": tool
            for key, tool in tools.items()
        }

    async def get_resources(self) -> dict[str, Resource]:
        resources = await self.server.get_resources()
        return {
            f"{self.prefix}{self.resource_separator}{key}": resource
            for key, resource in resources.items()
        }

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        templates = await self.server.get_resource_templates()
        return {
            f"{self.prefix}{self.resource_separator}{key}": template
            for key, template in templates.items()
        }

    async def get_prompts(self) -> dict[str, Prompt]:
        prompts = await self.server.get_prompts()
        return {
            f"{self.prefix}{self.prompt_separator}{key}": prompt
            for key, prompt in prompts.items()
        }

    def match_tool(self, key: str) -> bool:
        return key.startswith(f"{self.prefix}{self.tool_separator}")

    def strip_tool_prefix(self, key: str) -> str:
        return key.removeprefix(f"{self.prefix}{self.tool_separator}")

    def match_resource(self, key: str) -> bool:
        return key.startswith(f"{self.prefix}{self.resource_separator}")

    def strip_resource_prefix(self, key: str) -> str:
        return key.removeprefix(f"{self.prefix}{self.resource_separator}")

    def match_prompt(self, key: str) -> bool:
        return key.startswith(f"{self.prefix}{self.prompt_separator}")

    def strip_prompt_prefix(self, key: str) -> str:
        return key.removeprefix(f"{self.prefix}{self.prompt_separator}")
