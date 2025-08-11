"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import inspect
import re
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, cast, overload

import anyio
import httpx
import mcp.types
import uvicorn
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import LifespanResultT, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Annotations,
    AnyFunction,
    CallToolRequestParams,
    ContentBlock,
    GetPromptResult,
    ToolAnnotations,
)
from mcp.types import Prompt as MCPPrompt
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import Tool as MCPTool
from pydantic import AnyUrl
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

import fastmcp
import fastmcp.server
from fastmcp.exceptions import DisabledError, NotFoundError
from fastmcp.mcp_config import MCPConfig
from fastmcp.prompts import Prompt, PromptManager
from fastmcp.prompts.prompt import FunctionPrompt
from fastmcp.resources import Resource, ResourceManager
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.registry import get_registered_provider
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.settings import Settings
from fastmcp.tools import ToolManager
from fastmcp.tools.tool import FunctionTool, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.client.transports import ClientTransport, ClientTransportT
    from fastmcp.experimental.server.openapi import FastMCPOpenAPI as FastMCPOpenAPINew
    from fastmcp.experimental.server.openapi.routing import (
        ComponentFn as OpenAPIComponentFnNew,
    )
    from fastmcp.experimental.server.openapi.routing import RouteMap as RouteMapNew
    from fastmcp.experimental.server.openapi.routing import (
        RouteMapFn as OpenAPIRouteMapFnNew,
    )
    from fastmcp.server.openapi import ComponentFn as OpenAPIComponentFn
    from fastmcp.server.openapi import FastMCPOpenAPI, RouteMap
    from fastmcp.server.openapi import RouteMapFn as OpenAPIRouteMapFn
    from fastmcp.server.proxy import FastMCPProxy

logger = get_logger(__name__)

DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]
Transport = Literal["stdio", "http", "sse", "streamable-http"]

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
    [LowLevelServer[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]
]:
    @asynccontextmanager
    async def wrap(
        s: LowLevelServer[LifespanResultT],
    ) -> AsyncIterator[LifespanResultT]:
        async with AsyncExitStack() as stack:
            context = await stack.enter_async_context(lifespan(app))
            yield context

    return wrap


