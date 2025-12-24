"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import asyncio
import re
import secrets
import warnings
import weakref
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
    suppress,
)
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, cast, overload

import anyio
import httpx
import mcp.types
import uvicorn
from docket import Docket, Worker
from mcp.server.lowlevel.server import LifespanResultT, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import (
    Annotations,
    AnyFunction,
    CallToolRequestParams,
    ContentBlock,
    GetPromptResult,
    ToolAnnotations,
)
from mcp.types import Prompt as SDKPrompt
from mcp.types import Resource as SDKResource
from mcp.types import ResourceTemplate as SDKResourceTemplate
from mcp.types import Tool as SDKTool
from pydantic import AnyUrl
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route
from typing_extensions import Self

import fastmcp
import fastmcp.server
from fastmcp.exceptions import (
    DisabledError,
    FastMCPError,
    NotFoundError,
    PromptError,
    ResourceError,
    ToolError,
    ValidationError,
)
from fastmcp.mcp_config import MCPConfig
from fastmcp.prompts import Prompt
from fastmcp.prompts.prompt import FunctionPrompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceContent
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth import AuthProvider
from fastmcp.server.event_store import EventStore
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers import LocalProvider, Provider
from fastmcp.server.tasks.capabilities import get_task_capabilities
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.settings import DuplicateBehavior as DuplicateBehaviorSetting
from fastmcp.settings import Settings
from fastmcp.tools.tool import FunctionTool, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger, temporary_log_level
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.client.client import FastMCP1Server
    from fastmcp.client.sampling import SamplingHandler
    from fastmcp.client.transports import ClientTransport, ClientTransportT
    from fastmcp.server.providers.openapi import ComponentFn as OpenAPIComponentFn
    from fastmcp.server.providers.openapi import RouteMap
    from fastmcp.server.providers.openapi import RouteMapFn as OpenAPIRouteMapFn
    from fastmcp.server.providers.proxy import FastMCPProxy
    from fastmcp.tools.tool import ToolResultSerializerType

logger = get_logger(__name__)


DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]


def _resolve_on_duplicate(
    on_duplicate: DuplicateBehavior | None,
    on_duplicate_tools: DuplicateBehavior | None,
    on_duplicate_resources: DuplicateBehavior | None,
    on_duplicate_prompts: DuplicateBehavior | None,
) -> DuplicateBehavior:
    """Resolve on_duplicate from deprecated per-type params.

    Takes the most strict value if multiple are provided.
    Delete this function when removing deprecated params.
    """
    strictness_order: list[DuplicateBehavior] = ["error", "warn", "replace", "ignore"]
    deprecated_values: list[DuplicateBehavior] = []

    deprecated_params: list[tuple[str, DuplicateBehavior | None]] = [
        ("on_duplicate_tools", on_duplicate_tools),
        ("on_duplicate_resources", on_duplicate_resources),
        ("on_duplicate_prompts", on_duplicate_prompts),
    ]
    for name, value in deprecated_params:
        if value is not None:
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    f"{name} is deprecated, use on_duplicate instead",
                    DeprecationWarning,
                    stacklevel=4,
                )
            deprecated_values.append(value)

    if on_duplicate is None and deprecated_values:
        return min(deprecated_values, key=lambda x: strictness_order.index(x))

    return on_duplicate or "warn"


Transport = Literal["stdio", "http", "sse", "streamable-http"]

# Compiled URI parsing regex to split a URI into protocol and path components
URI_PATTERN = re.compile(r"^([^:]+://)(.*?)$")

LifespanCallable = Callable[
    ["FastMCP[LifespanResultT]"], AbstractAsyncContextManager[LifespanResultT]
]


@asynccontextmanager
async def default_lifespan(server: FastMCP[LifespanResultT]) -> AsyncIterator[Any]:
    """Default lifespan context manager that does nothing.

    Args:
        server: The server instance this lifespan is managing

    Returns:
        An empty dictionary as the lifespan result.
    """
    yield {}


def _lifespan_proxy(
    fastmcp_server: FastMCP[LifespanResultT],
) -> Callable[
    [LowLevelServer[LifespanResultT]], AbstractAsyncContextManager[LifespanResultT]
]:
    @asynccontextmanager
    async def wrap(
        low_level_server: LowLevelServer[LifespanResultT],
    ) -> AsyncIterator[LifespanResultT]:
        if fastmcp_server._lifespan is default_lifespan:
            yield {}
            return

        if not fastmcp_server._lifespan_result_set:
            raise RuntimeError(
                "FastMCP server has a lifespan defined but no lifespan result is set, which means the server's context manager was not entered. "
                + " Are you running the server in a way that supports lifespans? If so, please file an issue at https://github.com/jlowin/fastmcp/issues."
            )

        yield fastmcp_server._lifespan_result

    return wrap


