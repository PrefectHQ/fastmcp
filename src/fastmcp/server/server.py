"""FastMCP - A more ergonomic interface for MCP servers."""

import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from typing import TYPE_CHECKING, Any, Generic, Literal

import anyio
import httpx
import pydantic_core
import uvicorn
from fastapi import FastAPI
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.lowlevel.server import lifespan as default_lifespan
from mcp.server.session import ServerSession
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.types import (
    AnyFunction,
    EmbeddedResource,
    GetPromptResult,
    ImageContent,
    TextContent,
)
from mcp.types import Prompt as MCPPrompt
from mcp.types import PromptArgument as MCPPromptArgument
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import Tool as MCPTool
from pydantic.networks import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route

import fastmcp
import fastmcp.settings
from fastmcp.exceptions import ResourceError
from fastmcp.prompts import Prompt, PromptManager
from fastmcp.resources import FunctionResource, Resource, ResourceManager
from fastmcp.tools import ToolManager
from fastmcp.utilities.logging import configure_logging, get_logger
from fastmcp.utilities.types import Image

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.server.context import Context
    from fastmcp.server.openapi import FastMCPOpenAPI
    from fastmcp.server.proxy import FastMCPProxy
logger = get_logger(__name__)


def lifespan_wrapper(
    app: "FastMCP",
    lifespan: Callable[["FastMCP"], AbstractAsyncContextManager[LifespanResultT]],
) -> Callable[
    [MCPServer[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]
]:
    @asynccontextmanager
    async def wrap(s: MCPServer[LifespanResultT]) -> AsyncIterator[LifespanResultT]:
        async with lifespan(app) as context:
            yield context

    return wrap


class FastMCP(Generic[LifespanResultT]):
    def __init__(
        self,
        name: str | None = None,
        instructions: str | None = None,
        lifespan: (
            Callable[["FastMCP"], AbstractAsyncContextManager[LifespanResultT]] | None
        ) = None,
        **settings: Any,
    ):
        self.settings = fastmcp.settings.ServerSettings(**settings)

        self._mcp_server = MCPServer[LifespanResultT](
            name=name or "FastMCP",
            instructions=instructions,
            lifespan=lifespan_wrapper(self, lifespan) if lifespan else default_lifespan,  # type: ignore
        )
        self._tool_manager = ToolManager(
            warn_on_duplicate_tools=self.settings.warn_on_duplicate_tools
        )
        self._resource_manager = ResourceManager(
            warn_on_duplicate_resources=self.settings.warn_on_duplicate_resources
        )
        self._prompt_manager = PromptManager(
            warn_on_duplicate_prompts=self.settings.warn_on_duplicate_prompts
        )
        self.dependencies = self.settings.dependencies

        # Setup for mounted apps
        self._mounted_apps: dict[str, FastMCP] = {}

        # Set up MCP protocol handlers
        self._setup_handlers()

        # Configure logging
        configure_logging(self.settings.log_level)

    @property
    def name(self) -> str:
        return self._mcp_server.name

    @property
    def instructions(self) -> str | None:
        return self._mcp_server.instructions

    async def run_async(self, transport: Literal["stdio", "sse"] | None = None) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio" or "sse")
        """
        if transport is None:
            transport = "stdio"
        if transport not in ["stdio", "sse"]:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async()
        else:  # transport == "sse"
            await self.run_sse_async()

    def run(self, transport: Literal["stdio", "sse"] | None = None) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("stdio" or "sse")
        """
        anyio.run(self.run_async, transport)

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self.list_tools)
        self._mcp_server.call_tool()(self.call_tool)
        self._mcp_server.list_resources()(self.list_resources)
        self._mcp_server.read_resource()(self.read_resource)
        self._mcp_server.list_prompts()(self.list_prompts)
        self._mcp_server.get_prompt()(self.get_prompt)
        self._mcp_server.list_resource_templates()(self.list_resource_templates)

    async def list_tools(self) -> list[MCPTool]:
        """List all available tools."""
        tools = self._tool_manager.list_tools()
        return [
            MCPTool(
                name=info.name,
                description=info.description,
                inputSchema=info.parameters,
            )
            for info in tools
        ]

    def get_context(self) -> "Context[ServerSession, LifespanResultT]":
        """
        Returns a Context object. Note that the context will only be valid
        during a request; outside a request, most methods will error.
        """

        try:
            request_context = self._mcp_server.request_context
        except LookupError:
            request_context = None
        from fastmcp.server.context import Context

        return Context(request_context=request_context, fastmcp=self)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        """Call a tool by name with arguments."""
        context = self.get_context()
        result = await self._tool_manager.call_tool(name, arguments, context=context)
        converted_result = _convert_to_content(result)
        return converted_result

    async def list_resources(self) -> list[MCPResource]:
        """List all available resources."""

        resources = self._resource_manager.list_resources()
        return [
            MCPResource(
                uri=resource.uri,
                name=resource.name or "",
                description=resource.description,
                mimeType=resource.mime_type,
            )
            for resource in resources
        ]

    async def list_resource_templates(self) -> list[MCPResourceTemplate]:
        templates = self._resource_manager.list_templates()
        return [
            MCPResourceTemplate(
                uriTemplate=template.uri_template,
                name=template.name,
                description=template.description,
            )
            for template in templates
        ]

    async def read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """Read a resource by URI."""

        resource = await self._resource_manager.get_resource(uri)
        if not resource:
            raise ResourceError(f"Unknown resource: {uri}")

        try:
            content = await resource.read()
            return [ReadResourceContents(content=content, mime_type=resource.mime_type)]
        except Exception as e:
            logger.error(f"Error reading resource {uri}: {e}")
            raise ResourceError(str(e))

    def add_tool(
        self,
        fn: AnyFunction,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            fn: The function to register as a tool
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does
        """
        self._tool_manager.add_tool(fn, name=name, description=description)

    def tool(
        self, name: str | None = None, description: str | None = None
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a tool.

        Tools can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and resource access.

        Args:
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does

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
            self.add_tool(fn, name=name, description=description)
            return fn

        return decorator

    def add_resource(self, resource: Resource) -> None:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add
        """
        self._resource_manager.add_resource(resource)

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a function as a resource.

        The function will be called when the resource is read to generate its content.
        The function can return:
        - str for text content
        - bytes for binary content
        - other types will be converted to JSON

        If the URI contains parameters (e.g. "resource://{param}") or the function
        has parameters, it will be registered as a template resource.

        Args:
            uri: URI for the resource (e.g. "resource://my-resource" or "resource://{param}")
            name: Optional name for the resource
            description: Optional description of the resource
            mime_type: Optional MIME type for the resource

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
            # Check if this should be a template
            has_uri_params = "{" in uri and "}" in uri
            has_func_params = bool(inspect.signature(fn).parameters)

            if has_uri_params or has_func_params:
                # Validate that URI params match function params
                uri_params = set(re.findall(r"{(\w+)}", uri))
                func_params = set(inspect.signature(fn).parameters.keys())

                if uri_params != func_params:
                    raise ValueError(
                        f"Mismatch between URI parameters {uri_params} "
                        f"and function parameters {func_params}"
                    )

                # Register as template
                self._resource_manager.add_template(
                    fn=fn,
                    uri_template=uri,
                    name=name,
                    description=description,
                    mime_type=mime_type or "text/plain",
                )
            else:
                # Register as regular resource
                resource = FunctionResource(
                    uri=AnyUrl(uri),
                    name=name,
                    description=description,
                    mime_type=mime_type or "text/plain",
                    fn=fn,
                )
                self.add_resource(resource)
            return fn

        return decorator

    def add_prompt(self, prompt: Prompt) -> None:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance to add
        """
        self._prompt_manager.add_prompt(prompt)

    def prompt(
        self, name: str | None = None, description: str | None = None
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a prompt.

        Args:
            name: Optional name for the prompt (defaults to function name)
            description: Optional description of what the prompt does

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
            prompt = Prompt.from_function(func, name=name, description=description)
            self.add_prompt(prompt)
            return func

        return decorator

    async def run_stdio_async(self) -> None:
        """Run the server using stdio transport."""
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )

    async def run_sse_async(self) -> None:
        """Run the server using SSE transport."""
        starlette_app = self.sse_app()

        config = uvicorn.Config(
            starlette_app,
            host=self.settings.host,
            port=self.settings.port,
            log_level=self.settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    def sse_app(self) -> Starlette:
        """Return an instance of the SSE server app."""
        sse = SseServerTransport(self.settings.message_path)

        async def handle_sse(request: Request) -> None:
            async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,  # type: ignore[reportPrivateUsage]
            ) as streams:
                await self._mcp_server.run(
                    streams[0],
                    streams[1],
                    self._mcp_server.create_initialization_options(),
                )

        return Starlette(
            debug=self.settings.debug,
            routes=[
                Route(self.settings.sse_path, endpoint=handle_sse),
                Mount(self.settings.message_path, app=sse.handle_post_message),
            ],
        )

    async def list_prompts(self) -> list[MCPPrompt]:
        """List all available prompts."""
        prompts = self._prompt_manager.list_prompts()
        return [
            MCPPrompt(
                name=prompt.name,
                description=prompt.description,
                arguments=[
                    MCPPromptArgument(
                        name=arg.name,
                        description=arg.description,
                        required=arg.required,
                    )
                    for arg in (prompt.arguments or [])
                ],
            )
            for prompt in prompts
        ]

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """Get a prompt by name with arguments."""
        try:
            messages = await self._prompt_manager.render_prompt(name, arguments)

            return GetPromptResult(messages=pydantic_core.to_jsonable_python(messages))
        except Exception as e:
            logger.error(f"Error getting prompt {name}: {e}")
            raise ValueError(str(e))

    def mount(self, prefix: str, app: "FastMCP") -> None:
        """Mount another FastMCP application with a given prefix.

        When an application is mounted:
        - The tools are imported with prefixed names
          Example: If app has a tool named "get_weather", it will be available as "weather/get_weather"
        - The resources are imported with prefixed URIs
          Example: If app has a resource with URI "weather://forecast", it will be available as "weather+weather://forecast"
        - The templates are imported with prefixed URI templates
          Example: If app has a template with URI "weather://location/{id}", it will be available as "weather+weather://location/{id}"
        - The prompts are imported with prefixed names
          Example: If app has a prompt named "weather_prompt", it will be available as "weather/weather_prompt"

        Args:
            prefix: The prefix to use for the mounted application
            app: The FastMCP application to mount
        """
        # Mount the app in the list of mounted apps
        self._mounted_apps[prefix] = app

        # Import tools from the mounted app with / delimiter
        tool_prefix = f"{prefix}/"
        self._tool_manager.import_tools(app._tool_manager, tool_prefix)

        # Import resources and templates from the mounted app with + delimiter
        resource_prefix = f"{prefix}+"
        self._resource_manager.import_resources(app._resource_manager, resource_prefix)
        self._resource_manager.import_templates(app._resource_manager, resource_prefix)

        # Import prompts with / delimiter
        prompt_prefix = f"{prefix}/"
        self._prompt_manager.import_prompts(app._prompt_manager, prompt_prefix)

        logger.info(f"Mounted app with prefix '{prefix}'")
        logger.debug(f"Imported tools with prefix '{tool_prefix}'")
        logger.debug(f"Imported resources with prefix '{resource_prefix}'")
        logger.debug(f"Imported templates with prefix '{resource_prefix}'")
        logger.debug(f"Imported prompts with prefix '{prompt_prefix}'")

    @classmethod
    async def as_proxy(
        cls, client: "Client | FastMCP", **settings: Any
    ) -> "FastMCPProxy":
        """
        Create a FastMCP proxy server from a client.

        This method creates a new FastMCP server instance that proxies requests to the provided client.
        It discovers the client's tools, resources, prompts, and templates, and creates corresponding
        components in the server that forward requests to the client.

        Args:
            client: The client to proxy requests to
            **settings: Additional settings for the FastMCP server

        Returns:
            A FastMCP server that proxies requests to the client
        """
        from fastmcp.client import Client

        from .proxy import FastMCPProxy

        if isinstance(client, Client):
            return await FastMCPProxy.from_client(client=client, **settings)

        elif isinstance(client, FastMCP):
            return await FastMCPProxy.from_server(server=client, **settings)

        else:
            raise ValueError(f"Unknown client type: {type(client)}")

    @classmethod
    def from_openapi(
        cls, openapi_spec: dict[str, Any], client: httpx.AsyncClient, **settings: Any
    ) -> "FastMCPOpenAPI":
        """
        Create a FastMCP server from an OpenAPI specification.
        """
        from .openapi import FastMCPOpenAPI

        return FastMCPOpenAPI(openapi_spec=openapi_spec, client=client, **settings)

    @classmethod
    def from_fastapi(
        cls, app: FastAPI, name: str | None = None, **settings: Any
    ) -> "FastMCPOpenAPI":
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


def _convert_to_content(
    result: Any,
    _process_as_single_item: bool = False,
) -> list[TextContent | ImageContent | EmbeddedResource]:
    """Convert a result to a sequence of content objects."""
    if result is None:
        return []

    if isinstance(result, TextContent | ImageContent | EmbeddedResource):
        return [result]

    if isinstance(result, Image):
        return [result.to_image_content()]

    if isinstance(result, list | tuple) and not _process_as_single_item:
        # if the result is a list, then it could either be a list of MCP types,
        # or a "regular" list that the tool is returning, or a mix of both.
        #
        # so we extract all the MCP types / images and convert them as individual content elements,
        # and aggregate the rest as a single content element

        mcp_types = []
        other_content = []

        for item in result:
            if isinstance(item, (TextContent, ImageContent, EmbeddedResource, Image)):
                mcp_types.append(_convert_to_content(item)[0])
            else:
                other_content.append(item)
        if other_content:
            other_content = _convert_to_content(
                other_content, _process_as_single_item=True
            )

        return other_content + mcp_types

    if not isinstance(result, str):
        try:
            result = json.dumps(pydantic_core.to_jsonable_python(result))
        except Exception:
            result = str(result)

    return [TextContent(type="text", text=result)]
