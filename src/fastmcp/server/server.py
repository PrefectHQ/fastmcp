"""FastMCP - A more ergonomic interface for MCP servers."""

import datetime
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from typing import TYPE_CHECKING, Any, Generic, Literal

import anyio
import httpx
import pydantic_core
import uvicorn
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.lowlevel.server import Server as MCPServer
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
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import Tool as MCPTool
from pydantic.networks import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route

import fastmcp
import fastmcp.settings
from fastmcp.exceptions import NotFoundError, ResourceError
from fastmcp.prompts import Prompt, PromptManager
from fastmcp.prompts.prompt import PromptResult
from fastmcp.resources import Resource, ResourceManager
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools import ToolManager
from fastmcp.tools.tool import Tool
from fastmcp.utilities.decorators import DecoratedFunction
from fastmcp.utilities.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.server.context import Context
    from fastmcp.server.openapi import FastMCPOpenAPI
    from fastmcp.server.proxy import FastMCPProxy

logger = get_logger(__name__)

NOT_FOUND = object()


class MountedServer:
    def __init__(
        self,
        prefix: str,
        server: "FastMCP",
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


class TimedCache:
    def __init__(self, expiration: datetime.timedelta):
        self.expiration = expiration
        self.cache: dict[Any, tuple[Any, datetime.datetime]] = {}

    def set(self, key: Any, value: Any) -> None:
        expires = datetime.datetime.now() + self.expiration
        self.cache[key] = (value, expires)

    def get(self, key: Any) -> Any:
        value = self.cache.get(key)
        if value is not None and value[1] > datetime.datetime.now():
            return value[0]
        else:
            return NOT_FOUND

    def clear(self) -> None:
        self.cache.clear()


@asynccontextmanager
async def default_lifespan(server: "FastMCP") -> AsyncIterator[Any]:
    """Default lifespan context manager that does nothing.

    Args:
        server: The server instance this lifespan is managing

    Returns:
        An empty context object
    """
    yield {}


def _lifespan_wrapper(
    app: "FastMCP",
    lifespan: Callable[["FastMCP"], AbstractAsyncContextManager[LifespanResultT]],
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
        lifespan: (
            Callable[["FastMCP"], AbstractAsyncContextManager[LifespanResultT]] | None
        ) = None,
        tags: set[str] | None = None,
        **settings: Any,
    ):
        self.tags: set[str] = tags or set()
        self.settings = fastmcp.settings.ServerSettings(**settings)
        self._cache = TimedCache(
            expiration=datetime.timedelta(
                seconds=self.settings.cache_expiration_seconds
            )
        )

        self._mounted_servers: dict[str, MountedServer] = {}

        if lifespan is None:
            lifespan = default_lifespan

        self._mcp_server = MCPServer[LifespanResultT](
            name=name or "FastMCP",
            instructions=instructions,
            lifespan=_lifespan_wrapper(self, lifespan),
        )
        self._tool_manager = ToolManager(
            duplicate_behavior=self.settings.on_duplicate_tools
        )
        self._resource_manager = ResourceManager(
            duplicate_behavior=self.settings.on_duplicate_resources
        )
        self._prompt_manager = PromptManager(
            duplicate_behavior=self.settings.on_duplicate_prompts
        )
        self.dependencies = self.settings.dependencies

        # Set up MCP protocol handlers
        self._setup_handlers()

        # Configure logging
        configure_logging(self.settings.log_level)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    @property
    def name(self) -> str:
        return self._mcp_server.name

    @property
    def instructions(self) -> str | None:
        return self._mcp_server.instructions

    async def run_async(
        self, transport: Literal["stdio", "sse"] | None = None, **transport_kwargs: Any
    ) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio" or "sse")
        """
        if transport is None:
            transport = "stdio"
        if transport not in ["stdio", "sse"]:
            raise ValueError(f"Unknown transport: {transport}")

        if transport == "stdio":
            await self.run_stdio_async(**transport_kwargs)
        else:  # transport == "sse"
            await self.run_sse_async(**transport_kwargs)

    def run(
        self, transport: Literal["stdio", "sse"] | None = None, **transport_kwargs: Any
    ) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("stdio" or "sse")
        """
        logger.info(f'Starting server "{self.name}"...')
        anyio.run(self.run_async, transport, **transport_kwargs)

    def _setup_handlers(self) -> None:
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self._mcp_list_tools)
        self._mcp_server.call_tool()(self._mcp_call_tool)
        self._mcp_server.list_resources()(self._mcp_list_resources)
        self._mcp_server.read_resource()(self._mcp_read_resource)
        self._mcp_server.list_prompts()(self._mcp_list_prompts)
        self._mcp_server.get_prompt()(self._mcp_get_prompt)
        self._mcp_server.list_resource_templates()(self._mcp_list_resource_templates)

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

    async def get_tools(self) -> dict[str, Tool]:
        """Get all registered tools, indexed by registered key."""
        if (tools := self._cache.get("tools")) is NOT_FOUND:
            tools = {}
            for server in self._mounted_servers.values():
                server_tools = await server.get_tools()
                tools.update(server_tools)
            tools.update(self._tool_manager.get_tools())
            self._cache.set("tools", tools)
        return tools

    async def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, indexed by registered key."""
        if (resources := self._cache.get("resources")) is NOT_FOUND:
            resources = {}
            for server in self._mounted_servers.values():
                server_resources = await server.get_resources()
                resources.update(server_resources)
            resources.update(self._resource_manager.get_resources())
            self._cache.set("resources", resources)
        return resources

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered resource templates, indexed by registered key."""
        if (templates := self._cache.get("resource_templates")) is NOT_FOUND:
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
        if (prompts := self._cache.get("prompts")) is NOT_FOUND:
            prompts = {}
            for server in self._mounted_servers.values():
                server_prompts = await server.get_prompts()
                prompts.update(server_prompts)
            prompts.update(self._prompt_manager.get_prompts())
            self._cache.set("prompts", prompts)
        return prompts

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
        if self._tool_manager.has_tool(key):
            context = self.get_context()
            result = await self._tool_manager.call_tool(key, arguments, context=context)

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
        if self._resource_manager.has_resource(uri):
            resource = await self._resource_manager.get_resource(uri)
            try:
                content = await resource.read()
                return [
                    ReadResourceContents(content=content, mime_type=resource.mime_type)
                ]
            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}")
                raise ResourceError(str(e))
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
        if self._prompt_manager.has_prompt(name):
            messages = await self._prompt_manager.render_prompt(name, arguments)
            return GetPromptResult(messages=pydantic_core.to_jsonable_python(messages))
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
    ) -> None:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            fn: The function to register as a tool
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does
            tags: Optional set of tags for categorizing the tool
        """
        self._tool_manager.add_tool_from_fn(
            fn, name=name, description=description, tags=tags
        )
        self._cache.clear()

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> Callable[[AnyFunction], AnyFunction]:
        """Decorator to register a tool.

        Tools can optionally request a Context object by adding a parameter with the
        Context type annotation. The context provides access to MCP capabilities like
        logging, progress reporting, and resource access.

        Args:
            name: Optional name for the tool (defaults to function name)
            description: Optional description of what the tool does
            tags: Optional set of tags for categorizing the tool

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
            self.add_tool(fn, name=name, description=description, tags=tags)
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
                self._mcp_server.create_initialization_options(),
            )

    async def run_sse_async(
        self,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
    ) -> None:
        """Run the server using SSE transport."""
        starlette_app = self.sse_app()

        config = uvicorn.Config(
            starlette_app,
            host=host or self.settings.host,
            port=port or self.settings.port,
            log_level=log_level or self.settings.log_level.lower(),
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

    def mount(
        self,
        prefix: str,
        server: "FastMCP",
        tool_separator: str | None = None,
        resource_separator: str | None = None,
        prompt_separator: str | None = None,
    ) -> None:
        """
        Mount another FastMCP server on a given prefix.
        """
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
        server: "FastMCP",
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

        When an server is mounted: - The tools are imported with prefixed names
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
    ) -> "FastMCPOpenAPI":
        """
        Create a FastMCP server from an OpenAPI specification.
        """
        from .openapi import FastMCPOpenAPI

        return FastMCPOpenAPI(openapi_spec=openapi_spec, client=client, **settings)

    @classmethod
    def from_fastapi(
        cls, app: "Any", name: str | None = None, **settings: Any
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

    @classmethod
    def from_client(cls, client: "Client", **settings: Any) -> "FastMCPProxy":
        """
        Create a FastMCP proxy server from a FastMCP client.
        """
        from fastmcp.server.proxy import FastMCPProxy

        return FastMCPProxy(client=client, **settings)