class FastMCP(Generic[LifespanResultT]):
    def __init__(
        self,
        name: str | None = None,
        instructions: str | None = None,
        *,
        version: str | None = None,
        auth: AuthProvider | None | NotSetT = NotSet,
        middleware: list[Middleware] | None = None,
        lifespan: (
            Callable[
                [FastMCP[LifespanResultT]],
                AbstractAsyncContextManager[LifespanResultT],
            ]
            | None
        ) = None,
        dependencies: list[str] | None = None,
        resource_prefix_format: Literal["protocol", "path"] | None = None,
        mask_error_details: bool | None = None,
        tools: list[Tool | Callable[..., Any]] | None = None,
        tool_transformations: dict[str, ToolTransformConfig] | None = None,
        tool_serializer: Callable[[Any], str] | None = None,
        include_tags: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        include_fastmcp_meta: bool | None = None,
        on_duplicate_tools: DuplicateBehavior | None = None,
        on_duplicate_resources: DuplicateBehavior | None = None,
        on_duplicate_prompts: DuplicateBehavior | None = None,
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

        self._additional_http_routes: list[BaseRoute] = []
        self._tool_manager = ToolManager(
            duplicate_behavior=on_duplicate_tools,
            mask_error_details=mask_error_details,
            transformations=tool_transformations,
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
        self._mcp_server = LowLevelServer[LifespanResultT](
            name=name or "FastMCP",
            version=version,
            instructions=instructions,
            lifespan=_lifespan_wrapper(self, lifespan),
        )

        # if auth is `NotSet`, try to create a provider from the environment
        if auth is NotSet:
            if fastmcp.settings.server_auth is not None:
                provider_cls = get_registered_provider(fastmcp.settings.server_auth)
                auth = provider_cls()
            else:
                auth = None
        self.auth = cast(AuthProvider | None, auth)

        if tools:
            for tool in tools:
                if not isinstance(tool, Tool):
                    tool = Tool.from_function(tool, serializer=self._tool_serializer)
                self.add_tool(tool)

        self.include_tags = include_tags
        self.exclude_tags = exclude_tags

        self.middleware = middleware or []

        # Set up MCP protocol handlers
        self._setup_handlers()
        self.dependencies = dependencies or fastmcp.settings.server_dependencies

        self.include_fastmcp_meta = (
            include_fastmcp_meta
            if include_fastmcp_meta is not None
            else fastmcp.settings.include_fastmcp_meta
        )

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
                if fastmcp.settings.deprecation_warnings:
                    warnings.warn(
                        f"Providing `{name}` when creating a server is deprecated. Provide it when calling `run` or as a global setting instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                deprecated_settings[name] = arg

        combined_settings = fastmcp.settings.model_dump() | deprecated_settings
        self._deprecated_settings = Settings(**combined_settings)

    @property
    def settings(self) -> Settings:
        # Deprecated in 2.8.0
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "Accessing `.settings` on a FastMCP instance is deprecated. Use the global `fastmcp.settings` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self._deprecated_settings

    @property
    def name(self) -> str:
        return self._mcp_server.name

    @property
    def instructions(self) -> str | None:
        return self._mcp_server.instructions

    @property
    def version(self) -> str | None:
        return self._mcp_server.version

    async def run_async(
        self,
        transport: Transport | None = None,
        show_banner: bool = True,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio", "sse", or "streamable-http")
        """
        if transport is None:
            transport = "stdio"
        if transport not in {"stdio", "http", "sse", "streamable-http"}:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async(
                show_banner=show_banner,
                **transport_kwargs,
            )
        elif transport in {"http", "sse", "streamable-http"}:
            await self.run_http_async(
                transport=transport,
                show_banner=show_banner,
                **transport_kwargs,
            )
        else:
            raise ValueError(f"Unknown transport: {transport}")

    def run(
        self,
        transport: Transport | None = None,
        show_banner: bool = True,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("stdio", "sse", or "streamable-http")
        """

        anyio.run(
            partial(
                self.run_async,
                transport,
                show_banner=show_banner,
                **transport_kwargs,
            )
        )

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self._mcp_list_tools)
        self._mcp_server.list_resources()(self._mcp_list_resources)
        self._mcp_server.list_resource_templates()(self._mcp_list_resource_templates)
        self._mcp_server.list_prompts()(self._mcp_list_prompts)
        self._mcp_server.call_tool()(self._mcp_call_tool)
        self._mcp_server.read_resource()(self._mcp_read_resource)
        self._mcp_server.get_prompt()(self._mcp_get_prompt)

    async def _apply_middleware(
        self,
        context: MiddlewareContext[Any],
        call_next: Callable[[MiddlewareContext[Any]], Awaitable[Any]],
    ) -> Any:
        """Builds and executes the middleware chain."""
        chain = call_next
        for mw in reversed(self.middleware):
            chain = partial(mw, call_next=chain)
        return await chain(context)

    def add_middleware(self, middleware: Middleware) -> None:
        self.middleware.append(middleware)

    async def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        return await self._tool_manager.get_tools()

    async def get_tool(self, key: str) -> Tool:
        tools = await self.get_tools()
        if key not in tools:
            raise NotFoundError(f"Unknown tool: {key}")
        return tools[key]

    async def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, indexed by registered key."""
        return await self._resource_manager.get_resources()

    async def get_resource(self, key: str) -> Resource:
        resources = await self.get_resources()
        if key not in resources:
            raise NotFoundError(f"Unknown resource: {key}")
        return resources[key]

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered resource templates, indexed by registered key."""
        return await self._resource_manager.get_resource_templates()

    async def get_resource_template(self, key: str) -> ResourceTemplate:
        """Get a registered resource template by key."""
        templates = await self.get_resource_templates()
        if key not in templates:
            raise NotFoundError(f"Unknown resource template: {key}")
        return templates[key]

    async def get_prompts(self) -> dict[str, Prompt]:
        """
        List all available prompts.
        """
        return await self._prompt_manager.get_prompts()

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
    ) -> Callable[
        [Callable[[Request], Awaitable[Response]]],
        Callable[[Request], Awaitable[Response]],
    ]:
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
            Register a custom HTTP route for a health check endpoint:
            ```python
            @server.custom_route("/health", methods=["GET"])
            async def health_check(request: Request) -> Response:
                return JSONResponse({"status": "ok"})
            ```
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
        logger.debug("Handler called: list_tools")

        async with fastmcp.server.context.Context(fastmcp=self):
            tools = await self._list_tools()
            return [
                tool.to_mcp_tool(
                    name=tool.key,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for tool in tools
            ]

    async def _list_tools(self) -> list[Tool]:
        """
        List all available tools, in the format expected by the low-level MCP
        server.
        """

        async def _handler(
            context: MiddlewareContext[mcp.types.ListToolsRequest],
        ) -> list[Tool]:
            tools = await self._tool_manager.list_tools()  # type: ignore[reportPrivateUsage]

            mcp_tools: list[Tool] = []
            for tool in tools:
                if self._should_enable_component(tool):
                    mcp_tools.append(tool)

            return mcp_tools

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            # Create the middleware context.
            mw_context = MiddlewareContext(
                message=mcp.types.ListToolsRequest(method="tools/list"),
                source="client",
                type="request",
                method="tools/list",
                fastmcp_context=fastmcp_ctx,
            )

            # Apply the middleware chain.
            return await self._apply_middleware(mw_context, _handler)

    async def _mcp_list_resources(self) -> list[MCPResource]:
        logger.debug("Handler called: list_resources")

        async with fastmcp.server.context.Context(fastmcp=self):
            resources = await self._list_resources()
            return [
                resource.to_mcp_resource(
                    uri=resource.key,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for resource in resources
            ]

    async def _list_resources(self) -> list[Resource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.

        """

        async def _handler(
            context: MiddlewareContext[dict[str, Any]],
        ) -> list[Resource]:
            resources = await self._resource_manager.list_resources()  # type: ignore[reportPrivateUsage]

            mcp_resources: list[Resource] = []
            for resource in resources:
                if self._should_enable_component(resource):
                    mcp_resources.append(resource)

            return mcp_resources

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            # Create the middleware context.
            mw_context = MiddlewareContext(
                message={},  # List resources doesn't have parameters
                source="client",
                type="request",
                method="resources/list",
                fastmcp_context=fastmcp_ctx,
            )

            # Apply the middleware chain.
            return await self._apply_middleware(mw_context, _handler)

    async def _mcp_list_resource_templates(self) -> list[MCPResourceTemplate]:
        logger.debug("Handler called: list_resource_templates")

        async with fastmcp.server.context.Context(fastmcp=self):
            templates = await self._list_resource_templates()
            return [
                template.to_mcp_template(
                    uriTemplate=template.key,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for template in templates
            ]

    async def _list_resource_templates(self) -> list[ResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server.

        """

        async def _handler(
            context: MiddlewareContext[dict[str, Any]],
        ) -> list[ResourceTemplate]:
            templates = await self._resource_manager.list_resource_templates()

            mcp_templates: list[ResourceTemplate] = []
            for template in templates:
                if self._should_enable_component(template):
                    mcp_templates.append(template)

            return mcp_templates

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            # Create the middleware context.
            mw_context = MiddlewareContext(
                message={},  # List resource templates doesn't have parameters
                source="client",
                type="request",
                method="resources/templates/list",
                fastmcp_context=fastmcp_ctx,
            )

            # Apply the middleware chain.
            return await self._apply_middleware(mw_context, _handler)

    async def _mcp_list_prompts(self) -> list[MCPPrompt]:
        logger.debug("Handler called: list_prompts")

        async with fastmcp.server.context.Context(fastmcp=self):
            prompts = await self._list_prompts()
            return [
                prompt.to_mcp_prompt(
                    name=prompt.key,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for prompt in prompts
            ]

    async def _list_prompts(self) -> list[Prompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.

        """

        async def _handler(
            context: MiddlewareContext[mcp.types.ListPromptsRequest],
        ) -> list[Prompt]:
            prompts = await self._prompt_manager.list_prompts()  # type: ignore[reportPrivateUsage]

            mcp_prompts: list[Prompt] = []
            for prompt in prompts:
                if self._should_enable_component(prompt):
                    mcp_prompts.append(prompt)

            return mcp_prompts

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            # Create the middleware context.
            mw_context = MiddlewareContext(
                message=mcp.types.ListPromptsRequest(method="prompts/list"),
                source="client",
                type="request",
                method="prompts/list",
                fastmcp_context=fastmcp_ctx,
            )

            # Apply the middleware chain.
            return await self._apply_middleware(mw_context, _handler)

    async def _mcp_call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]]:
        """
        Handle MCP 'callTool' requests.

        Delegates to _call_tool, which should be overridden by FastMCP subclasses.

        Args:
            key: The name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            List of MCP Content objects containing the tool results
        """
        logger.debug("Handler called: call_tool %s with %s", key, arguments)

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                result = await self._call_tool(key, arguments)
                return result.to_mcp_result()
            except DisabledError:
                raise NotFoundError(f"Unknown tool: {key}")
            except NotFoundError:
                raise NotFoundError(f"Unknown tool: {key}")

    async def _call_tool(self, key: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Applies this server's middleware and delegates the filtered call to the manager.
        """

        async def _handler(
            context: MiddlewareContext[mcp.types.CallToolRequestParams],
        ) -> ToolResult:
            tool = await self._tool_manager.get_tool(context.message.name)
            if not self._should_enable_component(tool):
                raise NotFoundError(f"Unknown tool: {context.message.name!r}")

            return await self._tool_manager.call_tool(
                key=context.message.name, arguments=context.message.arguments or {}
            )

        mw_context = MiddlewareContext[CallToolRequestParams](
            message=mcp.types.CallToolRequestParams(name=key, arguments=arguments),
            source="client",
            type="request",
            method="tools/call",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        return await self._apply_middleware(mw_context, _handler)

    async def _mcp_read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """
        Handle MCP 'readResource' requests.

        Delegates to _read_resource, which should be overridden by FastMCP subclasses.
        """
        logger.debug("Handler called: read_resource %s", uri)

        async with fastmcp.server.context.Context(fastmcp=self):
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
        Applies this server's middleware and delegates the filtered call to the manager.
        """

        async def _handler(
            context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        ) -> list[ReadResourceContents]:
            resource = await self._resource_manager.get_resource(context.message.uri)
            if not self._should_enable_component(resource):
                raise NotFoundError(f"Unknown resource: {str(context.message.uri)!r}")

            content = await self._resource_manager.read_resource(context.message.uri)
            return [
                ReadResourceContents(
                    content=content,
                    mime_type=resource.mime_type,
                )
            ]

        # Convert string URI to AnyUrl if needed
        if isinstance(uri, str):
            uri_param = AnyUrl(uri)
        else:
            uri_param = uri

        mw_context = MiddlewareContext(
            message=mcp.types.ReadResourceRequestParams(uri=uri_param),
            source="client",
            type="request",
            method="resources/read",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        return await self._apply_middleware(mw_context, _handler)

    async def _mcp_get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """
        Handle MCP 'getPrompt' requests.

        Delegates to _get_prompt, which should be overridden by FastMCP subclasses.
        """
        logger.debug("Handler called: get_prompt %s with %s", name, arguments)

        async with fastmcp.server.context.Context(fastmcp=self):
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
        """
        Applies this server's middleware and delegates the filtered call to the manager.
        """

        async def _handler(
            context: MiddlewareContext[mcp.types.GetPromptRequestParams],
        ) -> GetPromptResult:
            prompt = await self._prompt_manager.get_prompt(context.message.name)
            if not self._should_enable_component(prompt):
                raise NotFoundError(f"Unknown prompt: {context.message.name!r}")

            return await self._prompt_manager.render_prompt(
                name=context.message.name, arguments=context.message.arguments
            )

        mw_context = MiddlewareContext(
            message=mcp.types.GetPromptRequestParams(name=name, arguments=arguments),
            source="client",
            type="request",
            method="prompts/get",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        return await self._apply_middleware(mw_context, _handler)

    def add_tool(self, tool: Tool) -> Tool:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            tool: The Tool instance to register

        Returns:
            The tool instance that was added to the server.
        """
        self._tool_manager.add_tool(tool)

        # Send notification if we're in a request context
        try:
            from fastmcp.server.dependencies import get_context

            context = get_context()
            context._queue_tool_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

        return tool

    def remove_tool(self, name: str) -> None:
        """Remove a tool from the server.

        Args:
            name: The name of the tool to remove

        Raises:
            NotFoundError: If the tool is not found
        """
        self._tool_manager.remove_tool(name)

        # Send notification if we're in a request context
        try:
            from fastmcp.server.dependencies import get_context

            context = get_context()
            context._queue_tool_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def add_tool_transformation(
        self, tool_name: str, transformation: ToolTransformConfig
    ) -> None:
        """Add a tool transformation."""
        self._tool_manager.add_tool_transformation(tool_name, transformation)

    def remove_tool_transformation(self, tool_name: str) -> None:
        """Remove a tool transformation."""
        self._tool_manager.remove_tool_transformation(tool_name)

    @overload
    def tool(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | None | NotSetT = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
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
            output_schema: Optional JSON schema for the tool's output
            annotations: Optional annotations about the tool's behavior
            exclude_args: Optional list of argument names to exclude from the tool schema
            meta: Optional meta information about the tool
            enabled: Optional boolean to enable or disable the tool

        Examples:
            Register a tool with a custom name:
            ```python
            @server.tool
            def my_tool(x: int) -> str:
                return str(x)

            # Register a tool with a custom name
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
            ```
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
                title=title,
                description=description,
                tags=tags,
                output_schema=output_schema,
                annotations=annotations,
                exclude_args=exclude_args,
                meta=meta,
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
            title=title,
            description=description,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            exclude_args=exclude_args,
            meta=meta,
            enabled=enabled,
        )

    def add_resource(self, resource: Resource) -> Resource:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add

        Returns:
            The resource instance that was added to the server.
        """
        self._resource_manager.add_resource(resource)

        # Send notification if we're in a request context
        try:
            from fastmcp.server.dependencies import get_context

            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

        return resource

    def add_template(self, template: ResourceTemplate) -> ResourceTemplate:
        """Add a resource template to the server.

        Args:
            template: A ResourceTemplate instance to add

        Returns:
            The template instance that was added to the server.
        """
        self._resource_manager.add_template(template)

        # Send notification if we're in a request context
        try:
            from fastmcp.server.dependencies import get_context

            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

        return template

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
        if fastmcp.settings.deprecation_warnings:
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

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
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
            annotations: Optional annotations about the resource's behavior
            meta: Optional meta information about the resource

        Examples:
            Register a resource with a custom name:
            ```python
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
                    title=title,
                    description=description,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                    annotations=annotations,
                    meta=meta,
                )
                self.add_template(template)
                return template
            elif not has_uri_params and not has_func_params:
                resource = Resource.from_function(
                    fn=fn,
                    uri=uri,
                    name=name,
                    title=title,
                    description=description,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                    annotations=annotations,
                    meta=meta,
                )
                self.add_resource(resource)
                return resource
            else:
                raise ValueError(
                    "Invalid resource or template definition due to a "
                    "mismatch between URI parameters and function parameters."
                )

        return decorator

    def add_prompt(self, prompt: Prompt) -> Prompt:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance to add

        Returns:
            The prompt instance that was added to the server.
        """
        self._prompt_manager.add_prompt(prompt)

        # Send notification if we're in a request context
        try:
            from fastmcp.server.dependencies import get_context

            context = get_context()
            context._queue_prompt_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

        return prompt

    @overload
    def prompt(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
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
            meta: Optional meta information about the prompt

        Examples:

            ```python
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
            # Case 1: @prompt (without parens) - function passed directly as decorator
            # Case 2: direct call like prompt(fn, name="something")
            fn = name_or_fn
            prompt_name = name  # Use keyword name if provided, otherwise None

            # Register the prompt immediately
            prompt = Prompt.from_function(
                fn=fn,
                name=prompt_name,
                title=title,
                description=description,
                tags=tags,
                enabled=enabled,
                meta=meta,
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
            title=title,
            description=description,
            tags=tags,
            enabled=enabled,
            meta=meta,
        )

    async def run_stdio_async(self, show_banner: bool = True) -> None:
        """Run the server using stdio transport."""

        # Display server banner
        if show_banner:
            log_server_banner(
                server=self,
                transport="stdio",
            )

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
        show_banner: bool = True,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        path: str | None = None,
        uvicorn_config: dict[str, Any] | None = None,
        middleware: list[ASGIMiddleware] | None = None,
        stateless_http: bool | None = None,
    ) -> None:
        """Run the server using HTTP transport.

        Args:
            transport: Transport protocol to use - either "streamable-http" (default) or "sse"
            host: Host address to bind to (defaults to settings.host)
            port: Port to bind to (defaults to settings.port)
            log_level: Log level for the server (defaults to settings.log_level)
            path: Path for the endpoint (defaults to settings.streamable_http_path or settings.sse_path)
            uvicorn_config: Additional configuration for the Uvicorn server
            middleware: A list of middleware to apply to the app
            stateless_http: Whether to use stateless HTTP (defaults to settings.stateless_http)
        """

        host = host or self._deprecated_settings.host
        port = port or self._deprecated_settings.port
        default_log_level_to_use = (
            log_level or self._deprecated_settings.log_level
        ).lower()

        app = self.http_app(
            path=path,
            transport=transport,
            middleware=middleware,
            stateless_http=stateless_http,
        )

        # Get the path for the server URL
        server_path = (
            app.state.path.lstrip("/")
            if hasattr(app, "state") and hasattr(app.state, "path")
            else path or ""
        )

        # Display server banner
        if show_banner:
            log_server_banner(
                server=self,
                transport=transport,
                host=host,
                port=port,
                path=server_path,
            )
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
        uvicorn_config: dict[str, Any] | None = None,
    ) -> None:
        """Run the server using SSE transport."""

        # Deprecated since 2.3.2
        if fastmcp.settings.deprecation_warnings:
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
        middleware: list[ASGIMiddleware] | None = None,
    ) -> StarletteWithLifespan:
        """
        Create a Starlette app for the SSE server.

        Args:
            path: The path to the SSE endpoint
            message_path: The path to the message endpoint
            middleware: A list of middleware to apply to the app
        """
        # Deprecated since 2.3.2
        if fastmcp.settings.deprecation_warnings:
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
        middleware: list[ASGIMiddleware] | None = None,
    ) -> StarletteWithLifespan:
        """
        Create a Starlette app for the StreamableHTTP server.

        Args:
            path: The path to the StreamableHTTP endpoint
            middleware: A list of middleware to apply to the app
        """
        # Deprecated since 2.3.2
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The streamable_http_app method is deprecated (as of 2.3.2). Use http_app() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self.http_app(path=path, middleware=middleware)

    def http_app(
        self,
        path: str | None = None,
        middleware: list[ASGIMiddleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["http", "streamable-http", "sse"] = "http",
    ) -> StarletteWithLifespan:
        """Create a Starlette app using the specified HTTP transport.

        Args:
            path: The path for the HTTP endpoint
            middleware: A list of middleware to apply to the app
            transport: Transport protocol to use - either "streamable-http" (default) or "sse"

        Returns:
            A Starlette application configured with the specified transport
        """

        if transport in ("streamable-http", "http"):
            return create_streamable_http_app(
                server=self,
                streamable_http_path=path
                or self._deprecated_settings.streamable_http_path,
                event_store=None,
                auth=self.auth,
                json_response=(
                    json_response
                    if json_response is not None
                    else self._deprecated_settings.json_response
                ),
                stateless_http=(
                    stateless_http
                    if stateless_http is not None
                    else self._deprecated_settings.stateless_http
                ),
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
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The run_streamable_http_async method is deprecated (as of 2.3.2). "
                "Use run_http_async instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        await self.run_http_async(
            transport="http",
            host=host,
            port=port,
            log_level=log_level,
            path=path,
            uvicorn_config=uvicorn_config,
        )

    def mount(
        self,
        server: FastMCP[LifespanResultT],
        prefix: str | None = None,
        as_proxy: bool | None = None,
        *,
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
    ) -> None:
        """Mount another FastMCP server on this server with an optional prefix.

        Unlike importing (with import_server), mounting establishes a dynamic connection
        between servers. When a client interacts with a mounted server's objects through
        the parent server, requests are forwarded to the mounted server in real-time.
        This means changes to the mounted server are immediately reflected when accessed
        through the parent.

        When a server is mounted with a prefix:
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

        When a server is mounted without a prefix (prefix=None), its tools, resources, templates,
        and prompts are accessible with their original names. Multiple servers can be mounted
        without prefixes, and they will be tried in order until a match is found.

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
            server: The FastMCP server to mount.
            prefix: Optional prefix to use for the mounted server's objects. If None,
                the server's objects are accessible with their original names.
            as_proxy: Whether to treat the mounted server as a proxy. If None (default),
                automatically determined based on whether the server has a custom lifespan
                (True if it has a custom lifespan, False otherwise).
            tool_separator: Deprecated. Separator character for tool names.
            resource_separator: Deprecated. Separator character for resource URIs.
            prompt_separator: Deprecated. Separator character for prompt names.
        """
        from fastmcp.server.proxy import FastMCPProxy

        # Deprecated since 2.9.0
        # Prior to 2.9.0, the first positional argument was the prefix and the
        # second was the server. Here we swap them if needed now that the prefix
        # is optional.
        if isinstance(server, str):
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "Mount prefixes are now optional and the first positional argument "
                    "should be the server you want to mount.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            server, prefix = cast(FastMCP[Any], prefix), server

        if tool_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The tool_separator parameter is deprecated and will be removed in a future version. "
                    "Tools are now prefixed using 'prefix_toolname' format.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        if resource_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The resource_separator parameter is deprecated and ignored. "
                    "Resource prefixes are now added using the protocol://prefix/path format.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        if prompt_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
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
            server = FastMCP.as_proxy(server)

        # Delegate mounting to all three managers
        mounted_server = MountedServer(
            prefix=prefix,
            server=server,
            resource_prefix_format=self.resource_prefix_format,
        )
        self._tool_manager.mount(mounted_server)
        self._resource_manager.mount(mounted_server)
        self._prompt_manager.mount(mounted_server)

    async def import_server(
        self,
        server: FastMCP[LifespanResultT],
        prefix: str | None = None,
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

        When a server is imported with a prefix:
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

        When a server is imported without a prefix (prefix=None), its tools, resources,
        templates, and prompts are imported with their original names.

        Args:
            server: The FastMCP server to import
            prefix: Optional prefix to use for the imported server's objects. If None,
                objects are imported with their original names.
            tool_separator: Deprecated. Separator for tool names.
            resource_separator: Deprecated and ignored. Prefix is now
              applied using the protocol://prefix/path format
            prompt_separator: Deprecated. Separator for prompt names.
        """

        # Deprecated since 2.9.0
        # Prior to 2.9.0, the first positional argument was the prefix and the
        # second was the server. Here we swap them if needed now that the prefix
        # is optional.
        if isinstance(server, str):
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "Import prefixes are now optional and the first positional argument "
                    "should be the server you want to import.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            server, prefix = cast(FastMCP[Any], prefix), server

        if tool_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The tool_separator parameter is deprecated and will be removed in a future version. "
                    "Tools are now prefixed using 'prefix_toolname' format.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        if resource_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The resource_separator parameter is deprecated and ignored. "
                    "Resource prefixes are now added using the protocol://prefix/path format.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        if prompt_separator is not None:
            # Deprecated since 2.4.0
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The prompt_separator parameter is deprecated and will be removed in a future version. "
                    "Prompts are now prefixed using 'prefix_promptname' format.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        # Import tools from the server
        for key, tool in (await server.get_tools()).items():
            if prefix:
                tool = tool.model_copy(key=f"{prefix}_{key}")
            self._tool_manager.add_tool(tool)

        # Import resources and templates from the server
        for key, resource in (await server.get_resources()).items():
            if prefix:
                resource_key = add_resource_prefix(
                    key, prefix, self.resource_prefix_format
                )
                resource = resource.model_copy(
                    update={"name": f"{prefix}_{resource.name}"}, key=resource_key
                )
            self._resource_manager.add_resource(resource)

        for key, template in (await server.get_resource_templates()).items():
            if prefix:
                template_key = add_resource_prefix(
                    key, prefix, self.resource_prefix_format
                )
                template = template.model_copy(
                    update={"name": f"{prefix}_{template.name}"}, key=template_key
                )
            self._resource_manager.add_template(template)

        # Import prompts from the server
        for key, prompt in (await server.get_prompts()).items():
            if prefix:
                prompt = prompt.model_copy(key=f"{prefix}_{key}")
            self._prompt_manager.add_prompt(prompt)

        if prefix:
            logger.debug(f"Imported server {server.name} with prefix '{prefix}'")
        else:
            logger.debug(f"Imported server {server.name}")

    @classmethod
    def from_openapi(
        cls,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        route_maps: list[RouteMap] | list[RouteMapNew] | None = None,
        route_map_fn: OpenAPIRouteMapFn | OpenAPIRouteMapFnNew | None = None,
        mcp_component_fn: OpenAPIComponentFn | OpenAPIComponentFnNew | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        **settings: Any,
    ) -> FastMCPOpenAPI | FastMCPOpenAPINew:
        """
        Create a FastMCP server from an OpenAPI specification.
        """

        # Check if experimental parser is enabled
        if fastmcp.settings.experimental.enable_new_openapi_parser:
            from fastmcp.experimental.server.openapi import FastMCPOpenAPI

            return FastMCPOpenAPI(
                openapi_spec=openapi_spec,
                client=client,
                route_maps=cast(Any, route_maps),
                route_map_fn=cast(Any, route_map_fn),
                mcp_component_fn=cast(Any, mcp_component_fn),
                mcp_names=mcp_names,
                tags=tags,
                **settings,
            )
        else:
            logger.info(
                "Using legacy OpenAPI parser. To use the new parser, set "
                "FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER=true. The new parser "
                "was introduced for testing in 2.11 and will become the default soon."
            )
            from .openapi import FastMCPOpenAPI

            return FastMCPOpenAPI(
                openapi_spec=openapi_spec,
                client=client,
                route_maps=cast(Any, route_maps),
                route_map_fn=cast(Any, route_map_fn),
                mcp_component_fn=cast(Any, mcp_component_fn),
                mcp_names=mcp_names,
                tags=tags,
                **settings,
            )

    @classmethod
    def from_fastapi(
        cls,
        app: Any,
        name: str | None = None,
        route_maps: list[RouteMap] | list[RouteMapNew] | None = None,
        route_map_fn: OpenAPIRouteMapFn | OpenAPIRouteMapFnNew | None = None,
        mcp_component_fn: OpenAPIComponentFn | OpenAPIComponentFnNew | None = None,
        mcp_names: dict[str, str] | None = None,
        httpx_client_kwargs: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        **settings: Any,
    ) -> FastMCPOpenAPI | FastMCPOpenAPINew:
        """
        Create a FastMCP server from a FastAPI application.
        """

        if httpx_client_kwargs is None:
            httpx_client_kwargs = {}
        httpx_client_kwargs.setdefault("base_url", "http://fastapi")

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            **httpx_client_kwargs,
        )

        name = name or app.title

        # Check if experimental parser is enabled
        if fastmcp.settings.experimental.enable_new_openapi_parser:
            from fastmcp.experimental.server.openapi import FastMCPOpenAPI

            return FastMCPOpenAPI(
                openapi_spec=app.openapi(),
                client=client,
                name=name,
                route_maps=cast(Any, route_maps),
                route_map_fn=cast(Any, route_map_fn),
                mcp_component_fn=cast(Any, mcp_component_fn),
                mcp_names=mcp_names,
                tags=tags,
                **settings,
            )
        else:
            logger.info(
                "Using legacy OpenAPI parser. To use the new parser, set "
                "FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER=true. The new parser "
                "was introduced for testing in 2.11 and will become the default soon."
            )
            from .openapi import FastMCPOpenAPI

            return FastMCPOpenAPI(
                openapi_spec=app.openapi(),
                client=client,
                name=name,
                route_maps=cast(Any, route_maps),
                route_map_fn=cast(Any, route_map_fn),
                mcp_component_fn=cast(Any, mcp_component_fn),
                mcp_names=mcp_names,
                tags=tags,
                **settings,
            )

    @classmethod
    def as_proxy(
        cls,
        backend: (
            Client[ClientTransportT]
            | ClientTransport
            | FastMCP[Any]
            | AnyUrl
            | Path
            | MCPConfig
            | dict[str, Any]
            | str
        ),
        **settings: Any,
    ) -> FastMCPProxy:
        """Create a FastMCP proxy server for the given backend.

        The `backend` argument can be either an existing `fastmcp.client.Client`
        instance or any value accepted as the `transport` argument of
        `fastmcp.client.Client`. This mirrors the convenience of the
        `fastmcp.client.Client` constructor.
        """
        from fastmcp.client.client import Client
        from fastmcp.server.proxy import FastMCPProxy, ProxyClient

        if isinstance(backend, Client):
            client = backend
            # Session strategy based on client connection state:
            # - Connected clients: reuse existing session for all requests
            # - Disconnected clients: create fresh sessions per request for isolation
            if client.is_connected():
                from fastmcp.utilities.logging import get_logger

                logger = get_logger(__name__)
                logger.info(
                    "Proxy detected connected client - reusing existing session for all requests. "
                    "This may cause context mixing in concurrent scenarios."
                )

                # Reuse sessions - return the same client instance
                def reuse_client_factory():
                    return client

                client_factory = reuse_client_factory
            else:
                # Fresh sessions per request
                def fresh_client_factory():
                    return client.new()

                client_factory = fresh_client_factory
        else:
            base_client = ProxyClient(backend)

            # Fresh client created from transport - use fresh sessions per request
            def proxy_client_factory():
                return base_client.new()

            client_factory = proxy_client_factory

        return FastMCPProxy(client_factory=client_factory, **settings)

    @classmethod
    def from_client(
        cls, client: Client[ClientTransportT], **settings: Any
    ) -> FastMCPProxy:
        """
        Create a FastMCP proxy server from a FastMCP client.
        """
        # Deprecated since 2.3.5
        if fastmcp.settings.deprecation_warnings:
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
            - If the component's enabled property is False, always return False.
            - If both include_tags and exclude_tags are None, return True.
            - If exclude_tags is provided, check each exclude tag:
                - If the exclude tag is a string, it must be present in the input tags to exclude.
            - If include_tags is provided, check each include tag:
                - If the include tag is a string, it must be present in the input tags to include.
            - If include_tags is provided and none of the include tags match, return False.
            - If include_tags is not provided, return True.
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


@dataclass
class MountedServer:
    prefix: str | None
    server: FastMCP[Any]
    resource_prefix_format: Literal["protocol", "path"] | None = None


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
        With new style:
        ```python
        add_resource_prefix("resource://path/to/resource", "prefix")
        "resource://prefix/path/to/resource"
        ```
        With legacy style:
        ```python
        add_resource_prefix("resource://path/to/resource", "prefix")
        "prefix+resource://path/to/resource"
        ```
        With absolute path:
        ```python
        add_resource_prefix("resource:///absolute/path", "prefix")
        "resource://prefix//absolute/path"
        ```

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
        With new style:
        ```python
        remove_resource_prefix("resource://prefix/path/to/resource", "prefix")
        "resource://path/to/resource"
        ```
        With legacy style:
        ```python
        remove_resource_prefix("prefix+resource://path/to/resource", "prefix")
        "resource://path/to/resource"
        ```
        With absolute path:
        ```python
        remove_resource_prefix("resource://prefix//absolute/path", "prefix")
        "resource:///absolute/path"
        ```

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
        With new style:
        ```python
        has_resource_prefix("resource://prefix/path/to/resource", "prefix")
        True
        ```
        With legacy style:
        ```python
        has_resource_prefix("prefix+resource://path/to/resource", "prefix")
        True
        ```
        With other path:
        ```python
        has_resource_prefix("resource://other/path/to/resource", "prefix")
        False
        ```

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
