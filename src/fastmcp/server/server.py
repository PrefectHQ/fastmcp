"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import asyncio
import re
import secrets
import warnings
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
    asynccontextmanager,
)
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, cast, overload

import httpx
import mcp.types
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from mcp.server.lowlevel.server import LifespanResultT
from mcp.shared.exceptions import McpError
from mcp.types import (
    Annotations,
    AnyFunction,
    CallToolRequestParams,
    ToolAnnotations,
)
from pydantic import AnyUrl
from pydantic import ValidationError as PydanticValidationError
from starlette.routing import BaseRoute
from typing_extensions import Self

import fastmcp
import fastmcp.server
from fastmcp.exceptions import (
    AuthorizationError,
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
from fastmcp.server.lifespan import Lifespan
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.mixins import LifespanMixin, MCPOperationsMixin, TransportMixin
from fastmcp.server.providers import LocalProvider, Provider
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.server.telemetry import server_span
from fastmcp.server.transforms import (
    ToolTransform,
    Transform,
)
from fastmcp.server.transforms.visibility import apply_session_transforms, is_enabled
from fastmcp.settings import DuplicateBehavior as DuplicateBehaviorSetting
from fastmcp.settings import Settings
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import AuthCheckCallable, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import FastMCPBaseModel, NotSet, NotSetT
from fastmcp.utilities.versions import (
    VersionSpec,
)

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


class FastMCP(
    AggregateProvider,
    LifespanMixin,
    MCPOperationsMixin,
    TransportMixin,
    Generic[LifespanResultT],
):
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
        list_page_size: int | None = None,
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
        # Initialize Provider (sets up _transforms)
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

        # Add providers using AggregateProvider's add_provider
        # LocalProvider is always first (no namespace)
        self.add_provider(self._local_provider)
        for p in providers or []:
            self.add_provider(p)

        # Store mask_error_details for execution error handling
        self._mask_error_details: bool = (
            mask_error_details
            if mask_error_details is not None
            else fastmcp.settings.mask_error_details
        )

        # Store list_page_size for pagination of list operations
        if list_page_size is not None and list_page_size <= 0:
            raise ValueError("list_page_size must be a positive integer")
        self._list_page_size: int | None = list_page_size

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
            self.enable(tags=set(include_tags), only=True)
        if exclude_tags is not None:
            warnings.warn(
                "exclude_tags is deprecated. Use server.disable(tags=...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # For backwards compatibility, initialize blocklist from exclude_tags
            self.disable(tags=set(exclude_tags))

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

    def add_provider(self, provider: Provider, *, namespace: str = "") -> None:
        """Add a provider for dynamic tools, resources, and prompts.

        Providers are queried in registration order. The first provider to return
        a non-None result wins. Static components (registered via decorators)
        always take precedence over providers.

        Args:
            provider: A Provider instance that will provide components dynamically.
            namespace: Optional namespace prefix. When set:
                - Tools become "namespace_toolname"
                - Resources become "protocol://namespace/path"
                - Prompts become "namespace_promptname"
        """
        super().add_provider(provider, namespace=namespace)

    # -------------------------------------------------------------------------
    # Provider interface overrides - inherited from AggregateProvider
    # -------------------------------------------------------------------------
    # _list_tools, _list_resources, _list_resource_templates, _list_prompts
    # are inherited from AggregateProvider which handles aggregation and namespacing

    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        """Get task-eligible components with all transforms applied.

        Overrides AggregateProvider.get_tasks() to apply server-level transforms
        after aggregation. AggregateProvider handles provider-level namespacing.
        """
        # Get tasks from AggregateProvider (handles aggregation and namespacing)
        components = list(await super().get_tasks())

        # Separate by component type for server-level transform application
        tools = [c for c in components if isinstance(c, Tool)]
        resources = [c for c in components if isinstance(c, Resource)]
        templates = [c for c in components if isinstance(c, ResourceTemplate)]
        prompts = [c for c in components if isinstance(c, Prompt)]

        # Apply server-level transforms sequentially
        for transform in self.transforms:
            tools = await transform.list_tools(tools)
            resources = await transform.list_resources(resources)
            templates = await transform.list_resource_templates(templates)
            prompts = await transform.list_prompts(prompts)

        return [
            *tools,
            *resources,
            *templates,
            *prompts,
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
            Tool transformations are now immutable. Use enable/disable controls instead.
        """
        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "remove_tool_transformation is deprecated and has no effect. "
                "Transforms are immutable once added. Use server.disable(keys=[...]) "
                "to hide tools instead.",
                DeprecationWarning,
                stacklevel=2,
            )

    async def list_tools(self, *, run_middleware: bool = True) -> Sequence[Tool]:
        """List all enabled tools from providers.

        Overrides Provider.list_tools() to add visibility filtering, auth filtering,
        and middleware execution. Returns all versions (no deduplication).
        Protocol handlers deduplicate for MCP wire format.
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
                    call_next=lambda context: self.list_tools(run_middleware=False),
                )

            # Get all tools, apply session transforms, then filter enabled
            tools = list(await super().list_tools())
            tools = await apply_session_transforms(tools)
            tools = [t for t in tools if is_enabled(t)]

            skip_auth, token = _get_auth_context()
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
            return authorized

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name via aggregation from providers.

        Extends AggregateProvider._get_tool() with component-level auth checks.

        Args:
            name: The tool name.
            version: Version filter (None returns highest version).

        Returns:
            The tool if found and authorized, None if not found or unauthorized.
        """
        # Get tool from AggregateProvider (handles aggregation and namespacing)
        tool = await super()._get_tool(name, version)
        if tool is None:
            return None

        # Component auth - return None if unauthorized (consistent with list filtering)
        skip_auth, token = _get_auth_context()
        if not skip_auth and tool.auth is not None:
            ctx = AuthContext(token=token, component=tool)
            try:
                if not run_auth_checks(tool.auth, ctx):
                    return None
            except AuthorizationError:
                return None

        return tool

    async def get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, filtering disabled tools.

        Overrides Provider.get_tool() to add visibility filtering after all
        transforms (including session-level) have been applied. This ensures
        session transforms can override provider-level disables.

        Args:
            name: The tool name.
            version: Version filter (None returns highest version).

        Returns:
            The tool if found and enabled, None otherwise.
        """
        tool = await super().get_tool(name, version)
        if tool is None:
            return None

        # Apply session transforms to single item
        tools = await apply_session_transforms([tool])
        if not tools or not is_enabled(tools[0]):
            return None
        return tools[0]

    async def list_resources(
        self, *, run_middleware: bool = True
    ) -> Sequence[Resource]:
        """List all enabled resources from providers.

        Overrides Provider.list_resources() to add visibility filtering, auth filtering,
        and middleware execution. Returns all versions (no deduplication).
        Protocol handlers deduplicate for MCP wire format.
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
                    call_next=lambda context: self.list_resources(run_middleware=False),
                )

            # Get all resources, apply session transforms, then filter enabled
            resources = list(await super().list_resources())
            resources = await apply_session_transforms(resources)
            resources = [r for r in resources if is_enabled(r)]

            skip_auth, token = _get_auth_context()
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
            return authorized

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI via aggregation from providers.

        Extends AggregateProvider._get_resource() with component-level auth checks.

        Args:
            uri: The resource URI.
            version: Version filter (None returns highest version).

        Returns:
            The resource if found and authorized, None if not found or unauthorized.
        """
        # Get resource from AggregateProvider (handles aggregation and namespacing)
        resource = await super()._get_resource(uri, version)
        if resource is None:
            return None

        # Component auth - return None if unauthorized (consistent with list filtering)
        skip_auth, token = _get_auth_context()
        if not skip_auth and resource.auth is not None:
            ctx = AuthContext(token=token, component=resource)
            try:
                if not run_auth_checks(resource.auth, ctx):
                    return None
            except AuthorizationError:
                return None

        return resource

    async def get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI, filtering disabled resources.

        Overrides Provider.get_resource() to add visibility filtering after all
        transforms (including session-level) have been applied.

        Args:
            uri: The resource URI.
            version: Version filter (None returns highest version).

        Returns:
            The resource if found and enabled, None otherwise.
        """
        resource = await super().get_resource(uri, version)
        if resource is None:
            return None

        # Apply session transforms to single item
        resources = await apply_session_transforms([resource])
        if not resources or not is_enabled(resources[0]):
            return None
        return resources[0]

    async def list_resource_templates(
        self, *, run_middleware: bool = True
    ) -> Sequence[ResourceTemplate]:
        """List all enabled resource templates from providers.

        Overrides Provider.list_resource_templates() to add visibility filtering,
        auth filtering, and middleware execution. Returns all versions (no deduplication).
        Protocol handlers deduplicate for MCP wire format.
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
                    call_next=lambda context: self.list_resource_templates(
                        run_middleware=False
                    ),
                )

            # Get all templates, apply session transforms, then filter enabled
            templates = list(await super().list_resource_templates())
            templates = await apply_session_transforms(templates)
            templates = [t for t in templates if is_enabled(t)]

            skip_auth, token = _get_auth_context()
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
            return authorized

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template by URI via aggregation from providers.

        Extends AggregateProvider._get_resource_template() with component-level auth checks.

        Args:
            uri: The template URI to match.
            version: Version filter (None returns highest version).

        Returns:
            The template if found and authorized, None if not found or unauthorized.
        """
        # Get template from AggregateProvider (handles aggregation and namespacing)
        template = await super()._get_resource_template(uri, version)
        if template is None:
            return None

        # Component auth - return None if unauthorized (consistent with list filtering)
        skip_auth, token = _get_auth_context()
        if not skip_auth and template.auth is not None:
            ctx = AuthContext(token=token, component=template)
            try:
                if not run_auth_checks(template.auth, ctx):
                    return None
            except AuthorizationError:
                return None

        return template

    async def get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template by URI, filtering disabled templates.

        Overrides Provider.get_resource_template() to add visibility filtering after
        all transforms (including session-level) have been applied.

        Args:
            uri: The template URI.
            version: Version filter (None returns highest version).

        Returns:
            The template if found and enabled, None otherwise.
        """
        template = await super().get_resource_template(uri, version)
        if template is None:
            return None

        # Apply session transforms to single item
        templates = await apply_session_transforms([template])
        if not templates or not is_enabled(templates[0]):
            return None
        return templates[0]

    async def list_prompts(self, *, run_middleware: bool = True) -> Sequence[Prompt]:
        """List all enabled prompts from providers.

        Overrides Provider.list_prompts() to add visibility filtering, auth filtering,
        and middleware execution. Returns all versions (no deduplication).
        Protocol handlers deduplicate for MCP wire format.
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
                    call_next=lambda context: self.list_prompts(run_middleware=False),
                )

            # Get all prompts, apply session transforms, then filter enabled
            prompts = list(await super().list_prompts())
            prompts = await apply_session_transforms(prompts)
            prompts = [p for p in prompts if is_enabled(p)]

            skip_auth, token = _get_auth_context()
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
            return authorized

    async def _get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a prompt by name via aggregation from providers.

        Extends AggregateProvider._get_prompt() with component-level auth checks.

        Args:
            name: The prompt name.
            version: Version filter (None returns highest version).

        Returns:
            The prompt if found and authorized, None if not found or unauthorized.
        """
        # Get prompt from AggregateProvider (handles aggregation and namespacing)
        prompt = await super()._get_prompt(name, version)
        if prompt is None:
            return None

        # Component auth - return None if unauthorized (consistent with list filtering)
        skip_auth, token = _get_auth_context()
        if not skip_auth and prompt.auth is not None:
            ctx = AuthContext(token=token, component=prompt)
            try:
                if not run_auth_checks(prompt.auth, ctx):
                    return None
            except AuthorizationError:
                return None

        return prompt

    async def get_prompt(
        self, name: str, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get a prompt by name, filtering disabled prompts.

        Overrides Provider.get_prompt() to add visibility filtering after all
        transforms (including session-level) have been applied.

        Args:
            name: The prompt name.
            version: Version filter (None returns highest version).

        Returns:
            The prompt if found and enabled, None otherwise.
        """
        prompt = await super().get_prompt(name, version)
        if prompt is None:
            return None

        # Apply session transforms to single item
        prompts = await apply_session_transforms([prompt])
        if not prompts or not is_enabled(prompts[0]):
            return None
        return prompts[0]

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: VersionSpec | None = None,
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
            # Use get_tool to apply transforms and filter disabled
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
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> ResourceResult: ...

    @overload
    async def read_resource(
        self,
        uri: str,
        *,
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def read_resource(
        self,
        uri: str,
        *,
        version: VersionSpec | None = None,
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
                # Try concrete resources first (transforms + auth via _get_resource)
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

                # Try templates (transforms + auth via get_resource_template)
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
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: VersionSpec | None = None,
        run_middleware: bool = True,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: VersionSpec | None = None,
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
            # Use get_prompt to apply transforms and filter disabled
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

        # Create provider and add it with namespace
        provider: Provider = FastMCPProvider(server)

        # Apply tool renames first (scoped to this provider), then namespace
        # So foo → bar with namespace="baz" becomes baz_bar
        if tool_names:
            transforms = {
                old_name: ToolTransformConfig(name=new_name)
                for old_name, new_name in tool_names.items()
            }
            provider = provider.wrap_transform(ToolTransform(transforms))

        # Use add_provider with namespace (applies namespace in AggregateProvider)
        self.add_provider(provider, namespace=namespace or "")

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
        for tool in await server.list_tools():
            if prefix:
                tool = tool.model_copy(update={"name": f"{prefix}_{tool.name}"})
            self.add_tool(tool)

        # Import resources and templates from the server
        for resource in await server.list_resources():
            if prefix:
                new_uri = add_resource_prefix(str(resource.uri), prefix)
                resource = resource.model_copy(update={"uri": new_uri})
            self.add_resource(resource)

        for template in await server.list_resource_templates():
            if prefix:
                new_uri_template = add_resource_prefix(template.uri_template, prefix)
                template = template.model_copy(
                    update={"uri_template": new_uri_template}
                )
            self.add_template(template)

        # Import prompts from the server
        for prompt in await server.list_prompts():
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
