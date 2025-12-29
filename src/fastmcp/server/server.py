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
from dataclasses import replace
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
from fastmcp.resources.resource import Resource, ResourceResult
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
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.settings import DuplicateBehavior as DuplicateBehaviorSetting
from fastmcp.settings import Settings
from fastmcp.tools.tool import FunctionTool, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger, temporary_log_level
from fastmcp.utilities.types import NotSet, NotSetT
from fastmcp.utilities.visibility import VisibilityFilter

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
        auth: AuthProvider | None = None,
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

        if tool_serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `tool_serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                DeprecationWarning,
                stacklevel=2,
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

        self.auth: AuthProvider | None = auth

        if tools:
            for tool in tools:
                if not isinstance(tool, Tool):
                    tool = Tool.from_function(tool, serializer=self._tool_serializer)
                self.add_tool(tool)

        # Server-level visibility filter for runtime enable/disable
        self._visibility = VisibilityFilter()

        # Emit deprecation warnings for include_tags and exclude_tags
        if include_tags is not None:
            warnings.warn(
                "include_tags is deprecated. Use server.enable(tags=..., only=True) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # For backwards compatibility, initialize allowlist from include_tags
            self._visibility.enable(tags=set(include_tags), only=True)
        if exclude_tags is not None:
            warnings.warn(
                "exclude_tags is deprecated. Use server.disable(tags=...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # For backwards compatibility, initialize blocklist from exclude_tags
            self._visibility.disable(tags=set(exclude_tags))

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

                # Register task-enabled components from all providers in parallel
                task_results = await gather(
                    *[p.get_tasks() for p in self._providers],
                    return_exceptions=True,
                )

                for i, result in enumerate(task_results):
                    if isinstance(result, BaseException):
                        provider = self._providers[i]
                        logger.warning(
                            f"Failed to register tasks from {provider}: {result}"
                        )
                        if fastmcp.settings.mounted_components_raise_on_load_error:
                            raise result
                        continue
                    for component in result:
                        component.register_with_docket(docket)

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
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server asynchronously.

        Args:
            transport: Transport protocol to use ("stdio", "sse", or "streamable-http")
            show_banner: Whether to display the server banner. If None, uses the
                FASTMCP_SHOW_SERVER_BANNER setting (default: True).
        """
        if show_banner is None:
            show_banner = fastmcp.settings.show_server_banner
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
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None:
        """Run the FastMCP server. Note this is a synchronous function.

        Args:
            transport: Transport protocol to use ("http", "stdio", "sse", or "streamable-http")
            show_banner: Whether to display the server banner. If None, uses the
                FASTMCP_SHOW_SERVER_BANNER setting (default: True).
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

        All handlers use decorator-based registration for consistency.
        The call_tool decorator is from the SDK (supports CreateTaskResult + validate_input).
        The read_resource and get_prompt decorators are from LowLevelServer to add
        CreateTaskResult support until the SDK provides it natively.
        """
        self._mcp_server.list_tools()(self._list_tools_mcp)
        self._mcp_server.list_resources()(self._list_resources_mcp)
        self._mcp_server.list_resource_templates()(self._list_resource_templates_mcp)
        self._mcp_server.list_prompts()(self._list_prompts_mcp)

        self._mcp_server.call_tool(validate_input=self.strict_input_validation)(
            self._call_tool_mcp
        )
        self._mcp_server.read_resource()(self._read_resource_mcp)
        self._mcp_server.get_prompt()(self._get_prompt_mcp)

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

    async def _run_middleware(
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

    # -------------------------------------------------------------------------
    # Enable/Disable
    # -------------------------------------------------------------------------

    def enable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
        only: bool = False,
    ) -> None:
        """Enable components by removing from blocklist, or set allowlist with only=True.

        Args:
            keys: Keys to enable (e.g., ``"tool:my_tool"``).
            tags: Tags to enable - components with these tags will be enabled.
            only: If True, switches to allowlist mode - ONLY show these keys/tags.
                This clears existing allowlists and sets default visibility to False.

        Note:
            Component keys must match how they appear on this server. If a tool
            passes through a transforming provider (e.g., mounted with a namespace),
            its key changes. Always retrieve components from the same server you
            call enable/disable on.

        Example:
            .. code-block:: python

                # By key (prefixed)
                server.enable(keys=["tool:my_tool"])

                # By tag
                server.enable(tags={"internal"})

                # Allowlist mode - ONLY show tools tagged "final"
                server.enable(tags={"final"}, only=True)
        """
        self._visibility.enable(keys=keys, tags=tags, only=only)

    def disable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
    ) -> None:
        """Disable components by adding to the blocklist.

        Args:
            keys: Keys to disable (e.g., ``"tool:my_tool"``).
            tags: Tags to disable - components with these tags will be disabled.

        Note:
            Component keys must match how they appear on this server. If a tool
            passes through a transforming provider (e.g., mounted with a namespace),
            its key changes. Always retrieve components from the same server you
            call enable/disable on.

        Example:
            .. code-block:: python

                # By key (prefixed)
                server.disable(keys=["tool:my_tool"])

                # By tag
                server.disable(tags={"dangerous", "internal"})
        """
        self._visibility.disable(keys=keys, tags=tags)

    def _is_component_enabled(self, component: FastMCPComponent) -> bool:
        """Check if a component is enabled (not in blocklist, passes allowlist)."""
        return self._visibility.is_enabled(component)

    async def get_tools(self, *, run_middleware: bool = False) -> list[Tool]:
        """Get all enabled tools from providers.

        Queries all providers in parallel and collects tools.
        First provider wins for duplicate keys. Filters by server blocklist.

        Args:
            run_middleware: If True, apply the middleware chain before
                returning results. Used by MCP handlers and mounted servers.
        """
        if run_middleware:
            async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
                mw_context = MiddlewareContext(
                    message=mcp.types.ListToolsRequest(method="tools/list"),
                    source="client",
                    type="request",
                    method="tools/list",
                    fastmcp_context=fastmcp_ctx,
                )
                return list(
                    await self._run_middleware(
                        context=mw_context,
                        call_next=lambda context: self.get_tools(run_middleware=False),
                    )
                )

        results = await gather(
            *[p.list_tools() for p in self._providers],
            return_exceptions=True,
        )

        all_tools: dict[str, Tool] = {}
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                provider = self._providers[i]
                logger.exception(f"Error listing tools from provider {provider}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise result
                continue
            for tool in result:
                if self._is_component_enabled(tool) and tool.key not in all_tools:
                    all_tools[tool.key] = tool
        return list(all_tools.values())

    async def get_tool(self, name: str) -> Tool:
        """Get an enabled tool by name.

        Queries all providers in parallel to find the tool.
        First provider wins. Returns only if enabled.
        """
        results = await gather(
            *[p.get_tool(name) for p in self._providers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error getting tool from {self._providers[i]}: {result}"
                    )
                continue
            if isinstance(result, Tool) and self._is_component_enabled(result):
                return result

        raise NotFoundError(f"Unknown tool: {name!r}")

    async def get_resources(self, *, run_middleware: bool = False) -> list[Resource]:
        """Get all enabled resources from providers.

        Queries all providers in parallel and collects resources.
        First provider wins for duplicate keys. Filters by server blocklist.

        Args:
            run_middleware: If True, apply the middleware chain before
                returning results. Used by MCP handlers and mounted servers.
        """
        if run_middleware:
            async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
                mw_context = MiddlewareContext(
                    message={},  # List resources doesn't have parameters
                    source="client",
                    type="request",
                    method="resources/list",
                    fastmcp_context=fastmcp_ctx,
                )
                return list(
                    await self._run_middleware(
                        context=mw_context,
                        call_next=lambda context: self.get_resources(
                            run_middleware=False
                        ),
                    )
                )

        results = await gather(
            *[p.list_resources() for p in self._providers],
            return_exceptions=True,
        )

        all_resources: dict[str, Resource] = {}
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                provider = self._providers[i]
                logger.exception(f"Error listing resources from provider {provider}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise result
                continue
            for resource in result:
                if (
                    self._is_component_enabled(resource)
                    and resource.key not in all_resources
                ):
                    all_resources[resource.key] = resource
        return list(all_resources.values())

    async def get_resource(self, uri: str) -> Resource:
        """Get an enabled resource by URI.

        Queries all providers in parallel to find the resource.
        First provider wins. Returns only if enabled.
        """
        results = await gather(
            *[p.get_resource(uri) for p in self._providers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error getting resource from {self._providers[i]}: {result}"
                    )
                continue
            if isinstance(result, Resource) and self._is_component_enabled(result):
                return result

        raise NotFoundError(f"Unknown resource: {uri}")

    async def get_resource_templates(
        self, *, run_middleware: bool = False
    ) -> list[ResourceTemplate]:
        """Get all enabled resource templates from providers.

        Queries all providers in parallel and collects templates.
        First provider wins for duplicate keys. Filters by server blocklist.

        Args:
            run_middleware: If True, apply the middleware chain before
                returning results. Used by MCP handlers and mounted servers.
        """
        if run_middleware:
            async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
                mw_context = MiddlewareContext(
                    message={},  # List resource templates doesn't have parameters
                    source="client",
                    type="request",
                    method="resources/templates/list",
                    fastmcp_context=fastmcp_ctx,
                )
                return list(
                    await self._run_middleware(
                        context=mw_context,
                        call_next=lambda context: self.get_resource_templates(
                            run_middleware=False
                        ),
                    )
                )

        results = await gather(
            *[p.list_resource_templates() for p in self._providers],
            return_exceptions=True,
        )

        all_templates: dict[str, ResourceTemplate] = {}
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                provider = self._providers[i]
                logger.exception(
                    f"Error listing resource templates from provider {provider}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise result
                continue
            for template in result:
                if (
                    self._is_component_enabled(template)
                    and template.key not in all_templates
                ):
                    all_templates[template.key] = template
        return list(all_templates.values())

    async def get_resource_template(self, uri: str) -> ResourceTemplate:
        """Get an enabled resource template that matches the given URI.

        Queries all providers in parallel to find the template.
        First provider wins. Returns only if enabled.
        """
        results = await gather(
            *[p.get_resource_template(uri) for p in self._providers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error getting template from {self._providers[i]}: {result}"
                    )
                continue
            if isinstance(result, ResourceTemplate) and self._is_component_enabled(
                result
            ):
                return result

        raise NotFoundError(f"Unknown resource template: {uri}")

    async def get_prompts(self, *, run_middleware: bool = False) -> list[Prompt]:
        """Get all enabled prompts from providers.

        Queries all providers in parallel and collects prompts.
        First provider wins for duplicate keys. Filters by server blocklist.

        Args:
            run_middleware: If True, apply the middleware chain before
                returning results. Used by MCP handlers and mounted servers.
        """
        if run_middleware:
            async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
                mw_context = MiddlewareContext(
                    message=mcp.types.ListPromptsRequest(method="prompts/list"),
                    source="client",
                    type="request",
                    method="prompts/list",
                    fastmcp_context=fastmcp_ctx,
                )
                return list(
                    await self._run_middleware(
                        context=mw_context,
                        call_next=lambda context: self.get_prompts(
                            run_middleware=False
                        ),
                    )
                )

        results = await gather(
            *[p.list_prompts() for p in self._providers],
            return_exceptions=True,
        )

        all_prompts: dict[str, Prompt] = {}
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                provider = self._providers[i]
                logger.exception(f"Error listing prompts from provider {provider}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise result
                continue
            for prompt in result:
                if self._is_component_enabled(prompt) and prompt.key not in all_prompts:
                    all_prompts[prompt.key] = prompt
        return list(all_prompts.values())

    async def get_prompt(self, name: str) -> Prompt:
        """Get an enabled prompt by name.

        Queries all providers in parallel to find the prompt.
        First provider wins. Returns only if enabled.
        """
        results = await gather(
            *[p.get_prompt(name) for p in self._providers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error getting prompt from {self._providers[i]}: {result}"
                    )
                continue
            if isinstance(result, Prompt) and self._is_component_enabled(result):
                return result

        raise NotFoundError(f"Unknown prompt: {name}")

    async def get_component(
        self, key: str
    ) -> Tool | Resource | ResourceTemplate | Prompt:
        """Get a component by its prefixed key.

        Queries all providers in parallel to find the component.
        First provider wins.

        Args:
            key: The prefixed key (e.g., "tool:name", "resource:uri", "template:uri").

        Returns:
            The component if found.

        Raises:
            NotFoundError: If no component is found with the given key.
        """
        results = await gather(
            *[p.get_component(key) for p in self._providers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                if not isinstance(result, NotFoundError):
                    logger.debug(
                        f"Error getting component from {self._providers[i]}: {result}"
                    )
                continue
            if isinstance(result, FastMCPComponent):
                return result

        raise NotFoundError(f"Unknown component: {key}")

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Call a tool by name.

        This is the public API for executing tools. By default, middleware is applied.

        Args:
            name: The tool name
            arguments: Tool arguments (optional)
            run_middleware: If True (default), apply the middleware chain.
                Set to False when called from middleware to avoid re-applying.
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ToolResult.

        Returns:
            ToolResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Raises:
            NotFoundError: If tool not found or disabled
            ToolError: If tool execution fails
            ValidationError: If arguments fail validation
        """
        # Note: fn_key enrichment happens here after finding the tool.
        # For mounted servers, the parent's provider sets fn_key to the
        # namespaced key before delegating, ensuring correct Docket routing.

        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext[CallToolRequestParams](
                    message=mcp.types.CallToolRequestParams(
                        name=name, arguments=arguments or {}
                    ),
                    source="client",
                    type="request",
                    method="tools/call",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.call_tool(
                        context.message.name,
                        context.message.arguments or {},
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and execute tool (providers queried in parallel)
            tool = await self.get_tool(name)
            # Set fn_key for background task routing
            if task_meta is not None and task_meta.fn_key is None:
                task_meta = replace(task_meta, fn_key=tool.key)
            try:
                return await tool._run(arguments or {}, task_meta=task_meta)
            except FastMCPError:
                logger.exception(f"Error calling tool {name!r}")
                raise
            except (ValidationError, PydanticValidationError):
                logger.exception(f"Error validating tool {name!r}")
                raise
            except Exception as e:
                logger.exception(f"Error calling tool {name!r}")
                if self._mask_error_details:
                    raise ToolError(f"Error calling tool {name!r}") from e
                raise ToolError(f"Error calling tool {name!r}: {e}") from e

    @overload
    async def read_resource(
        self,
        uri: str,
        *,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ResourceResult: ...

    @overload
    async def read_resource(
        self,
        uri: str,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def read_resource(
        self,
        uri: str,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Read a resource by URI.

        This is the public API for reading resources. By default, middleware is applied.
        Checks concrete resources first, then templates.

        Args:
            uri: The resource URI
            run_middleware: If True (default), apply the middleware chain.
                Set to False when called from middleware to avoid re-applying.
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ResourceResult.

        Returns:
            ResourceResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Raises:
            NotFoundError: If resource not found or disabled
            ResourceError: If resource read fails
        """
        # Note: fn_key enrichment happens here after finding the resource/template.
        # Resources and templates use different key formats:
        # - Resources use resource.key (derived from the concrete URI)
        # - Templates use template.key (the template pattern)
        # For mounted servers, the parent's provider sets fn_key to the
        # namespaced key before delegating, ensuring correct Docket routing.

        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                uri_param = AnyUrl(uri)
                mw_context = MiddlewareContext(
                    message=mcp.types.ReadResourceRequestParams(uri=uri_param),
                    source="client",
                    type="request",
                    method="resources/read",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.read_resource(
                        str(context.message.uri),
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and read resource (providers queried in parallel)
            # Try concrete resources first
            try:
                resource = await self.get_resource(uri)
                # Set fn_key for background task routing
                if task_meta is not None and task_meta.fn_key is None:
                    task_meta = replace(task_meta, fn_key=resource.key)
                return await resource._read(task_meta=task_meta)
            except NotFoundError:
                pass  # Fall through to try templates
            except (FastMCPError, McpError):
                logger.exception(f"Error reading resource {uri!r}")
                raise
            except Exception as e:
                logger.exception(f"Error reading resource {uri!r}")
                if self._mask_error_details:
                    raise ResourceError(f"Error reading resource {uri!r}") from e
                raise ResourceError(f"Error reading resource {uri!r}: {e}") from e

            # Try templates
            try:
                template = await self.get_resource_template(uri)
            except NotFoundError:
                raise NotFoundError(f"Unknown resource: {uri!r}") from None
            params = template.matches(uri)
            assert params is not None  # get_resource_template already verified match
            # Set fn_key for background task routing
            if task_meta is not None and task_meta.fn_key is None:
                task_meta = replace(task_meta, fn_key=template.key)
            try:
                return await template._read(uri, params, task_meta=task_meta)
            except (FastMCPError, McpError):
                logger.exception(f"Error reading resource {uri!r}")
                raise
            except Exception as e:
                logger.exception(f"Error reading resource {uri!r}")
                if self._mask_error_details:
                    raise ResourceError(f"Error reading resource {uri!r}") from e
                raise ResourceError(f"Error reading resource {uri!r}: {e}") from e

    @overload
    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Render a prompt by name.

        This is the public API for rendering prompts. By default, middleware is applied.
        Use get_prompt() to retrieve the prompt definition without rendering.

        Args:
            name: The prompt name
            arguments: Prompt arguments (optional)
            run_middleware: If True (default), apply the middleware chain.
                Set to False when called from middleware to avoid re-applying.
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return PromptResult.

        Returns:
            PromptResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Raises:
            NotFoundError: If prompt not found or disabled
            PromptError: If prompt rendering fails
        """
        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext(
                    message=mcp.types.GetPromptRequestParams(
                        name=name, arguments=arguments
                    ),
                    source="client",
                    type="request",
                    method="prompts/get",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.render_prompt(
                        context.message.name,
                        context.message.arguments,
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and render prompt (providers queried in parallel)
            prompt = await self.get_prompt(name)
            # Set fn_key for background task routing
            if task_meta is not None and task_meta.fn_key is None:
                task_meta = replace(task_meta, fn_key=prompt.key)
            try:
                return await prompt._render(arguments, task_meta=task_meta)
            except (FastMCPError, McpError):
                logger.exception(f"Error rendering prompt {name!r}")
                raise
            except Exception as e:
                logger.exception(f"Error rendering prompt {name!r}")
                if self._mask_error_details:
                    raise PromptError(f"Error rendering prompt {name!r}") from e
                raise PromptError(f"Error rendering prompt {name!r}: {e}") from e

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
            tools = await self.get_tools(run_middleware=True)
            return [
                tool.to_mcp_tool(
                    name=tool.name,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for tool in tools
            ]

    async def _list_resources_mcp(self) -> list[SDKResource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resources")

        async with fastmcp.server.context.Context(fastmcp=self):
            resources = await self.get_resources(run_middleware=True)
            return [
                resource.to_mcp_resource(
                    uri=str(resource.uri),
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for resource in resources
            ]

    async def _list_resource_templates_mcp(self) -> list[SDKResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resource_templates")

        async with fastmcp.server.context.Context(fastmcp=self):
            templates = await self.get_resource_templates(run_middleware=True)
            return [
                template.to_mcp_template(
                    uriTemplate=template.uri_template,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for template in templates
            ]

    async def _list_prompts_mcp(self) -> list[SDKPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_prompts")

        async with fastmcp.server.context.Context(fastmcp=self):
            prompts = await self.get_prompts(run_middleware=True)
            return [
                prompt.to_mcp_prompt(
                    name=prompt.name,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for prompt in prompts
            ]

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

        Extracts task metadata from MCP request context and passes it explicitly
        to call_tool(). The tool's _run() method handles the backgrounding decision,
        ensuring middleware runs before Docket.

        Args:
            key: The name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Tool result or CreateTaskResult for background execution
        """
        logger.debug(
            f"[{self.name}] Handler called: call_tool %s with %s", key, arguments
        )

        try:
            # Extract SEP-1686 task metadata from request context.
            # fn_key is set by call_tool() after finding the tool.
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.call_tool(key, arguments, task_meta=task_meta)

            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result.to_mcp_result()

        except DisabledError as e:
            raise NotFoundError(f"Unknown tool: {key!r}") from e
        except NotFoundError as e:
            raise NotFoundError(f"Unknown tool: {key!r}") from e

    async def _read_resource_mcp(
        self, uri: AnyUrl | str
    ) -> mcp.types.ReadResourceResult | mcp.types.CreateTaskResult:
        """Handle MCP 'readResource' requests.

        Extracts task metadata from MCP request context and passes it explicitly
        to read_resource(). The resource's _read() method handles the backgrounding
        decision, ensuring middleware runs before Docket.

        Args:
            uri: The resource URI

        Returns:
            ReadResourceResult or CreateTaskResult for background execution
        """
        logger.debug(f"[{self.name}] Handler called: read_resource %s", uri)

        try:
            # Extract SEP-1686 task metadata from request context.
            # fn_key is set by read_resource() after finding the resource/template.
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.read_resource(str(uri), task_meta=task_meta)

            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result.to_mcp_result(uri)
        except DisabledError as e:
            raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e
        except NotFoundError:
            raise

    async def _get_prompt_mcp(
        self, name: str, arguments: dict[str, Any] | None
    ) -> mcp.types.GetPromptResult | mcp.types.CreateTaskResult:
        """Handle MCP 'getPrompt' requests.

        Extracts task metadata from MCP request context and passes it explicitly
        to render_prompt(). The prompt's _render() method handles the backgrounding
        decision, ensuring middleware runs before Docket.

        Args:
            name: The prompt name
            arguments: Prompt arguments

        Returns:
            GetPromptResult or CreateTaskResult for background execution
        """
        logger.debug(
            f"[{self.name}] Handler called: get_prompt %s with %s", name, arguments
        )

        try:
            # Extract SEP-1686 task metadata from request context.
            # fn_key is set by render_prompt() after finding the prompt.
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.render_prompt(name, arguments, task_meta=task_meta)

            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result.to_mcp_prompt_result()
        except DisabledError as e:
            raise NotFoundError(f"Unknown prompt: {name!r}") from e
        except NotFoundError:
            raise

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
            log_server_banner(server=self)

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

        # Display server banner
        if show_banner:
            log_server_banner(server=self)
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
        for tool in await server.get_tools():
            if prefix:
                tool = tool.model_copy(update={"name": f"{prefix}_{tool.name}"})
            self.add_tool(tool)

        # Import resources and templates from the server
        for resource in await server.get_resources():
            if prefix:
                new_uri = add_resource_prefix(str(resource.uri), prefix)
                resource = resource.model_copy(update={"uri": new_uri})
            self.add_resource(resource)

        for template in await server.get_resource_templates():
            if prefix:
                new_uri_template = add_resource_prefix(template.uri_template, prefix)
                template = template.model_copy(
                    update={"uri_template": new_uri_template}
                )
            self.add_template(template)

        # Import prompts from the server
        for prompt in await server.get_prompts():
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

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__

        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"