class FastMCP(Generic[LifespanResultT]):
    def __init__(
        self,
        name: str | None = None,
        instructions: str | None = None,
        *,
        version: str | None = None,
        website_url: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        auth: AuthProvider | NotSetT | None = NotSet,
        middleware: Sequence[Middleware] | None = None,
        providers: Sequence[Provider] | None = None,
        lifespan: LifespanCallable | None = None,
        mask_error_details: bool | None = None,
        tools: Sequence[Tool | Callable[..., Any]] | None = None,
        tool_transformations: Mapping[str, ToolTransformConfig] | None = None,
        tool_serializer: ToolResultSerializerType | None = None,
        include_tags: Collection[str] | None = None,
        exclude_tags: Collection[str] | None = None,
        include_fastmcp_meta: bool | None = None,
        on_duplicate: DuplicateBehavior | None = None,
        strict_input_validation: bool | None = None,
        tasks: bool | None = None,
        # ---
        # --- DEPRECATED parameters ---
        # ---
        on_duplicate_tools: DuplicateBehavior | None = None,
        on_duplicate_resources: DuplicateBehavior | None = None,
        on_duplicate_prompts: DuplicateBehavior | None = None,
        log_level: str | None = None,
        debug: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        sse_path: str | None = None,
        message_path: str | None = None,
        streamable_http_path: str | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        sampling_handler: SamplingHandler | None = None,
        sampling_handler_behavior: Literal["always", "fallback"] | None = None,
    ):
        # Resolve on_duplicate from deprecated params (delete when removing deprecation)
        self._on_duplicate: DuplicateBehaviorSetting = _resolve_on_duplicate(
            on_duplicate,
            on_duplicate_tools,
            on_duplicate_resources,
            on_duplicate_prompts,
        )

        # Resolve server default for background task support
        self._support_tasks_by_default: bool = tasks if tasks is not None else False

        # Docket instance (set during lifespan for cross-task access)
        self._docket = None

        self._additional_http_routes: list[BaseRoute] = []

        # Create LocalProvider for local components
        self._local_provider: LocalProvider = LocalProvider(
            on_duplicate=self._on_duplicate
        )

        # Apply tool transformations to LocalProvider
        if tool_transformations:
            for tool_name, transformation in tool_transformations.items():
                self._local_provider.add_tool_transformation(tool_name, transformation)

        # LocalProvider is always first in the provider list
        self._providers: list[Provider] = [
            self._local_provider,
            *(providers or []),
        ]

        # Store mask_error_details for execution error handling
        self._mask_error_details: bool = (
            mask_error_details
            if mask_error_details is not None
            else fastmcp.settings.mask_error_details
        )
        self._tool_serializer: Callable[[Any], str] | None = tool_serializer

        self._lifespan: LifespanCallable[LifespanResultT] = lifespan or default_lifespan
        self._lifespan_result: LifespanResultT | None = None
        self._lifespan_result_set: bool = False
        self._started: asyncio.Event = asyncio.Event()

        # Generate random ID if no name provided
        self._mcp_server: LowLevelServer[LifespanResultT, Any] = LowLevelServer[
            LifespanResultT
        ](
            fastmcp=self,
            name=name or self.generate_name(),
            version=version or fastmcp.__version__,
            instructions=instructions,
            website_url=website_url,
            icons=icons,
            lifespan=_lifespan_proxy(fastmcp_server=self),
        )

        # if auth is `NotSet`, try to create a provider from the environment
        if auth is NotSet:
            if fastmcp.settings.server_auth is not None:
                # server_auth_class returns the class itself, not an instance
                auth_class = cast(
                    type[AuthProvider], fastmcp.settings.server_auth_class
                )
                auth = auth_class()
            else:
                auth = None
        self.auth: AuthProvider | None = auth

        if tools:
            for tool in tools:
                if not isinstance(tool, Tool):
                    tool = Tool.from_function(tool, serializer=self._tool_serializer)
                self.add_tool(tool)

        self.include_tags: set[str] | None = (
            set(include_tags) if include_tags is not None else None
        )
        self.exclude_tags: set[str] | None = (
            set(exclude_tags) if exclude_tags is not None else None
        )

        self.strict_input_validation: bool = (
            strict_input_validation
            if strict_input_validation is not None
            else fastmcp.settings.strict_input_validation
        )

        self.middleware: list[Middleware] = list(middleware or [])

        # Set up MCP protocol handlers
        self._setup_handlers()

        self.sampling_handler: SamplingHandler | None = sampling_handler
        self.sampling_handler_behavior: Literal["always", "fallback"] = (
            sampling_handler_behavior or "fallback"
        )

        self.include_fastmcp_meta: bool = (
            include_fastmcp_meta
            if include_fastmcp_meta is not None
            else fastmcp.settings.include_fastmcp_meta
        )

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

    @instructions.setter
    def instructions(self, value: str | None) -> None:
        self._mcp_server.instructions = value

    @property
    def version(self) -> str | None:
        return self._mcp_server.version

    @property
    def website_url(self) -> str | None:
        return self._mcp_server.website_url

    @property
    def icons(self) -> list[mcp.types.Icon]:
        if self._mcp_server.icons is None:
            return []
        else:
            return list(self._mcp_server.icons)

    @property
    def docket(self) -> Docket | None:
        """Get the Docket instance if Docket support is enabled.

        Returns None if Docket is not enabled or server hasn't been started yet.
        """
        return self._docket

    @asynccontextmanager
    async def _docket_lifespan(self) -> AsyncIterator[None]:
        """Manage Docket instance and Worker for background task execution."""
        from fastmcp import settings

        # Set FastMCP server in ContextVar so CurrentFastMCP can access it (use weakref to avoid reference cycles)
        from fastmcp.server.dependencies import (
            _current_docket,
            _current_server,
            _current_worker,
        )

        server_token = _current_server.set(weakref.ref(self))

        try:
            # Create Docket instance using configured name and URL
            async with Docket(
                name=settings.docket.name,
                url=settings.docket.url,
            ) as docket:
                # Store on server instance for cross-task access (FastMCPTransport)
                self._docket = docket

                # Register task-enabled components from all providers (LocalProvider first)
                for provider in self._providers:
                    try:
                        for component in await provider.get_tasks():
                            component.register_with_docket(docket)
                    except Exception as e:
                        provider_name = getattr(
                            provider, "server", provider
                        ).__class__.__name__
                        logger.warning(
                            f"Failed to register tasks from provider {provider_name!r}: {e}"
                        )
                        if fastmcp.settings.mounted_components_raise_on_load_error:
                            raise

                # Set Docket in ContextVar so CurrentDocket can access it
                docket_token = _current_docket.set(docket)
                try:
                    # Build worker kwargs from settings
                    worker_kwargs: dict[str, Any] = {
                        "concurrency": settings.docket.concurrency,
                        "redelivery_timeout": settings.docket.redelivery_timeout,
                        "reconnection_delay": settings.docket.reconnection_delay,
                    }
                    if settings.docket.worker_name:
                        worker_kwargs["name"] = settings.docket.worker_name

                    # Create and start Worker
                    async with Worker(docket, **worker_kwargs) as worker:  # type: ignore[arg-type]
                        # Set Worker in ContextVar so CurrentWorker can access it
                        worker_token = _current_worker.set(worker)
                        try:
                            worker_task = asyncio.create_task(worker.run_forever())
                            try:
                                yield
                            finally:
                                worker_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await worker_task
                        finally:
                            _current_worker.reset(worker_token)
                finally:
                    # Reset ContextVar
                    _current_docket.reset(docket_token)
                    # Clear instance attribute
                    self._docket = None
        finally:
            # Reset server ContextVar
            _current_server.reset(server_token)

    @asynccontextmanager
    async def _lifespan_manager(self) -> AsyncIterator[None]:
        if self._lifespan_result_set:
            yield
            return

        async with (
            self._lifespan(self) as user_lifespan_result,
            self._docket_lifespan(),
        ):
            self._lifespan_result = user_lifespan_result
            self._lifespan_result_set = True

            async with AsyncExitStack[bool | None]() as stack:
                # Start lifespans for all providers
                for provider in self._providers:
                    await stack.enter_async_context(provider.lifespan())

                self._started.set()
                try:
                    yield
                finally:
                    self._started.clear()

        self._lifespan_result_set = False
        self._lifespan_result = None

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
        """Set up core MCP protocol handlers.

        We override the SDK's default handlers for tools/call, resources/read,
        and prompts/get to add task-augmented execution support (SEP-1686).

        The SDK's decorators have different capabilities:
        - call_tool: Supports CreateTaskResult returns AND validate_input
        - read_resource: Does NOT support CreateTaskResult
        - get_prompt: Does NOT support CreateTaskResult

        So we use the SDK decorator for tools (to get input validation), but
        register custom handlers for resources and prompts.
        """
        self._mcp_server.list_tools()(self._list_tools_mcp)
        self._mcp_server.list_resources()(self._list_resources_mcp)
        self._mcp_server.list_resource_templates()(self._list_resource_templates_mcp)
        self._mcp_server.list_prompts()(self._list_prompts_mcp)

        # Tools: SDK decorator provides validate_input + CreateTaskResult support
        self._mcp_server.call_tool(validate_input=self.strict_input_validation)(
            self._call_tool_mcp
        )

        # Resources/Prompts: Custom handlers (SDK decorators don't support CreateTaskResult)
        self._mcp_server.request_handlers[mcp.types.ReadResourceRequest] = (
            self._read_resource_handler
        )
        self._mcp_server.request_handlers[mcp.types.GetPromptRequest] = (
            self._get_prompt_handler
        )

        # Register SEP-1686 task protocol handlers
        self._setup_task_protocol_handlers()

    def _setup_task_protocol_handlers(self) -> None:
        """Register SEP-1686 task protocol handlers with SDK."""
        from mcp.types import (
            CancelTaskRequest,
            GetTaskPayloadRequest,
            GetTaskRequest,
            ListTasksRequest,
            ServerResult,
        )

        from fastmcp.server.tasks.requests import (
            tasks_cancel_handler,
            tasks_get_handler,
            tasks_list_handler,
            tasks_result_handler,
        )

        # Manually register handlers (SDK decorators fail with locally-defined functions)
        # SDK expects handlers that receive Request objects and return ServerResult

        async def handle_get_task(req: GetTaskRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_get_handler(self, params)
            return ServerResult(result)

        async def handle_get_task_result(req: GetTaskPayloadRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_result_handler(self, params)
            return ServerResult(result)

        async def handle_list_tasks(req: ListTasksRequest) -> ServerResult:
            params = (
                req.params.model_dump(by_alias=True, exclude_none=True)
                if req.params
                else {}
            )
            result = await tasks_list_handler(self, params)
            return ServerResult(result)

        async def handle_cancel_task(req: CancelTaskRequest) -> ServerResult:
            params = req.params.model_dump(by_alias=True, exclude_none=True)
            result = await tasks_cancel_handler(self, params)
            return ServerResult(result)

        # Register directly with SDK (same as what decorators do internally)
        self._mcp_server.request_handlers[GetTaskRequest] = handle_get_task
        self._mcp_server.request_handlers[GetTaskPayloadRequest] = (
            handle_get_task_result
        )
        self._mcp_server.request_handlers[ListTasksRequest] = handle_list_tasks
        self._mcp_server.request_handlers[CancelTaskRequest] = handle_cancel_task

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

    def add_provider(self, provider: Provider) -> None:
        """Add a provider for dynamic tools, resources, and prompts.

        Providers are queried in registration order. The first provider to return
        a non-None result wins. Static components (registered via decorators)
        always take precedence over providers.

        Args:
            provider: A Provider instance that will provide components dynamically.
        """
        self._providers.append(provider)

    async def get_tools(self) -> dict[str, Tool]:
        """Get all tools (unfiltered), including from providers, indexed by name.

        Iterates through all providers (LocalProvider first) and collects tools.
        First provider wins for duplicate names.
        """
        all_tools: dict[str, Tool] = {}
        for provider in self._providers:
            try:
                provider_tools = await provider.list_tools()
                for tool in provider_tools:
                    if tool.name not in all_tools:
                        all_tools[tool.name] = tool
            except Exception as e:
                provider_name = getattr(provider, "server", provider).__class__.__name__
                logger.warning(
                    f"Failed to get tools from provider {provider_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue
        return all_tools

    async def get_tool(self, name: str) -> Tool:
        """Get a tool by name.

        Iterates through all providers (LocalProvider first) to find the tool.
        First provider wins.
        """
        for provider in self._providers:
            try:
                tool = await provider.get_tool(name)
                if tool is not None:
                    return tool
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown tool: {name}")

    async def _get_resource_or_template_or_none(
        self, uri: str
    ) -> Resource | ResourceTemplate | None:
        """Get a resource or template by URI. Returns None if not found.

        Returns the original ResourceTemplate (not a Resource created from it)
        to preserve the registered function for task execution.

        Iterates through all providers (LocalProvider first).
        First provider wins. Checks concrete resources first, then templates.
        """
        # First pass: check concrete resources from all providers
        for provider in self._providers:
            try:
                resource = await provider.get_resource(uri)
                if resource is not None:
                    return resource
            except NotFoundError:
                continue

        # Second pass: check templates from all providers
        for provider in self._providers:
            try:
                template = await provider.get_resource_template(uri)
                if template is not None:
                    return template
            except NotFoundError:
                continue

        return None

    async def get_resources(self) -> dict[str, Resource]:
        """Get all resources (unfiltered), including from providers, indexed by URI.

        Iterates through all providers (LocalProvider first) and collects resources.
        First provider wins for duplicate URIs.
        """
        all_resources: dict[str, Resource] = {}
        for provider in self._providers:
            try:
                provider_resources = await provider.list_resources()
                for resource in provider_resources:
                    uri = str(resource.uri)
                    if uri not in all_resources:
                        all_resources[uri] = resource
            except Exception as e:
                provider_name = getattr(provider, "server", provider).__class__.__name__
                logger.warning(
                    f"Failed to get resources from provider {provider_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue
        return all_resources

    async def get_resource(self, uri: str) -> Resource:
        """Get a resource by URI.

        Iterates through all providers (LocalProvider first) to find the resource.
        First provider wins.
        """
        for provider in self._providers:
            try:
                resource = await provider.get_resource(uri)
                if resource is not None:
                    return resource
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown resource: {uri}")

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all resource templates (unfiltered), including from providers, indexed by uri_template.

        Iterates through all providers (LocalProvider first) and collects templates.
        First provider wins for duplicate uri_templates.
        """
        all_templates: dict[str, ResourceTemplate] = {}
        for provider in self._providers:
            try:
                provider_templates = await provider.list_resource_templates()
                for template in provider_templates:
                    if template.uri_template not in all_templates:
                        all_templates[template.uri_template] = template
            except Exception as e:
                provider_name = getattr(provider, "server", provider).__class__.__name__
                logger.warning(
                    f"Failed to get resource templates from provider {provider_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue
        return all_templates

    async def get_resource_template(self, uri: str) -> ResourceTemplate:
        """Get a resource template that matches the given URI.

        Iterates through all providers (LocalProvider first) to find the template.
        First provider wins.
        """
        for provider in self._providers:
            try:
                template = await provider.get_resource_template(uri)
                if template is not None:
                    return template
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown resource template: {uri}")

    async def get_prompts(self) -> dict[str, Prompt]:
        """Get all prompts (unfiltered), including from providers, indexed by name.

        Iterates through all providers (LocalProvider first) and collects prompts.
        First provider wins for duplicate names.
        """
        all_prompts: dict[str, Prompt] = {}
        for provider in self._providers:
            try:
                provider_prompts = await provider.list_prompts()
                for prompt in provider_prompts:
                    if prompt.name not in all_prompts:
                        all_prompts[prompt.name] = prompt
            except Exception as e:
                provider_name = getattr(provider, "server", provider).__class__.__name__
                logger.warning(
                    f"Failed to get prompts from provider {provider_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue
        return all_prompts

    async def get_prompt(self, name: str) -> Prompt:
        """Get a prompt by name.

        Iterates through all providers (LocalProvider first) to find the prompt.
        First provider wins.
        """
        for provider in self._providers:
            try:
                prompt = await provider.get_prompt(name)
                if prompt is not None:
                    return prompt
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown prompt: {name}")

    async def get_component(
        self, key: str
    ) -> Tool | Resource | ResourceTemplate | Prompt:
        """Get a component by its prefixed key.

        Iterates through all providers (LocalProvider first) to find the component.
        First provider wins.

        Args:
            key: The prefixed key (e.g., "tool:name", "resource:uri", "template:uri").

        Returns:
            The component if found.

        Raises:
            NotFoundError: If no component is found with the given key.
        """
        for provider in self._providers:
            try:
                component = await provider.get_component(key)
                if component is not None:
                    return component
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown component: {key}")

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
            path: URL path for the route (e.g., "/auth/callback")
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

    def _get_additional_http_routes(self) -> list[BaseRoute]:
        """Get all additional HTTP routes including from providers.

        Returns a list of all custom HTTP routes from this server and
        from all providers that have HTTP routes (e.g., FastMCPProvider).

        Returns:
            List of Starlette BaseRoute objects
        """
        return list(self._additional_http_routes)

    async def _list_tools_mcp(self) -> list[SDKTool]:
        """
        List all available tools, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_tools")

        async with fastmcp.server.context.Context(fastmcp=self):
            tools = await self._list_tools_middleware()
            return [
                tool.to_mcp_tool(
                    name=tool.name,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for tool in tools
            ]

    async def _list_tools_middleware(self) -> list[Tool]:
        """
        List all available tools, applying MCP middleware.
        """

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
            return list(
                await self._apply_middleware(
                    context=mw_context, call_next=self._list_tools
                )
            )

    async def _list_tools(
        self,
        context: MiddlewareContext[mcp.types.ListToolsRequest],
    ) -> list[Tool]:
        """
        List all available tools.

        Iterates through all providers (LocalProvider first) and collects tools.
        First provider wins for duplicate keys.
        """
        all_tools: dict[str, Tool] = {}
        for provider in self._providers:
            try:
                provider_tools = await provider.list_tools()
                for tool in provider_tools:
                    if (
                        self._should_enable_component(tool)
                        and tool.key not in all_tools
                    ):
                        all_tools[tool.key] = tool
            except Exception:
                logger.exception("Error listing tools from provider")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
        return list(all_tools.values())

    async def _list_resources_mcp(self) -> list[SDKResource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resources")

        async with fastmcp.server.context.Context(fastmcp=self):
            resources = await self._list_resources_middleware()
            return [
                resource.to_mcp_resource(
                    uri=str(resource.uri),
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for resource in resources
            ]

    async def _list_resources_middleware(self) -> list[Resource]:
        """
        List all available resources, applying MCP middleware.
        """

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
            return list(
                await self._apply_middleware(
                    context=mw_context, call_next=self._list_resources
                )
            )

    async def _list_resources(
        self,
        context: MiddlewareContext[dict[str, Any]],
    ) -> list[Resource]:
        """
        List all available resources.

        Iterates through all providers (LocalProvider first) and collects resources.
        First provider wins for duplicate keys.
        """
        all_resources: dict[str, Resource] = {}
        for provider in self._providers:
            try:
                provider_resources = await provider.list_resources()
                for resource in provider_resources:
                    if (
                        self._should_enable_component(resource)
                        and resource.key not in all_resources
                    ):
                        all_resources[resource.key] = resource
            except Exception:
                logger.exception("Error listing resources from provider")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
        return list(all_resources.values())

    async def _list_resource_templates_mcp(self) -> list[SDKResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resource_templates")

        async with fastmcp.server.context.Context(fastmcp=self):
            templates = await self._list_resource_templates_middleware()
            return [
                template.to_mcp_template(
                    uriTemplate=template.uri_template,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for template in templates
            ]

    async def _list_resource_templates_middleware(self) -> list[ResourceTemplate]:
        """
        List all available resource templates, applying MCP middleware.

        """

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
            return list(
                await self._apply_middleware(
                    context=mw_context, call_next=self._list_resource_templates
                )
            )

    async def _list_resource_templates(
        self,
        context: MiddlewareContext[dict[str, Any]],
    ) -> list[ResourceTemplate]:
        """
        List all available resource templates.

        Iterates through all providers (LocalProvider first) and collects templates.
        First provider wins for duplicate keys.
        """
        all_templates: dict[str, ResourceTemplate] = {}
        for provider in self._providers:
            try:
                provider_templates = await provider.list_resource_templates()
                for template in provider_templates:
                    if (
                        self._should_enable_component(template)
                        and template.key not in all_templates
                    ):
                        all_templates[template.key] = template
            except Exception:
                logger.exception("Error listing resource templates from provider")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
        return list(all_templates.values())

    async def _list_prompts_mcp(self) -> list[SDKPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_prompts")

        async with fastmcp.server.context.Context(fastmcp=self):
            prompts = await self._list_prompts_middleware()
            return [
                prompt.to_mcp_prompt(
                    name=prompt.name,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for prompt in prompts
            ]

    async def _list_prompts_middleware(self) -> list[Prompt]:
        """
        List all available prompts, applying MCP middleware.

        """

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
            return list(
                await self._apply_middleware(
                    context=mw_context, call_next=self._list_prompts
                )
            )

    async def _list_prompts(
        self,
        context: MiddlewareContext[mcp.types.ListPromptsRequest],
    ) -> list[Prompt]:
        """
        List all available prompts.

        Iterates through all providers (LocalProvider first) and collects prompts.
        First provider wins for duplicate keys.
        """
        all_prompts: dict[str, Prompt] = {}
        for provider in self._providers:
            try:
                provider_prompts = await provider.list_prompts()
                for prompt in provider_prompts:
                    if (
                        self._should_enable_component(prompt)
                        and prompt.key not in all_prompts
                    ):
                        all_prompts[prompt.key] = prompt
            except Exception:
                logger.exception("Error listing prompts from provider")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
        return list(all_prompts.values())

    async def _call_tool_mcp(
        self, key: str, arguments: dict[str, Any]
    ) -> (
        list[ContentBlock]
        | tuple[list[ContentBlock], dict[str, Any]]
        | mcp.types.CallToolResult
        | mcp.types.CreateTaskResult
    ):
        """
        Handle MCP 'callTool' requests.

        Sets task metadata contextvar and runs middleware. The tool's _run() method
        handles the backgrounding decision, ensuring middleware runs before Docket.

        Args:
            key: The name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Tool result or CreateTaskResult for background execution
        """
        from fastmcp.server.dependencies import _docket_fn_key, _task_metadata

        logger.debug(
            f"[{self.name}] Handler called: call_tool %s with %s", key, arguments
        )

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Extract SEP-1686 task metadata from request context
                task_meta_dict: dict[str, Any] | None = None
                try:
                    ctx = self._mcp_server.request_context
                    if ctx.experimental.is_task:
                        task_meta = ctx.experimental.task_metadata
                        task_meta_dict = task_meta.model_dump(exclude_none=True)
                except (AttributeError, LookupError):
                    pass

                # Set contextvars so tool._run() can access them
                task_token = _task_metadata.set(task_meta_dict)
                key_token = _docket_fn_key.set(Tool.make_key(key))
                try:
                    # Middleware always runs - tool._run() handles backgrounding
                    result = await self._call_tool_middleware(key, arguments)

                    # Result could be CreateTaskResult (from nested tool._run())
                    if isinstance(result, mcp.types.CreateTaskResult):
                        return result
                    return result.to_mcp_result()
                finally:
                    _task_metadata.reset(task_token)
                    _docket_fn_key.reset(key_token)

            except DisabledError as e:
                raise NotFoundError(f"Unknown tool: {key}") from e
            except NotFoundError as e:
                raise NotFoundError(f"Unknown tool: {key}") from e

    async def _read_resource_handler(
        self, req: mcp.types.ReadResourceRequest
    ) -> mcp.types.ServerResult:
        """Handle resources/read requests with task-augmented execution support.

        This is a custom handler because the SDK's read_resource decorator
        does not support returning CreateTaskResult for background tasks.
        """
        from fastmcp.server.dependencies import _docket_fn_key, _task_metadata

        uri = req.params.uri

        # Check for task metadata via SDK's request context
        task_meta_dict: dict[str, Any] | None = None
        try:
            ctx = self._mcp_server.request_context
            if ctx.experimental.is_task:
                task_meta = ctx.experimental.task_metadata
                task_meta_dict = task_meta.model_dump(exclude_none=True)
        except (AttributeError, LookupError):
            pass

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Set contextvars so Resource._read() can access them
                task_token = _task_metadata.set(task_meta_dict)
                key_token = _docket_fn_key.set(Resource.make_key(str(uri)))
                try:
                    # Middleware always runs - Resource._read() handles backgrounding
                    result = await self._read_resource_middleware(uri)

                    # Result could be CreateTaskResult (from nested Resource._read())
                    if isinstance(result, mcp.types.CreateTaskResult):
                        return mcp.types.ServerResult(result)

                    # Normal synchronous result
                    mcp_contents = [
                        item.to_mcp_resource_contents(uri) for item in result
                    ]
                    return mcp.types.ServerResult(
                        mcp.types.ReadResourceResult(contents=mcp_contents)
                    )
                finally:
                    _task_metadata.reset(task_token)
                    _docket_fn_key.reset(key_token)

            except DisabledError as e:
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e
            except NotFoundError as e:
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e

    async def _get_prompt_handler(
        self, req: mcp.types.GetPromptRequest
    ) -> mcp.types.ServerResult:
        """Handle prompts/get requests with task-augmented execution support.

        This is a custom handler because the SDK's get_prompt decorator
        does not support returning CreateTaskResult for background tasks.
        """
        from fastmcp.server.dependencies import _docket_fn_key, _task_metadata

        name = req.params.name
        arguments = req.params.arguments

        # Check for task metadata via SDK's request context
        task_meta_dict: dict[str, Any] | None = None
        try:
            ctx = self._mcp_server.request_context
            if ctx.experimental.is_task:
                task_meta = ctx.experimental.task_metadata
                task_meta_dict = task_meta.model_dump(exclude_none=True)
        except (AttributeError, LookupError):
            pass

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Set contextvars so Prompt._render() can access them
                task_token = _task_metadata.set(task_meta_dict)
                key_token = _docket_fn_key.set(Prompt.make_key(name))
                try:
                    # Middleware always runs - Prompt._render() handles backgrounding
                    result = await self._get_prompt_content_middleware(name, arguments)

                    # Result could be CreateTaskResult (from nested Prompt._render())
                    if isinstance(result, mcp.types.CreateTaskResult):
                        return mcp.types.ServerResult(result)

                    # Normal synchronous result
                    return mcp.types.ServerResult(result.to_mcp_prompt_result())
                finally:
                    _task_metadata.reset(task_token)
                    _docket_fn_key.reset(key_token)

            except DisabledError as e:
                raise NotFoundError(f"Unknown prompt: {name!r}") from e
            except NotFoundError as e:
                raise NotFoundError(f"Unknown prompt: {name!r}") from e

    async def _call_tool_middleware(
        self,
        key: str,
        arguments: dict[str, Any],
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """
        Applies this server's middleware and delegates the filtered call to the manager.

        Returns ToolResult for synchronous execution, or CreateTaskResult if the
        tool was submitted to Docket for background execution.
        """

        mw_context = MiddlewareContext[CallToolRequestParams](
            message=mcp.types.CallToolRequestParams(name=key, arguments=arguments),
            source="client",
            type="request",
            method="tools/call",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        return await self._apply_middleware(
            context=mw_context, call_next=self._call_tool
        )

    async def _call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """
        Call a tool.

        Iterates through all providers (LocalProvider first) to find the tool.
        First provider wins.
        """
        tool_name = context.message.name

        for provider in self._providers:
            tool = await provider.get_tool(tool_name)
            if tool is not None and self._should_enable_component(tool):
                return await self._execute_tool(
                    tool, tool_name, context.message.arguments or {}
                )

        raise NotFoundError(f"Unknown tool: {tool_name!r}")

    async def _execute_tool(
        self, tool: Tool, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Run a tool with unified error handling.

        Calls tool._run() which handles task routing - checking the task_metadata
        contextvar and submitting to Docket if appropriate.
        """
        try:
            return await tool._run(arguments)
        except FastMCPError:
            logger.exception(f"Error calling tool {tool_name!r}")
            raise
        except (ValidationError, PydanticValidationError):
            # Validation errors are never masked - they indicate client input issues
            logger.exception(f"Error validating tool {tool_name!r}")
            raise
        except Exception as e:
            logger.exception(f"Error calling tool {tool_name!r}")
            if self._mask_error_details:
                raise ToolError(f"Error calling tool {tool_name!r}") from e
            raise ToolError(f"Error calling tool {tool_name!r}: {e}") from e

    async def _read_resource_mcp(self, uri: AnyUrl | str) -> list[ResourceContent]:
        """
        Handle MCP 'readResource' requests.

        Delegates to _read_resource, which should be overridden by FastMCP subclasses.
        """
        logger.debug(f"[{self.name}] Handler called: read_resource %s", uri)

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Task routing handled by custom handler
                # Note: Without task metadata, _read_resource_middleware always returns list
                result = await self._read_resource_middleware(uri)
                if isinstance(result, mcp.types.CreateTaskResult):
                    # Should never happen without task metadata, but handle for type safety
                    raise RuntimeError(
                        "Unexpected CreateTaskResult in _read_resource_mcp"
                    )
                return result
            except DisabledError as e:
                # convert to NotFoundError to avoid leaking resource presence
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e
            except NotFoundError as e:
                # standardize NotFound message
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e

    async def _read_resource_middleware(
        self,
        uri: AnyUrl | str,
    ) -> list[ResourceContent] | mcp.types.CreateTaskResult:
        """
        Applies this server's middleware and delegates the filtered call to the manager.

        Returns list[ResourceContent] for synchronous execution, or CreateTaskResult
        if the resource was submitted to Docket for background execution.
        """

        # Convert string URI to AnyUrl if needed
        uri_param = AnyUrl(uri) if isinstance(uri, str) else uri

        mw_context = MiddlewareContext(
            message=mcp.types.ReadResourceRequestParams(uri=uri_param),
            source="client",
            type="request",
            method="resources/read",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        result = await self._apply_middleware(
            context=mw_context, call_next=self._read_resource
        )
        # CreateTaskResult passes through, otherwise convert to list
        if isinstance(result, mcp.types.CreateTaskResult):
            return result
        return list(result)

    async def _read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
    ) -> list[ResourceContent] | mcp.types.CreateTaskResult:
        """
        Read a resource.

        Iterates through all providers (LocalProvider first) to find the resource.
        First provider wins. Checks concrete resources first, then templates.

        Returns list[ResourceContent] for synchronous execution, or CreateTaskResult
        if the resource was submitted to Docket for background execution.
        """
        uri_str = str(context.message.uri)

        # First pass: try concrete resources from all providers
        for provider in self._providers:
            resource = await provider.get_resource(uri_str)
            if resource is not None and self._should_enable_component(resource):
                result = await self._execute_resource(resource, uri_str)
                if isinstance(result, mcp.types.CreateTaskResult):
                    return result
                if result.mime_type is None:
                    result.mime_type = resource.mime_type
                return [result]

        # Second pass: try templates from all providers
        for provider in self._providers:
            template = await provider.get_resource_template(uri_str)
            if template is not None and self._should_enable_component(template):
                params = template.matches(uri_str)
                if params is not None:
                    result = await self._execute_template(template, uri_str, params)
                    if isinstance(result, mcp.types.CreateTaskResult):
                        return result
                    return [result]

        raise NotFoundError(f"Unknown resource: {uri_str!r}")

    async def _execute_resource(
        self, resource: Resource, uri_str: str
    ) -> ResourceContent | mcp.types.CreateTaskResult:
        """Read a resource with unified error handling.

        Calls resource._read() which handles task routing - checking the task_metadata
        contextvar and submitting to Docket if appropriate.
        """
        try:
            return await resource._read()
        except (FastMCPError, McpError):
            logger.exception(f"Error reading resource {uri_str!r}")
            raise
        except Exception as e:
            logger.exception(f"Error reading resource {uri_str!r}")
            if self._mask_error_details:
                raise ResourceError(f"Error reading resource {uri_str!r}") from e
            raise ResourceError(f"Error reading resource {uri_str!r}: {e}") from e

    async def _execute_template(
        self,
        template: ResourceTemplate,
        uri_str: str,
        params: dict[str, Any],
    ) -> ResourceContent | mcp.types.CreateTaskResult:
        """Execute a template with unified error handling.

        Calls template._read() which handles task routing - checking the task_metadata
        contextvar and submitting to Docket if appropriate.
        """
        try:
            return await template._read(uri_str, params)
        except (FastMCPError, McpError):
            logger.exception(f"Error reading resource {uri_str!r}")
            raise
        except Exception as e:
            logger.exception(f"Error reading resource {uri_str!r}")
            if self._mask_error_details:
                raise ResourceError(f"Error reading resource {uri_str!r}") from e
            raise ResourceError(f"Error reading resource {uri_str!r}: {e}") from e

    async def _get_prompt_mcp(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """
        Handle MCP 'getPrompt' requests.

        Delegates to _get_prompt, which should be overridden by FastMCP subclasses.
        """
        import fastmcp.server.context

        logger.debug(
            f"[{self.name}] Handler called: get_prompt %s with %s", name, arguments
        )

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Task routing handled by custom handler
                return await self._get_prompt_middleware(name, arguments)
            except DisabledError as e:
                # convert to NotFoundError to avoid leaking prompt presence
                raise NotFoundError(f"Unknown prompt: {name}") from e
            except NotFoundError as e:
                # standardize NotFound message
                raise NotFoundError(f"Unknown prompt: {name}") from e

    async def _get_prompt_middleware(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """
        Applies this server's middleware and delegates the filtered call to the manager.
        Converts PromptResult to GetPromptResult for MCP protocol.

        Note: This method assumes synchronous execution. For task-augmented execution,
        use _get_prompt_content_middleware directly and handle CreateTaskResult.
        """
        result = await self._get_prompt_content_middleware(name, arguments)
        if isinstance(result, mcp.types.CreateTaskResult):
            raise RuntimeError(
                "Prompt returned CreateTaskResult but _get_prompt_middleware "
                "expects synchronous execution"
            )
        return result.to_mcp_prompt_result()

    async def _get_prompt_content_middleware(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """
        Applies this server's middleware and returns PromptResult.
        Used internally and by parent servers for mounted prompts.

        Returns PromptResult for synchronous execution, or CreateTaskResult
        if the prompt was submitted to Docket for background execution.
        """
        mw_context = MiddlewareContext(
            message=mcp.types.GetPromptRequestParams(name=name, arguments=arguments),
            source="client",
            type="request",
            method="prompts/get",
            fastmcp_context=fastmcp.server.dependencies.get_context(),
        )
        return await self._apply_middleware(
            context=mw_context, call_next=self._get_prompt
        )

    async def _get_prompt(
        self,
        context: MiddlewareContext[mcp.types.GetPromptRequestParams],
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """
        Get a prompt.

        Iterates through all providers (LocalProvider first) to find the prompt.
        First provider wins.

        Returns PromptResult for synchronous execution, or CreateTaskResult
        if the prompt was submitted to Docket for background execution.
        """
        name = context.message.name

        for provider in self._providers:
            prompt = await provider.get_prompt(name)
            if prompt is not None and self._should_enable_component(prompt):
                return await self._execute_prompt(
                    prompt, name, context.message.arguments
                )

        raise NotFoundError(f"Unknown prompt: {name!r}")

    async def _execute_prompt(
        self, prompt: Prompt, name: str, arguments: dict[str, Any] | None
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Render a prompt with unified error handling.

        Calls prompt._render() which handles task routing - checking the task_metadata
        contextvar and submitting to Docket if appropriate.
        """
        try:
            return await prompt._render(arguments)
        except (FastMCPError, McpError):
            logger.exception(f"Error rendering prompt {name!r}")
            raise
        except Exception as e:
            logger.exception(f"Error rendering prompt {name!r}")
            if self._mask_error_details:
                raise PromptError(f"Error rendering prompt {name!r}") from e
            raise PromptError(f"Error rendering prompt {name!r}: {e}") from e

    def add_tool(self, tool: Tool) -> Tool:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            tool: The Tool instance to register

        Returns:
            The tool instance that was added to the server.
        """
        return self._local_provider.add_tool(tool)

    def remove_tool(self, name: str) -> None:
        """Remove a tool from the server.

        Args:
            name: The name of the tool to remove

        Raises:
            NotFoundError: If the tool is not found
        """
        try:
            self._local_provider.remove_tool(name)
        except KeyError:
            raise NotFoundError(f"Tool {name!r} not found") from None

    def add_tool_transformation(
        self, tool_name: str, transformation: ToolTransformConfig
    ) -> None:
        """Add a tool transformation."""
        self._local_provider.add_tool_transformation(tool_name, transformation)

    def remove_tool_transformation(self, tool_name: str) -> None:
        """Remove a tool transformation."""
        self._local_provider.remove_tool_transformation(tool_name)

    @overload
    def tool(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
        task: bool | TaskConfig | None = None,
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        enabled: bool | None = None,
        task: bool | TaskConfig | None = None,
    ) -> (
        Callable[[AnyFunction], FunctionTool]
        | FunctionTool
        | partial[Callable[[AnyFunction], FunctionTool] | FunctionTool]
    ):
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
            exclude_args: Optional list of argument names to exclude from the tool schema.
                Deprecated: Use `Depends()` for dependency injection instead.
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
        # Delegate to LocalProvider with server-level defaults
        result = self._local_provider.tool(
            name_or_fn,
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            exclude_args=exclude_args,
            meta=meta,
            enabled=enabled,
            task=task if task is not None else self._support_tasks_by_default,
            serializer=self._tool_serializer,
        )

        return result

    def add_resource(self, resource: Resource) -> Resource:
        """Add a resource to the server.

        Args:
            resource: A Resource instance to add

        Returns:
            The resource instance that was added to the server.
        """
        return self._local_provider.add_resource(resource)

    def add_template(self, template: ResourceTemplate) -> ResourceTemplate:
        """Add a resource template to the server.

        Args:
            template: A ResourceTemplate instance to add

        Returns:
            The template instance that was added to the server.
        """
        return self._local_provider.add_template(template)

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
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
            async def get_weather_with_context(city: str, ctx: Context) -> str:
                await ctx.info(f"Fetching weather for {city}")
                return f"Weather for {city}"

            @server.resource("resource://{city}/weather")
            async def get_weather(city: str) -> str:
                data = await fetch_weather(city)
                return f"Weather for {city}: {data}"
            ```
        """
        # Delegate to LocalProvider with server-level defaults
        inner_decorator = self._local_provider.resource(
            uri,
            name=name,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            enabled=enabled,
            annotations=annotations,
            meta=meta,
            task=task if task is not None else self._support_tasks_by_default,
        )

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate:
            return inner_decorator(fn)

        return decorator

    def add_prompt(self, prompt: Prompt) -> Prompt:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance to add

        Returns:
            The prompt instance that was added to the server.
        """
        return self._local_provider.add_prompt(prompt)

    @overload
    def prompt(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> (
        Callable[[AnyFunction], FunctionPrompt]
        | FunctionPrompt
        | partial[Callable[[AnyFunction], FunctionPrompt] | FunctionPrompt]
    ):
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
            async def analyze_with_context(table_name: str, ctx: Context) -> list[Message]:
                await ctx.info(f"Analyzing table {table_name}")
                schema = read_table_schema(table_name)
                return [
                    {
                        "role": "user",
                        "content": f"Analyze this schema:\n{schema}"
                    }
                ]

            @server.prompt("custom_name")
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

            @server.prompt(name="custom_name")
            def another_prompt(data: str) -> list[Message]:
                return [{"role": "user", "content": data}]

            # Direct function call
            server.prompt(my_function, name="custom_name")
            ```
        """
        # Delegate to LocalProvider with server-level defaults
        return self._local_provider.prompt(
            name_or_fn,
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            enabled=enabled,
            meta=meta,
            task=task if task is not None else self._support_tasks_by_default,
        )

    async def run_stdio_async(
        self, show_banner: bool = True, log_level: str | None = None
    ) -> None:
        """Run the server using stdio transport.

        Args:
            show_banner: Whether to display the server banner
            log_level: Log level for the server
        """
        # Display server banner
        if show_banner:
            log_server_banner(
                server=self,
                transport="stdio",
            )

        with temporary_log_level(log_level):
            async with self._lifespan_manager():
                async with stdio_server() as (read_stream, write_stream):
                    logger.info(
                        f"Starting MCP server {self.name!r} with transport 'stdio'"
                    )

                    # Build experimental capabilities
                    experimental_capabilities = get_task_capabilities()

                    await self._mcp_server.run(
                        read_stream,
                        write_stream,
                        self._mcp_server.create_initialization_options(
                            notification_options=NotificationOptions(
                                tools_changed=True
                            ),
                            experimental_capabilities=experimental_capabilities,
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
        json_response: bool | None = None,
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
            json_response: Whether to use JSON response format (defaults to settings.json_response)
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
            json_response=json_response,
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
        uvicorn_config_from_user = uvicorn_config or {}

        config_kwargs: dict[str, Any] = {
            "timeout_graceful_shutdown": 0,
            "lifespan": "on",
            "ws": "websockets-sansio",
        }
        config_kwargs.update(uvicorn_config_from_user)

        if "log_config" not in config_kwargs and "log_level" not in config_kwargs:
            config_kwargs["log_level"] = default_log_level_to_use

        with temporary_log_level(log_level):
            async with self._lifespan_manager():
                config = uvicorn.Config(app, host=host, port=port, **config_kwargs)
                server = uvicorn.Server(config)
                path = getattr(app.state, "path", "").lstrip("/")
                logger.info(
                    f"Starting MCP server {self.name!r} with transport {transport!r} on http://{host}:{port}/{path}"
                )

                await server.serve()

    def http_app(
        self,
        path: str | None = None,
        middleware: list[ASGIMiddleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        event_store: EventStore | None = None,
        retry_interval: int | None = None,
    ) -> StarletteWithLifespan:
        """Create a Starlette app using the specified HTTP transport.

        Args:
            path: The path for the HTTP endpoint
            middleware: A list of middleware to apply to the app
            json_response: Whether to use JSON response format
            stateless_http: Whether to use stateless mode (new transport per request)
            transport: Transport protocol to use - "http", "streamable-http", or "sse"
            event_store: Optional event store for SSE polling/resumability. When set,
                enables clients to reconnect and resume receiving events after
                server-initiated disconnections. Only used with streamable-http transport.
            retry_interval: Optional retry interval in milliseconds for SSE polling.
                Controls how quickly clients should reconnect after server-initiated
                disconnections. Requires event_store to be set. Only used with
                streamable-http transport.

        Returns:
            A Starlette application configured with the specified transport
        """

        if transport in ("streamable-http", "http"):
            return create_streamable_http_app(
                server=self,
                streamable_http_path=path
                or self._deprecated_settings.streamable_http_path,
                event_store=event_store,
                retry_interval=retry_interval,
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

    def mount(
        self,
        server: FastMCP[LifespanResultT],
        namespace: str | None = None,
        as_proxy: bool | None = None,
        tool_names: dict[str, str] | None = None,
        prefix: str | None = None,  # deprecated, use namespace
    ) -> None:
        """Mount another FastMCP server on this server with an optional namespace.

        Unlike importing (with import_server), mounting establishes a dynamic connection
        between servers. When a client interacts with a mounted server's objects through
        the parent server, requests are forwarded to the mounted server in real-time.
        This means changes to the mounted server are immediately reflected when accessed
        through the parent.

        When a server is mounted with a namespace:
        - Tools from the mounted server are accessible with namespaced names.
          Example: If server has a tool named "get_weather", it will be available as "namespace_get_weather".
        - Resources are accessible with namespaced URIs.
          Example: If server has a resource with URI "weather://forecast", it will be available as
          "weather://namespace/forecast".
        - Templates are accessible with namespaced URI templates.
          Example: If server has a template with URI "weather://location/{id}", it will be available
          as "weather://namespace/location/{id}".
        - Prompts are accessible with namespaced names.
          Example: If server has a prompt named "weather_prompt", it will be available as
          "namespace_weather_prompt".

        When a server is mounted without a namespace (namespace=None), its tools, resources, templates,
        and prompts are accessible with their original names. Multiple servers can be mounted
        without namespaces, and they will be tried in order until a match is found.

        The mounted server's lifespan is executed when the parent server starts, and its
        middleware chain is invoked for all operations (tool calls, resource reads, prompts).

        Args:
            server: The FastMCP server to mount.
            namespace: Optional namespace to use for the mounted server's objects. If None,
                the server's objects are accessible with their original names.
            as_proxy: Deprecated. Mounted servers now always have their lifespan and
                middleware invoked. To create a proxy server, use FastMCP.as_proxy()
                explicitly before mounting.
            tool_names: Optional mapping of original tool names to custom names. Use this
                to override namespaced names. Keys are the original tool names from the
                mounted server.
            prefix: Deprecated. Use namespace instead.
        """
        import warnings

        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

        # Handle deprecated prefix parameter
        if prefix is not None:
            warnings.warn(
                "The 'prefix' parameter is deprecated, use 'namespace' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            if namespace is None:
                namespace = prefix
            else:
                raise ValueError("Cannot specify both 'prefix' and 'namespace'")

        if as_proxy is not None:
            warnings.warn(
                "as_proxy is deprecated and will be removed in a future version. "
                "Mounted servers now always have their lifespan and middleware invoked. "
                "To create a proxy server, use FastMCP.as_proxy() explicitly.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Still honor the flag for backward compatibility
            if as_proxy:
                from fastmcp.server.providers.proxy import FastMCPProxy

                if not isinstance(server, FastMCPProxy):
                    server = FastMCP.as_proxy(server)

        # Create provider with optional transformations
        provider: Provider = FastMCPProvider(server)
        if namespace or tool_names:
            provider = provider.with_transforms(
                namespace=namespace, tool_renames=tool_names
            )
        self._providers.append(provider)

    async def import_server(
        self,
        server: FastMCP[LifespanResultT],
        prefix: str | None = None,
    ) -> None:
        """
        Import the MCP objects from another FastMCP server into this one,
        optionally with a given prefix.

        .. deprecated::
            Use :meth:`mount` instead. ``import_server`` will be removed in a
            future version.

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
        """
        import warnings

        warnings.warn(
            "import_server is deprecated, use mount() instead",
            DeprecationWarning,
            stacklevel=2,
        )

        def add_resource_prefix(uri: str, prefix: str) -> str:
            """Add prefix to resource URI: protocol://path → protocol://prefix/path."""
            match = URI_PATTERN.match(uri)
            if match:
                protocol, path = match.groups()
                return f"{protocol}{prefix}/{path}"
            return uri

        # Import tools from the server
        for tool in (await server.get_tools()).values():
            if prefix:
                tool = tool.model_copy(update={"name": f"{prefix}_{tool.name}"})
            self.add_tool(tool)

        # Import resources and templates from the server
        for resource in (await server.get_resources()).values():
            if prefix:
                new_uri = add_resource_prefix(str(resource.uri), prefix)
                resource = resource.model_copy(update={"uri": new_uri})
            self.add_resource(resource)

        for template in (await server.get_resource_templates()).values():
            if prefix:
                new_uri_template = add_resource_prefix(template.uri_template, prefix)
                template = template.model_copy(
                    update={"uri_template": new_uri_template}
                )
            self.add_template(template)

        # Import prompts from the server
        for prompt in (await server.get_prompts()).values():
            if prefix:
                prompt = prompt.model_copy(update={"name": f"{prefix}_{prompt.name}"})
            self.add_prompt(prompt)

        if server._lifespan != default_lifespan:
            from warnings import warn

            warn(
                message="When importing from a server with a lifespan, the lifespan from the imported server will not be used.",
                category=RuntimeWarning,
                stacklevel=2,
            )

        if prefix:
            logger.debug(
                f"[{self.name}] Imported server {server.name} with prefix '{prefix}'"
            )
        else:
            logger.debug(f"[{self.name}] Imported server {server.name}")

    @classmethod
    def from_openapi(
        cls,
        openapi_spec: dict[str, Any],
        client: httpx.AsyncClient,
        name: str = "OpenAPI Server",
        route_maps: list[RouteMap] | None = None,
        route_map_fn: OpenAPIRouteMapFn | None = None,
        mcp_component_fn: OpenAPIComponentFn | None = None,
        mcp_names: dict[str, str] | None = None,
        tags: set[str] | None = None,
        timeout: float | None = None,
        **settings: Any,
    ) -> Self:
        """
        Create a FastMCP server from an OpenAPI specification.

        Args:
            openapi_spec: OpenAPI schema as a dictionary
            client: httpx AsyncClient for making HTTP requests
            name: Name for the MCP server
            route_maps: Optional list of RouteMap objects defining route mappings
            route_map_fn: Optional callable for advanced route type mapping
            mcp_component_fn: Optional callable for component customization
            mcp_names: Optional dictionary mapping operationId to component names
            tags: Optional set of tags to add to all components
            timeout: Optional timeout (in seconds) for all requests
            **settings: Additional settings passed to FastMCP

        Returns:
            A FastMCP server with an OpenAPIProvider attached.
        """
        from .providers.openapi import OpenAPIProvider

        provider = OpenAPIProvider(
            openapi_spec=openapi_spec,
            client=client,
            route_maps=route_maps,
            route_map_fn=route_map_fn,
            mcp_component_fn=mcp_component_fn,
            mcp_names=mcp_names,
            tags=tags,
            timeout=timeout,
        )
        return cls(name=name, providers=[provider], **settings)

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
        timeout: float | None = None,
        **settings: Any,
    ) -> Self:
        """
        Create a FastMCP server from a FastAPI application.

        Args:
            app: FastAPI application instance
            name: Name for the MCP server (defaults to app.title)
            route_maps: Optional list of RouteMap objects defining route mappings
            route_map_fn: Optional callable for advanced route type mapping
            mcp_component_fn: Optional callable for component customization
            mcp_names: Optional dictionary mapping operationId to component names
            httpx_client_kwargs: Optional kwargs passed to httpx.AsyncClient
            tags: Optional set of tags to add to all components
            timeout: Optional timeout (in seconds) for all requests
            **settings: Additional settings passed to FastMCP

        Returns:
            A FastMCP server with an OpenAPIProvider attached.
        """
        from .providers.openapi import OpenAPIProvider

        if httpx_client_kwargs is None:
            httpx_client_kwargs = {}
        httpx_client_kwargs.setdefault("base_url", "http://fastapi")

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            **httpx_client_kwargs,
        )

        server_name = name or app.title

        provider = OpenAPIProvider(
            openapi_spec=app.openapi(),
            client=client,
            route_maps=route_maps,
            route_map_fn=route_map_fn,
            mcp_component_fn=mcp_component_fn,
            mcp_names=mcp_names,
            tags=tags,
            timeout=timeout,
        )
        return cls(name=server_name, providers=[provider], **settings)

    @classmethod
    def as_proxy(
        cls,
        backend: (
            Client[ClientTransportT]
            | ClientTransport
            | FastMCP[Any]
            | FastMCP1Server
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
        from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

        if isinstance(backend, Client):
            client = backend
            # Session strategy based on client connection state:
            # - Connected clients: reuse existing session for all requests
            # - Disconnected clients: create fresh sessions per request for isolation
            if client.is_connected():
                proxy_logger = get_logger(__name__)
                proxy_logger.info(
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
            # backend is not a Client, so it's compatible with ProxyClient.__init__
            base_client = ProxyClient(cast(Any, backend))

            # Fresh client created from transport - use fresh sessions per request
            def proxy_client_factory():
                return base_client.new()

            client_factory = proxy_client_factory

        return FastMCPProxy(client_factory=client_factory, **settings)

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
            return bool(any(itag in component.tags for itag in self.include_tags))

        return True

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__

        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"
