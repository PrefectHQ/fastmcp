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
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

import anyio
import httpx
import mcp.types
import uvicorn
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
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
    AuthorizationError,
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
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.prompts.prompt import PromptResult
from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth import AuthContext, AuthProvider, run_auth_checks
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.event_store import EventStore
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.server.lifespan import Lifespan
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers import LocalProvider, Provider
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.server.telemetry import server_span
from fastmcp.server.transforms import (
    Namespace,
    ToolTransform,
    Transform,
)
from fastmcp.settings import DuplicateBehavior as DuplicateBehaviorSetting
from fastmcp.settings import Settings
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import AuthCheckCallable, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger, temporary_log_level
from fastmcp.utilities.types import FastMCPBaseModel, NotSet, NotSetT
from fastmcp.utilities.versions import (
    VersionSpec,
    parse_version_key,
    version_sort_key,
)

if TYPE_CHECKING:
    from docket import Docket

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


def _get_auth_context() -> tuple[bool, Any]:
    """Get auth context for the current request.

    Returns a tuple of (skip_auth, token) where:
    - skip_auth=True means auth checks should be skipped (STDIO transport)
    - token is the access token for HTTP transports (may be None if unauthenticated)

    Uses late import to avoid circular import with context.py.
    """
    from fastmcp.server.context import _current_transport

    is_stdio = _current_transport.get() == "stdio"
    if is_stdio:
        return (True, None)
    return (False, get_access_token())


C = TypeVar("C", bound="FastMCPComponent")


def _dedupe_with_versions(
    components: Sequence[C],
    key_fn: Callable[[C], str],
) -> list[C]:
    """Deduplicate components by key, keeping highest version.

    Groups components by key, selects the highest version from each group,
    and injects available versions into meta if any component is versioned.

    Args:
        components: Sequence of components to deduplicate.
        key_fn: Function to extract the grouping key from a component.

    Returns:
        Deduplicated list with versions injected into meta.
    """
    by_key: dict[str, list[C]] = {}
    for c in components:
        by_key.setdefault(key_fn(c), []).append(c)

    result: list[C] = []
    for versions in by_key.values():
        highest: C = cast(C, max(versions, key=version_sort_key))
        if any(c.version is not None for c in versions):
            all_versions = sorted(
                [c.version for c in versions if c.version is not None],
                key=parse_version_key,
                reverse=True,
            )
            meta = highest.meta or {}
            highest = highest.model_copy(
                update={
                    "meta": {
                        **meta,
                        "fastmcp": {
                            **meta.get("fastmcp", {}),
                            "versions": all_versions,
                        },
                    }
                }
            )
        result.append(highest)
    return result


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


class StateValue(FastMCPBaseModel):
    """Wrapper for stored context state values."""

    value: Any


class FastMCP(Provider, Generic[LifespanResultT]):
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
        lifespan: LifespanCallable | Lifespan | None = None,
        mask_error_details: bool | None = None,
        tools: Sequence[Tool | Callable[..., Any]] | None = None,
        tool_serializer: ToolResultSerializerType | None = None,
        include_tags: Collection[str] | None = None,
        exclude_tags: Collection[str] | None = None,
        on_duplicate: DuplicateBehavior | None = None,
        strict_input_validation: bool | None = None,
        tasks: bool | None = None,
        session_state_store: AsyncKeyValue | None = None,
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
        tool_transformations: Mapping[str, ToolTransformConfig] | None = None,
    ):
        # Initialize Provider (sets up _transforms and _visibility)
        super().__init__()

        # Resolve on_duplicate from deprecated params (delete when removing deprecation)
        self._on_duplicate: DuplicateBehaviorSetting = _resolve_on_duplicate(
            on_duplicate,
            on_duplicate_tools,
            on_duplicate_resources,
            on_duplicate_prompts,
        )

        # Resolve server default for background task support
        self._support_tasks_by_default: bool = tasks if tasks is not None else False

        # Docket and Worker instances (set during lifespan for cross-task access)
        self._docket = None
        self._worker = None

        self._additional_http_routes: list[BaseRoute] = []

        # Session-scoped state store (shared across all requests)
        self._state_storage: AsyncKeyValue = session_state_store or MemoryStore()
        self._state_store: PydanticAdapter[StateValue] = PydanticAdapter[StateValue](
            key_value=self._state_storage,
            pydantic_model=StateValue,
            default_collection="fastmcp_state",
        )

        # Create LocalProvider for local components
        self._local_provider: LocalProvider = LocalProvider(
            on_duplicate=self._on_duplicate
        )

        # Local provider is always first in the provider list
        # Note: _transforms is initialized by Provider.__init__()
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

        # Handle Lifespan instances (they're callable) or regular lifespan functions
        if lifespan is not None:
            self._lifespan: LifespanCallable[LifespanResultT] = lifespan
        else:
            self._lifespan = cast(LifespanCallable[LifespanResultT], default_lifespan)
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

        # Handle deprecated include_tags and exclude_tags parameters
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

        # Handle deprecated tool_transformations parameter
        if tool_transformations:
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "The tool_transformations parameter is deprecated. Use "
                    "server.add_transform(ToolTransform({...})) instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            self._transforms.append(ToolTransform(dict(tool_transformations)))

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
        """Manage Docket instance and Worker for background task execution.

        Docket infrastructure is only initialized if:
        1. pydocket is installed (fastmcp[tasks] extra)
        2. There are task-enabled components (task_config.mode != 'forbidden')

        This means users with pydocket installed but no task-enabled components
        won't spin up Docket/Worker infrastructure.
        """
        from fastmcp.server.dependencies import _current_server, is_docket_available

        # Set FastMCP server in ContextVar so CurrentFastMCP can access it
        # (use weakref to avoid reference cycles)
        server_token = _current_server.set(weakref.ref(self))

        try:
            # If docket is not available, skip task infrastructure
            if not is_docket_available():
                yield
                return

            # Collect task-enabled components at startup with all transforms applied.
            # Components must be available now to be registered with Docket workers;
            # dynamically added components after startup won't be registered.
            try:
                task_components = list(await self.get_tasks())
            except Exception as e:
                logger.warning(f"Failed to get tasks: {e}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                task_components = []

            # If no task-enabled components, skip Docket infrastructure entirely
            if not task_components:
                yield
                return

            # Docket is available AND there are task-enabled components
            from docket import Docket, Worker

            from fastmcp import settings
            from fastmcp.server.dependencies import (
                _current_docket,
                _current_worker,
            )

            # Create Docket instance using configured name and URL
            async with Docket(
                name=settings.docket.name,
                url=settings.docket.url,
            ) as docket:
                # Store on server instance for cross-task access (FastMCPTransport)
                self._docket = docket

                # Register task-enabled components with Docket
                for component in task_components:
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
                    async with Worker(docket, **worker_kwargs) as worker:
                        # Store on server instance for cross-context access
                        self._worker = worker
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
                            self._worker = None
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
        """Register SEP-1686 task protocol handlers with SDK.

        Only registers handlers if docket is installed. Without docket,
        task protocol requests will return "method not found" errors.
        """
        from fastmcp.server.dependencies import is_docket_available

        if not is_docket_available():
            return

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
    # Tool Transforms
    # -------------------------------------------------------------------------

    def _collect_list_results(
        self, results: list[Sequence[Any] | BaseException], operation: str
    ) -> list[Any]:
        """Collect successful list results, logging any exceptions."""
        collected: list[Any] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.debug(
                    f"Error during {operation} from provider "
                    f"{self._providers[i]}: {result}"
                )
                continue
            collected.extend(result)
        return collected

    # -------------------------------------------------------------------------
    # Provider interface overrides (aggregate from sub-providers)
    # -------------------------------------------------------------------------

    async def list_tools(self) -> Sequence[Tool]:
        """Aggregate tools from all sub-providers.

        This is the Provider interface implementation. The inherited _list_tools()
        applies server-level transforms over this method.
        """
        results = await gather(
            *[p._list_tools() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_tools")

    async def list_resources(self) -> Sequence[Resource]:
        """Aggregate resources from all sub-providers."""
        results = await gather(
            *[p._list_resources() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resources")

    async def list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """Aggregate resource templates from all sub-providers."""
        results = await gather(
            *[p._list_resource_templates() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_resource_templates")

    async def list_prompts(self) -> Sequence[Prompt]:
        """Aggregate prompts from all sub-providers."""
        results = await gather(
            *[p._list_prompts() for p in self._providers],
            return_exceptions=True,
        )
        return self._collect_list_results(results, "list_prompts")

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Get task-eligible components with all transforms applied.

        Overrides Provider.get_tasks() to collect task-eligible components
        from all sub-providers and apply server-level transforms.
        """
        results = await gather(
            *[p.get_tasks() for p in self._providers],
            return_exceptions=True,
        )
        components = self._collect_list_results(results, "get_tasks")

        # Separate by component type for transform application
        tools = [c for c in components if isinstance(c, Tool)]
        resources = [c for c in components if isinstance(c, Resource)]
        templates = [c for c in components if isinstance(c, ResourceTemplate)]
        prompts = [c for c in components if isinstance(c, Prompt)]

        # Apply server-level transforms using call_next pattern
        async def tools_base() -> Sequence[Tool]:
            return tools

        async def resources_base() -> Sequence[Resource]:
            return resources

        async def templates_base() -> Sequence[ResourceTemplate]:
            return templates

        async def prompts_base() -> Sequence[Prompt]:
            return prompts

        tools_chain = tools_base
        resources_chain = resources_base
        templates_chain = templates_base
        prompts_chain = prompts_base

        for transform in self.transforms:
            tools_chain = partial(transform.list_tools, call_next=tools_chain)
            resources_chain = partial(
                transform.list_resources, call_next=resources_chain
            )
            templates_chain = partial(
                transform.list_resource_templates, call_next=templates_chain
            )
            prompts_chain = partial(transform.list_prompts, call_next=prompts_chain)

        transformed_tools = await tools_chain()
        transformed_resources = await resources_chain()
        transformed_templates = await templates_chain()
        transformed_prompts = await prompts_chain()

        return [
            *transformed_tools,
            *transformed_resources,
            *transformed_templates,
            *transformed_prompts,
        ]

    def add_transform(self, transform: Transform) -> None:
        """Add a server-level transform.

        Server-level transforms are applied after all providers are aggregated.
        They transform tools, resources, and prompts from ALL providers.

        Args:
            transform: The transform to add.

        Example:
            ```python
            from fastmcp.server.transforms import Namespace

            server = FastMCP("Server")
            server.add_transform(Namespace("api"))
            # All tools from all providers become "api_toolname"
            ```
        """
        self._transforms.append(transform)

    def add_tool_transformation(
        self, tool_name: str, transformation: ToolTransformConfig
    ) -> None:
        """Add a tool transformation.

        .. deprecated::
            Use ``add_transform(ToolTransform({...}))`` instead.
        """
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "add_tool_transformation is deprecated. Use "
                "server.add_transform(ToolTransform({tool_name: config})) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self.add_transform(ToolTransform({tool_name: transformation}))

    def remove_tool_transformation(self, _tool_name: str) -> None:
        """Remove a tool transformation.

        .. deprecated::
            Tool transformations are now immutable. Use visibility controls instead.
        """
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "remove_tool_transformation is deprecated and has no effect. "
                "Transforms are immutable once added. Use server.disable(keys=[...]) "
                "to hide tools instead.",
                DeprecationWarning,
                stacklevel=2,
            )

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
            keys: Keys to enable (e.g., ``"tool:my_tool@"`` for unversioned, ``"tool:my_tool@1.0"`` for versioned).
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
                server.enable(keys=["tool:my_tool@"])

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
            keys: Keys to disable (e.g., ``"tool:my_tool@"`` for unversioned, ``"tool:my_tool@1.0"`` for versioned).
            tags: Tags to disable - components with these tags will be disabled.

        Note:
            Component keys must match how they appear on this server. If a tool
            passes through a transforming provider (e.g., mounted with a namespace),
            its key changes. Always retrieve components from the same server you
            call enable/disable on.

        Example:
            .. code-block:: python

                # By key (prefixed)
                server.disable(keys=["tool:my_tool@"])

                # By tag
                server.disable(tags={"dangerous", "internal"})
        """
        self._visibility.disable(keys=keys, tags=tags)

    async def get_tools(self, *, run_middleware: bool = False) -> list[Tool]:
        """Get all enabled tools from providers.

        Queries all providers via the root provider (which applies provider transforms,
        server transforms, and visibility filtering). First provider wins for duplicate keys.

        Args:
            run_middleware: If True, apply the middleware chain before returning.
                Used by MCP handlers and FastMCPProvider for nested servers.
        """
        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext(
                    message=mcp.types.ListToolsRequest(method="tools/list"),
                    source="client",
                    type="request",
                    method="tools/list",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.get_tools(run_middleware=False),
                )

            # Query through full transform chain (provider transforms + server transforms + visibility)
            tools = await self._list_tools()

            # Get auth context (skip_auth=True for STDIO which has no auth concept)
            skip_auth, token = _get_auth_context()

            # Filter by auth
            authorized: list[Tool] = []
            for tool in tools:
                if not skip_auth and tool.auth is not None:
                    ctx = AuthContext(token=token, component=tool)
                    try:
                        if not run_auth_checks(tool.auth, ctx):
                            continue
                    except AuthorizationError:
                        continue
                authorized.append(tool)

            return _dedupe_with_versions(authorized, lambda t: t.name)

    async def get_tool(
        self, name: str, version: VersionSpec | str | None = None
    ) -> Tool | None:
        """Get a tool by name with all server transforms applied.

        Returns None if not found or if the tool is disabled via visibility settings.

        Args:
            name: The tool name.
            version: Version filter. Can be:
                - None: returns highest version
                - str: returns exact version match
                - VersionSpec: returns best match within spec (highest matching)
        """
        # Convert string to VersionSpec for backward compatibility
        if isinstance(version, str):
            version_spec: VersionSpec | None = VersionSpec(eq=version)
        else:
            version_spec = version

        # Use _get_tool which applies server transforms
        tool = await self._get_tool(name, version_spec)
        if tool is None:
            return None

        # Auth check
        skip_auth, token = _get_auth_context()
        if not skip_auth and tool.auth is not None:
            ctx = AuthContext(token=token, component=tool)
            if not run_auth_checks(tool.auth, ctx):
                return None
        return tool

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool with all transforms applied (server transforms + aggregation).

        Overrides Provider._get_tool to aggregate from providers after applying
        server-level transforms.
        """

        async def base(n: str, *, version: VersionSpec | None = None) -> Tool | None:
            # Aggregate from all sub-providers
            results = await gather(
                *[p._get_tool(n, version) for p in self._providers],
                return_exceptions=True,
            )

            # Collect valid results, pick highest version
            valid: list[Tool] = []
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    if not isinstance(result, NotFoundError):
                        logger.debug(
                            f"Error during get_tool({n!r}) from provider "
                            f"{self._providers[i]}: {result}"
                        )
                    continue
                if result is not None:
                    valid.append(result)

            if not valid:
                return None

            return max(valid, key=version_sort_key)  # type: ignore[type-var]

        # Build transform chain: server transforms applied over aggregation
        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_tool, call_next=chain)

        return await chain(name, version=version)

    async def get_resources(self, *, run_middleware: bool = False) -> list[Resource]:
        """Get all enabled resources from providers.

        Queries all providers via the root provider (which applies provider transforms,
        server transforms, and visibility filtering). First provider wins for duplicate keys.

        Args:
            run_middleware: If True, apply the middleware chain before returning.
                Used by MCP handlers and FastMCPProvider for nested servers.
        """
        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext(
                    message={},
                    source="client",
                    type="request",
                    method="resources/list",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.get_resources(run_middleware=False),
                )

            # Query through full transform chain (provider transforms + server transforms + visibility)
            resources = await self._list_resources()

            # Get auth context (skip_auth=True for STDIO which has no auth concept)
            skip_auth, token = _get_auth_context()

            # Filter by auth
            authorized: list[Resource] = []
            for resource in resources:
                if not skip_auth and resource.auth is not None:
                    ctx = AuthContext(token=token, component=resource)
                    try:
                        if not run_auth_checks(resource.auth, ctx):
                            continue
                    except AuthorizationError:
                        continue
                authorized.append(resource)

            return _dedupe_with_versions(authorized, lambda r: str(r.uri))

    async def get_resource(
        self, uri: str, version: VersionSpec | str | None = None
    ) -> Resource | None:
        """Get a resource by URI with all server transforms applied.

        Returns None if not found or if the resource is disabled via visibility settings.

        Args:
            uri: The resource URI.
            version: Version filter. Can be:
                - None: returns highest version
                - str: returns exact version match
                - VersionSpec: returns best match within spec (highest matching)
        """
        # Convert string to VersionSpec for backward compatibility
        if isinstance(version, str):
            version_spec: VersionSpec | None = VersionSpec(eq=version)
        else:
            version_spec = version

        # Use _get_resource which applies server transforms
        resource = await self._get_resource(uri, version_spec)
        if resource is None:
            return None

        # Auth check
        skip_auth, token = _get_auth_context()
        if not skip_auth and resource.auth is not None:
            ctx = AuthContext(token=token, component=resource)
            if not run_auth_checks(resource.auth, ctx):
                return None
        return resource

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get resource with all transforms applied (server transforms + aggregation).

        Overrides Provider._get_resource to aggregate from providers after applying
        server-level transforms.
        """

        async def base(
            u: str, *, version: VersionSpec | None = None
        ) -> Resource | None:
            # Aggregate from all sub-providers
            results = await gather(
                *[p._get_resource(u, version) for p in self._providers],
                return_exceptions=True,
            )

            # Collect valid results, pick highest version
            valid: list[Resource] = []
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    if not isinstance(result, NotFoundError):
                        logger.debug(
                            f"Error during get_resource({u!r}) from provider "
                            f"{self._providers[i]}: {result}"
                        )
                    continue
                if result is not None:
                    valid.append(result)

            if not valid:
                return None

            return max(valid, key=version_sort_key)  # type: ignore[type-var]

        # Build transform chain: server transforms applied over aggregation
        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_resource, call_next=chain)

        return await chain(uri, version=version)

    async def get_resource_templates(
        self, *, run_middleware: bool = False
    ) -> list[ResourceTemplate]:
        """Get all enabled resource templates from providers.

        Queries all providers via the root provider (which applies provider transforms,
        server transforms, and visibility filtering). First provider wins for duplicate keys.

        Args:
            run_middleware: If True, apply the middleware chain before returning.
                Used by MCP handlers and FastMCPProvider for nested servers.
        """
        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext(
                    message={},
                    source="client",
                    type="request",
                    method="resources/templates/list",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.get_resource_templates(
                        run_middleware=False
                    ),
                )

            # Query through full transform chain (provider transforms + server transforms + visibility)
            templates = await self._list_resource_templates()

            # Get auth context (skip_auth=True for STDIO which has no auth concept)
            skip_auth, token = _get_auth_context()

            # Filter by auth
            authorized: list[ResourceTemplate] = []
            for template in templates:
                if not skip_auth and template.auth is not None:
                    ctx = AuthContext(token=token, component=template)
                    try:
                        if not run_auth_checks(template.auth, ctx):
                            continue
                    except AuthorizationError:
                        continue
                authorized.append(template)

            return _dedupe_with_versions(authorized, lambda t: t.uri_template)

    async def get_resource_template(
        self, uri: str, version: VersionSpec | str | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template by URI with all server transforms applied.

        Returns None if not found or if the template is disabled via visibility settings.

        Args:
            uri: The template URI to match.
            version: Version filter. Can be:
                - None: returns highest version
                - str: returns exact version match
                - VersionSpec: returns best match within spec (highest matching)
        """
        # Convert string to VersionSpec for backward compatibility
        if isinstance(version, str):
            version_spec: VersionSpec | None = VersionSpec(eq=version)
        else:
            version_spec = version

        # Use _get_resource_template which applies server transforms
        template = await self._get_resource_template(uri, version_spec)
        if template is None:
            return None

        # Auth check
        skip_auth, token = _get_auth_context()
        if not skip_auth and template.auth is not None:
            ctx = AuthContext(token=token, component=template)
            if not run_auth_checks(template.auth, ctx):
                return None
        return template

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get resource template with all transforms applied.

        Overrides Provider._get_resource_template to aggregate from providers after
        applying server-level transforms.
        """

        async def base(
            u: str, *, version: VersionSpec | None = None
        ) -> ResourceTemplate | None:
            # Aggregate from all sub-providers
            results = await gather(
                *[p._get_resource_template(u, version) for p in self._providers],
                return_exceptions=True,
            )

            # Collect valid results, pick highest version
            valid: list[ResourceTemplate] = []
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    if not isinstance(result, NotFoundError):
                        logger.debug(
                            f"Error during get_resource_template({u!r}) from provider "
                            f"{self._providers[i]}: {result}"
                        )
                    continue
                if result is not None:
                    valid.append(result)

            if not valid:
                return None

            return max(valid, key=version_sort_key)  # type: ignore[type-var]

        # Build transform chain: server transforms applied over aggregation
        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_resource_template, call_next=chain)

        return await chain(uri, version=version)

    async def get_prompts(self, *, run_middleware: bool = False) -> list[Prompt]:
        """Get all enabled prompts from providers.

        Queries all providers via the root provider (which applies provider transforms,
        server transforms, and visibility filtering). First provider wins for duplicate keys.

        Args:
            run_middleware: If True, apply the middleware chain before returning.
                Used by MCP handlers and FastMCPProvider for nested servers.
        """
        async with fastmcp.server.context.Context(fastmcp=self) as ctx:
            if run_middleware:
                mw_context = MiddlewareContext(
                    message={},
                    source="client",
                    type="request",
                    method="prompts/list",
                    fastmcp_context=ctx,
                )
                return await self._run_middleware(
                    context=mw_context,
                    call_next=lambda context: self.get_prompts(run_middleware=False),
                )

            # Query through full transform chain (provider transforms + server transforms + visibility)
            prompts = await self._list_prompts()

            # Get auth context (skip_auth=True for STDIO which has no auth concept)
            skip_auth, token = _get_auth_context()

            # Filter by auth
            authorized: list[Prompt] = []
            for prompt in prompts:
                if not skip_auth and prompt.auth is not None:
                    ctx = AuthContext(token=token, component=prompt)
                    try:
                        if not run_auth_checks(prompt.auth, ctx):
                            continue
                    except AuthorizationError:
                        continue
                authorized.append(prompt)

            return _dedupe_with_versions(authorized, lambda p: p.name)

    async def get_prompt(
        self, name: str, version: VersionSpec | str | None = None
    ) -> Prompt | None:
        """Get a prompt by name with all server transforms applied.

        Returns None if not found or if the prompt is disabled via visibility settings.

        Args:
            name: The prompt name.
            version: Version filter. Can be:
                - None: returns highest version
                - str: returns exact version match
                - VersionSpec: returns best match within spec (highest matching)
        """
        # Convert string to VersionSpec for backward compatibility
        if isinstance(version, str):
            version_spec: VersionSpec | None = VersionSpec(eq=version)
        else:
            version_spec = version

        # Use _get_prompt which applies server transforms
        prompt = await self._get_prompt(name, version_spec)
        if prompt is None:
            return None

        # Auth check
        skip_auth, token = _get_auth_context()
        if not skip_auth and prompt.auth is not None:
            ctx = AuthContext(token=token, component=prompt)
            if not run_auth_checks(prompt.auth, ctx):
                return None
        return prompt

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt with all transforms applied (server transforms + aggregation).

        Overrides Provider._get_prompt to aggregate from providers after applying
        server-level transforms.
        """

        async def base(n: str, *, version: VersionSpec | None = None) -> Prompt | None:
            # Aggregate from all sub-providers
            results = await gather(
                *[p._get_prompt(n, version) for p in self._providers],
                return_exceptions=True,
            )

            # Collect valid results, pick highest version
            valid: list[Prompt] = []
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    if not isinstance(result, NotFoundError):
                        logger.debug(
                            f"Error during get_prompt({n!r}) from provider "
                            f"{self._providers[i]}: {result}"
                        )
                    continue
                if result is not None:
                    valid.append(result)

            if not valid:
                return None

            return max(valid, key=version_sort_key)  # type: ignore[type-var]

        # Build transform chain: server transforms applied over aggregation
        chain = base
        for transform in self.transforms:
            chain = partial(transform.get_prompt, call_next=chain)

        return await chain(name, version=version)

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
        """Call a tool by name.

        This is the public API for executing tools. By default, middleware is applied.

        Args:
            name: The tool name
            arguments: Tool arguments (optional)
            version: Specific version to call. If None, calls highest version.
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
                        version=version,
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and execute tool (providers queried in parallel)
            with server_span(
                f"tools/call {name}", "tools/call", self.name, "tool", name
            ) as span:
                tool = await self.get_tool(name, version=version)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {name!r}")
                span.set_attributes(tool.get_span_attributes())
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
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ResourceResult: ...

    @overload
    async def read_resource(
        self,
        uri: str,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def read_resource(
        self,
        uri: str,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Read a resource by URI.

        This is the public API for reading resources. By default, middleware is applied.
        Checks concrete resources first, then templates.

        Args:
            uri: The resource URI
            version: Specific version to read. If None, reads highest version.
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
                        version=version,
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and read resource (providers queried in parallel)
            with server_span(
                f"resources/read {uri}",
                "resources/read",
                self.name,
                "resource",
                uri,
                resource_uri=uri,
            ) as span:
                # Try concrete resources first (auth checked in get_resource)
                resource = await self.get_resource(uri, version=version)
                if resource is not None:
                    span.set_attributes(resource.get_span_attributes())
                    if task_meta is not None and task_meta.fn_key is None:
                        task_meta = replace(task_meta, fn_key=resource.key)
                    try:
                        return await resource._read(task_meta=task_meta)
                    except (FastMCPError, McpError):
                        logger.exception(f"Error reading resource {uri!r}")
                        raise
                    except Exception as e:
                        logger.exception(f"Error reading resource {uri!r}")
                        if self._mask_error_details:
                            raise ResourceError(
                                f"Error reading resource {uri!r}"
                            ) from e
                        raise ResourceError(
                            f"Error reading resource {uri!r}: {e}"
                        ) from e

                # Try templates (auth checked in get_resource_template)
                template = await self.get_resource_template(uri, version=version)
                if template is None:
                    if version is None:
                        raise NotFoundError(f"Unknown resource: {uri!r}")
                    raise NotFoundError(
                        f"Unknown resource: {uri!r} version {version!r}"
                    )
                span.set_attributes(template.get_span_attributes())
                params = template.matches(uri)
                assert params is not None
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
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta | None = None,
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Render a prompt by name.

        This is the public API for rendering prompts. By default, middleware is applied.
        Use get_prompt() to retrieve the prompt definition without rendering.

        Args:
            name: The prompt name
            arguments: Prompt arguments (optional)
            version: Specific version to render. If None, renders highest version.
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
                        version=version,
                        run_middleware=False,
                        task_meta=task_meta,
                    ),
                )

            # Core logic: find and render prompt (providers queried in parallel)
            with server_span(
                f"prompts/get {name}", "prompts/get", self.name, "prompt", name
            ) as span:
                prompt = await self.get_prompt(name, version=version)
                if prompt is None:
                    raise NotFoundError(f"Unknown prompt: {name!r}")
                span.set_attributes(prompt.get_span_attributes())
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
                )
                for resource in resources
            ]

    async def _list_resource_templates_mcp(self) -> list[SDKResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resource_templates")

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            mw_context = MiddlewareContext(
                message={},
                source="client",
                type="request",
                method="resources/templates/list",
                fastmcp_context=fastmcp_ctx,
            )
            templates = await self._run_middleware(
                context=mw_context,
                call_next=lambda context: self.get_resource_templates(),
            )
            return [
                template.to_mcp_template(
                    uriTemplate=template.uri_template,
                )
                for template in templates
            ]

    async def _list_prompts_mcp(self) -> list[SDKPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_prompts")

        async with fastmcp.server.context.Context(fastmcp=self) as fastmcp_ctx:
            mw_context = MiddlewareContext(
                message={},
                source="client",
                type="request",
                method="prompts/list",
                fastmcp_context=fastmcp_ctx,
            )
            prompts = await self._run_middleware(
                context=mw_context,
                call_next=lambda context: self.get_prompts(),
            )
            return [
                prompt.to_mcp_prompt(
                    name=prompt.name,
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
            # Extract version and task metadata from request context.
            # fn_key is set by call_tool() after finding the tool.
            version: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                # Extract version from request-level _meta.fastmcp.version
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    version = meta_dict.get("fastmcp", {}).get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.call_tool(
                key, arguments, version=version, task_meta=task_meta
            )

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
            # Extract version and task metadata from request context.
            version: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                # Extract version from _meta.fastmcp.version if provided
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    fastmcp_meta = meta_dict.get("fastmcp") or {}
                    version = fastmcp_meta.get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.read_resource(
                str(uri), version=version, task_meta=task_meta
            )

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
            # Extract version and task metadata from request context.
            # fn_key is set by render_prompt() after finding the prompt.
            version: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = self._mcp_server.request_context
                # Extract version from request-level _meta.fastmcp.version
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    version = meta_dict.get("fastmcp", {}).get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            result = await self.render_prompt(
                name, arguments, version=version, task_meta=task_meta
            )

            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result.to_mcp_prompt_result()
        except DisabledError as e:
            raise NotFoundError(f"Unknown prompt: {name!r}") from e
        except NotFoundError:
            raise

    def add_tool(self, tool: Tool | Callable[..., Any]) -> Tool:
        """Add a tool to the server.

        The tool function can optionally request a Context object by adding a parameter
        with the Context type annotation. See the @tool decorator for examples.

        Args:
            tool: The Tool instance or @tool-decorated function to register

        Returns:
            The tool instance that was added to the server.
        """
        return self._local_provider.add_tool(tool)

    def remove_tool(self, name: str, version: str | None = None) -> None:
        """Remove tool(s) from the server.

        Args:
            name: The name of the tool to remove.
            version: If None, removes ALL versions. If specified, removes only that version.

        Raises:
            NotFoundError: If no matching tool is found.
        """
        try:
            self._local_provider.remove_tool(name, version)
        except KeyError:
            if version is None:
                raise NotFoundError(f"Tool {name!r} not found") from None
            raise NotFoundError(
                f"Tool {name!r} version {version!r} not found"
            ) from None

    @overload
    def tool(
        self,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionTool: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], FunctionTool]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        annotations: ToolAnnotations | dict[str, Any] | None = None,
        exclude_args: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            exclude_args=exclude_args,
            meta=meta,
            task=task if task is not None else self._support_tasks_by_default,
            timeout=timeout,
            serializer=self._tool_serializer,
            auth=auth,
        )

        return result

    def add_resource(
        self, resource: Resource | Callable[..., Any]
    ) -> Resource | ResourceTemplate:
        """Add a resource to the server.

        Args:
            resource: A Resource instance or @resource-decorated function to add

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
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], Resource | ResourceTemplate | AnyFunction]:
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
            version=version,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
            task=task if task is not None else self._support_tasks_by_default,
            auth=auth,
        )

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate | AnyFunction:
            return inner_decorator(fn)

        return decorator

    def add_prompt(self, prompt: Prompt | Callable[..., Any]) -> Prompt:
        """Add a prompt to the server.

        Args:
            prompt: A Prompt instance or @prompt-decorated function to add

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
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            task=task if task is not None else self._support_tasks_by_default,
            auth=auth,
        )

    async def run_stdio_async(
        self,
        show_banner: bool = True,
        log_level: str | None = None,
        stateless: bool = False,
    ) -> None:
        """Run the server using stdio transport.

        Args:
            show_banner: Whether to display the server banner
            log_level: Log level for the server
            stateless: Whether to run in stateless mode (no session initialization)
        """
        from fastmcp.server.context import reset_transport, set_transport

        # Display server banner
        if show_banner:
            log_server_banner(server=self)

        token = set_transport("stdio")
        try:
            with temporary_log_level(log_level):
                async with self._lifespan_manager():
                    async with stdio_server() as (read_stream, write_stream):
                        mode = " (stateless)" if stateless else ""
                        logger.info(
                            f"Starting MCP server {self.name!r} with transport 'stdio'{mode}"
                        )

                        await self._mcp_server.run(
                            read_stream,
                            write_stream,
                            self._mcp_server.create_initialization_options(
                                notification_options=NotificationOptions(
                                    tools_changed=True
                                ),
                            ),
                            stateless=stateless,
                        )
        finally:
            reset_transport(token)

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
        stateless: bool | None = None,
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
            stateless: Alias for stateless_http for CLI consistency
        """
        # Allow stateless as alias for stateless_http
        if stateless is not None and stateless_http is None:
            stateless_http = stateless

        # Resolve from settings/env var if not explicitly set
        if stateless_http is None:
            stateless_http = self._deprecated_settings.stateless_http

        # SSE doesn't support stateless mode
        if stateless_http and transport == "sse":
            raise ValueError("SSE transport does not support stateless mode")

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
                mode = " (stateless)" if stateless_http else ""
                logger.info(
                    f"Starting MCP server {self.name!r} with transport {transport!r}{mode} on http://{host}:{port}/{path}"
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
                middleware invoked. To create a proxy server, use create_proxy()
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
                "To create a proxy server, use create_proxy() explicitly.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Still honor the flag for backward compatibility
            if as_proxy:
                from fastmcp.server.providers.proxy import FastMCPProxy

                if not isinstance(server, FastMCPProxy):
                    server = FastMCP.as_proxy(server)

        # Create provider with optional transforms
        provider: Provider = FastMCPProvider(server)
        if namespace:
            provider.add_transform(Namespace(namespace))
        if tool_names:
            # Tool renames are implemented as a ToolTransform
            # Keys must use the namespaced names (after Namespace transform)
            transforms = {
                (
                    f"{namespace}_{old_name}" if namespace else old_name
                ): ToolTransformConfig(name=new_name)
                for old_name, new_name in tool_names.items()
            }
            provider.add_transform(ToolTransform(transforms))
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

        provider: Provider = OpenAPIProvider(
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

        provider: Provider = OpenAPIProvider(
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

        .. deprecated::
            Use :func:`fastmcp.server.create_proxy` instead.
            This method will be removed in a future version.

        The `backend` argument can be either an existing `fastmcp.client.Client`
        instance or any value accepted as the `transport` argument of
        `fastmcp.client.Client`. This mirrors the convenience of the
        `fastmcp.client.Client` constructor.
        """
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "FastMCP.as_proxy() is deprecated. Use create_proxy() from "
                "fastmcp.server instead: `from fastmcp.server import create_proxy`",
                DeprecationWarning,
                stacklevel=2,
            )
        # Call the module-level create_proxy function directly
        return create_proxy(backend, **settings)

    @classmethod
    def generate_name(cls, name: str | None = None) -> str:
        class_name = cls.__name__

        if name is None:
            return f"{class_name}-{secrets.token_hex(2)}"
        else:
            return f"{class_name}-{name}-{secrets.token_hex(2)}"


# -----------------------------------------------------------------------------
# Module-level Factory Functions
# -----------------------------------------------------------------------------


def create_proxy(
    target: (
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
    """Create a FastMCP proxy server for the given target.

    This is the recommended way to create a proxy server. For lower-level control,
    use `FastMCPProxy` or `ProxyProvider` directly from `fastmcp.server.providers.proxy`.

    Args:
        target: The backend to proxy to. Can be:
            - A Client instance (connected or disconnected)
            - A ClientTransport
            - A FastMCP server instance
            - A URL string or AnyUrl
            - A Path to a server script
            - An MCPConfig or dict
        **settings: Additional settings passed to FastMCPProxy (name, etc.)

    Returns:
        A FastMCPProxy server that proxies to the target.

    Example:
        ```python
        from fastmcp.server import create_proxy

        # Create a proxy to a remote server
        proxy = create_proxy("http://remote-server/mcp")

        # Create a proxy to another FastMCP server
        proxy = create_proxy(other_server)
        ```
    """
    from fastmcp.server.providers.proxy import (
        FastMCPProxy,
        _create_client_factory,
    )

    client_factory = _create_client_factory(target)
    return FastMCPProxy(
        client_factory=client_factory,
        **settings,
    )
