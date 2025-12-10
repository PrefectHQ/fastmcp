"""FastMCP - A more ergonomic interface for MCP servers."""

from __future__ import annotations

import asyncio
import inspect
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
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, cast, overload

import anyio
import httpx
import mcp.types
import uvicorn
from docket import Docket, Worker
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import LifespanResultT, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    Annotations,
    AnyFunction,
    CallToolRequestParams,
    ContentBlock,
    ErrorData,
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
from fastmcp.prompts import Prompt
from fastmcp.prompts.prompt import FunctionPrompt
from fastmcp.prompts.prompt_manager import PromptManager
from fastmcp.resources.resource import FunctionResource, Resource
from fastmcp.resources.resource_manager import ResourceManager
from fastmcp.resources.template import FunctionResourceTemplate, ResourceTemplate
from fastmcp.server.auth import AuthProvider
from fastmcp.server.event_store import EventStore
from fastmcp.server.http import (
    StarletteWithLifespan,
    create_sse_app,
    create_streamable_http_app,
)
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.tasks.capabilities import get_task_capabilities
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.server.tasks.handlers import (
    handle_prompt_as_task,
    handle_resource_as_task,
    handle_tool_as_task,
)
from fastmcp.settings import Settings
from fastmcp.tools.tool import FunctionTool, Tool, ToolResult
from fastmcp.tools.tool_manager import ToolManager
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger, temporary_log_level
from fastmcp.utilities.types import NotSet, NotSetT

if TYPE_CHECKING:
    from fastmcp.client import Client
    from fastmcp.client.client import FastMCP1Server
    from fastmcp.client.transports import ClientTransport, ClientTransportT
    from fastmcp.server.openapi import ComponentFn as OpenAPIComponentFn
    from fastmcp.server.openapi import FastMCPOpenAPI, RouteMap
    from fastmcp.server.openapi import RouteMapFn as OpenAPIRouteMapFn
    from fastmcp.server.proxy import FastMCPProxy
    from fastmcp.server.sampling.handler import ServerSamplingHandler
    from fastmcp.tools.tool import ToolResultSerializerType

logger = get_logger(__name__)


def _create_named_fn_wrapper(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Create a wrapper function with a custom __name__ for Docket registration.

    Docket uses fn.__name__ as the key for function registration and lookup.
    When mounting servers, we need unique names to avoid collisions between
    mounted servers that have identically-named functions.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await fn(*args, **kwargs)

    wrapper.__name__ = name
    return wrapper


DuplicateBehavior = Literal["warn", "error", "replace", "ignore"]
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
        lifespan: LifespanCallable | None = None,
        mask_error_details: bool | None = None,
        tools: Sequence[Tool | Callable[..., Any]] | None = None,
        tool_transformations: Mapping[str, ToolTransformConfig] | None = None,
        tool_serializer: ToolResultSerializerType | None = None,
        include_tags: Collection[str] | None = None,
        exclude_tags: Collection[str] | None = None,
        include_fastmcp_meta: bool | None = None,
        on_duplicate_tools: DuplicateBehavior | None = None,
        on_duplicate_resources: DuplicateBehavior | None = None,
        on_duplicate_prompts: DuplicateBehavior | None = None,
        strict_input_validation: bool | None = None,
        tasks: bool | None = None,
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
        sampling_handler: ServerSamplingHandler[LifespanResultT] | None = None,
        sampling_handler_behavior: Literal["always", "fallback"] | None = None,
    ):
        # Resolve server default for background task support
        self._support_tasks_by_default: bool = tasks if tasks is not None else False

        # Docket instance (set during lifespan for cross-task access)
        self._docket = None

        self._additional_http_routes: list[BaseRoute] = []
        self._mounted_servers: list[MountedServer] = []
        self._is_mounted: bool = (
            False  # Set to True when this server is mounted on another
        )
        self._tool_manager: ToolManager = ToolManager(
            duplicate_behavior=on_duplicate_tools,
            mask_error_details=mask_error_details,
            transformations=tool_transformations,
        )
        self._resource_manager: ResourceManager = ResourceManager(
            duplicate_behavior=on_duplicate_resources,
            mask_error_details=mask_error_details,
        )
        self._prompt_manager: PromptManager = PromptManager(
            duplicate_behavior=on_duplicate_prompts,
            mask_error_details=mask_error_details,
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
                # server_auth_class returns the class itself
                auth = fastmcp.settings.server_auth_class()
            else:
                auth = None
        self.auth: AuthProvider | None = cast(AuthProvider | None, auth)

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

        self.sampling_handler: ServerSamplingHandler[LifespanResultT] | None = (
            sampling_handler
        )
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
            # For directly mounted servers, the parent's Docket/Worker handles all
            # task execution. Skip creating our own to avoid race conditions with
            # multiple workers competing for tasks from the same queue.
            if self._is_mounted:
                yield
                return

            # Create Docket instance using configured name and URL
            async with Docket(
                name=settings.docket.name,
                url=settings.docket.url,
            ) as docket:
                # Store on server instance for cross-task access (FastMCPTransport)
                self._docket = docket

                # Register local task-enabled tools/prompts/resources with Docket
                # Only function-based variants support background tasks
                # Register components where task execution is not "forbidden"
                for tool in self._tool_manager._tools.values():
                    if (
                        isinstance(tool, FunctionTool)
                        and tool.task_config.mode != "forbidden"
                    ):
                        docket.register(tool.fn)

                for prompt in self._prompt_manager._prompts.values():
                    if (
                        isinstance(prompt, FunctionPrompt)
                        and prompt.task_config.mode != "forbidden"
                    ):
                        # task execution requires async fn (validated at creation time)
                        docket.register(cast(Callable[..., Awaitable[Any]], prompt.fn))

                for resource in self._resource_manager._resources.values():
                    if (
                        isinstance(resource, FunctionResource)
                        and resource.task_config.mode != "forbidden"
                    ):
                        docket.register(resource.fn)

                for template in self._resource_manager._templates.values():
                    if (
                        isinstance(template, FunctionResourceTemplate)
                        and template.task_config.mode != "forbidden"
                    ):
                        docket.register(template.fn)

                # Also register functions from mounted servers so tasks can
                # execute in the parent's Docket context
                for mounted in self._mounted_servers:
                    await self._register_mounted_server_functions(
                        mounted.server, docket, mounted.prefix
                    )

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
                                # Cancel worker task on exit with timeout to prevent hanging
                                worker_task.cancel()
                                with suppress(
                                    asyncio.CancelledError, asyncio.TimeoutError
                                ):
                                    await asyncio.wait_for(worker_task, timeout=2.0)
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

    async def _register_mounted_server_functions(
        self, server: FastMCP, docket: Docket, prefix: str | None
    ) -> None:
        """Register task-enabled functions from a mounted server with Docket.

        This enables background task execution for mounted server components
        through the parent server's Docket context.

        Args:
            server: The mounted server whose functions to register
            docket: The Docket instance to register with
            prefix: The mount prefix to prepend to function names (matches
                    client-facing tool/prompt names)
        """
        # Register tools with prefixed names to avoid collisions
        for tool in server._tool_manager._tools.values():
            if isinstance(tool, FunctionTool) and tool.task_config.mode != "forbidden":
                # Use same naming as client-facing tool keys
                fn_name = f"{prefix}_{tool.key}" if prefix else tool.key
                named_fn = _create_named_fn_wrapper(tool.fn, fn_name)
                docket.register(named_fn)

        # Register prompts with prefixed names
        for prompt in server._prompt_manager._prompts.values():
            if (
                isinstance(prompt, FunctionPrompt)
                and prompt.task_config.mode != "forbidden"
            ):
                fn_name = f"{prefix}_{prompt.key}" if prefix else prompt.key
                named_fn = _create_named_fn_wrapper(
                    cast(Callable[..., Awaitable[Any]], prompt.fn), fn_name
                )
                docket.register(named_fn)

        # Register resources with prefixed names (use name, not key/URI)
        for resource in server._resource_manager._resources.values():
            if (
                isinstance(resource, FunctionResource)
                and resource.task_config.mode != "forbidden"
            ):
                fn_name = f"{prefix}_{resource.name}" if prefix else resource.name
                named_fn = _create_named_fn_wrapper(resource.fn, fn_name)
                docket.register(named_fn)

        # Register resource templates with prefixed names (use name, not key/URI)
        for template in server._resource_manager._templates.values():
            if (
                isinstance(template, FunctionResourceTemplate)
                and template.task_config.mode != "forbidden"
            ):
                fn_name = f"{prefix}_{template.name}" if prefix else template.name
                named_fn = _create_named_fn_wrapper(template.fn, fn_name)
                docket.register(named_fn)

        # Recursively register from nested mounted servers with accumulated prefix
        for nested in server._mounted_servers:
            nested_prefix = (
                f"{prefix}_{nested.prefix}"
                if prefix and nested.prefix
                else (prefix or nested.prefix)
            )
            await self._register_mounted_server_functions(
                nested.server, docket, nested_prefix
            )

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
                for server in self._mounted_servers:
                    await stack.enter_async_context(
                        cm=server.server._lifespan_manager()
                    )

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
        """Set up core MCP protocol handlers."""
        self._mcp_server.list_tools()(self._list_tools_mcp)
        self._mcp_server.list_resources()(self._list_resources_mcp)
        self._mcp_server.list_resource_templates()(self._list_resource_templates_mcp)
        self._mcp_server.list_prompts()(self._list_prompts_mcp)
        self._mcp_server.call_tool(validate_input=self.strict_input_validation)(
            self._call_tool_mcp
        )
        # Register custom read_resource handler (SDK decorator doesn't support CreateTaskResult)
        self._setup_read_resource_handler()
        # Register custom get_prompt handler (SDK decorator doesn't support CreateTaskResult)
        self._setup_get_prompt_handler()
        # Register custom SEP-1686 task protocol handlers
        self._setup_task_protocol_handlers()

    def _setup_read_resource_handler(self) -> None:
        """
        Set up custom read_resource handler that supports task-augmented responses.

        The SDK's read_resource decorator doesn't support CreateTaskResult returns,
        so we register a custom handler that checks request_context.experimental.is_task.
        """

        async def handler(req: mcp.types.ReadResourceRequest) -> mcp.types.ServerResult:
            uri = req.params.uri

            # Check for task metadata via SDK's request context
            task_meta = None
            try:
                ctx = self._mcp_server.request_context
                if ctx.experimental.is_task:
                    task_meta = ctx.experimental.task_metadata
            except (AttributeError, LookupError):
                pass

            # Check for task metadata and route appropriately
            async with fastmcp.server.context.Context(fastmcp=self):
                # Get resource including from mounted servers
                resource = await self._get_resource_or_template_or_none(str(uri))
                if (
                    resource
                    and self._should_enable_component(resource)
                    and hasattr(resource, "task_config")
                ):
                    task_mode = resource.task_config.mode  # type: ignore[union-attr]

                    # Enforce mode="required" - must have task metadata
                    if task_mode == "required" and not task_meta:
                        raise McpError(
                            ErrorData(
                                code=METHOD_NOT_FOUND,
                                message=f"Resource '{uri}' requires task-augmented execution",
                            )
                        )

                    # Route to background if task metadata present and mode allows
                    if task_meta and task_mode != "forbidden":
                        # For FunctionResource/FunctionResourceTemplate, use Docket
                        if isinstance(
                            resource,
                            FunctionResource | FunctionResourceTemplate,
                        ):
                            task_meta_dict = task_meta.model_dump(exclude_none=True)
                            return await handle_resource_as_task(
                                self, str(uri), resource, task_meta_dict
                            )

                    # Forbidden mode: task requested but mode="forbidden"
                    # Raise error since resources don't have isError field
                    if task_meta and task_mode == "forbidden":
                        raise McpError(
                            ErrorData(
                                code=METHOD_NOT_FOUND,
                                message=f"Resource '{uri}' does not support task-augmented execution",
                            )
                        )

            # Synchronous execution
            result = await self._read_resource_mcp(uri)

            # Graceful degradation: if we got here with task_meta, something went wrong
            # (This should be unreachable now that forbidden raises)
            if task_meta:
                mcp_contents = []
                for item in result:
                    if isinstance(item.content, str):
                        mcp_contents.append(
                            mcp.types.TextResourceContents(
                                uri=uri,
                                text=item.content,
                                mimeType=item.mime_type or "text/plain",
                            )
                        )
                    elif isinstance(item.content, bytes):
                        import base64

                        mcp_contents.append(
                            mcp.types.BlobResourceContents(
                                uri=uri,
                                blob=base64.b64encode(item.content).decode(),
                                mimeType=item.mime_type or "application/octet-stream",
                            )
                        )
                return mcp.types.ServerResult(
                    mcp.types.ReadResourceResult(
                        contents=mcp_contents,
                        _meta={
                            "modelcontextprotocol.io/task": {
                                "returned_immediately": True
                            }
                        },
                    )
                )

            # Convert to proper ServerResult
            if isinstance(result, mcp.types.ServerResult):
                return result

            mcp_contents = []
            for item in result:
                if isinstance(item.content, str):
                    mcp_contents.append(
                        mcp.types.TextResourceContents(
                            uri=uri,
                            text=item.content,
                            mimeType=item.mime_type or "text/plain",
                        )
                    )
                elif isinstance(item.content, bytes):
                    import base64

                    mcp_contents.append(
                        mcp.types.BlobResourceContents(
                            uri=uri,
                            blob=base64.b64encode(item.content).decode(),
                            mimeType=item.mime_type or "application/octet-stream",
                        )
                    )

            return mcp.types.ServerResult(
                mcp.types.ReadResourceResult(contents=mcp_contents)
            )

        self._mcp_server.request_handlers[mcp.types.ReadResourceRequest] = handler

    def _setup_get_prompt_handler(self) -> None:
        """
        Set up custom get_prompt handler that supports task-augmented responses.

        The SDK's get_prompt decorator doesn't support CreateTaskResult returns,
        so we register a custom handler that checks request_context.experimental.is_task.
        """

        async def handler(req: mcp.types.GetPromptRequest) -> mcp.types.ServerResult:
            name = req.params.name
            arguments = req.params.arguments

            # Check for task metadata via SDK's request context
            task_meta = None
            try:
                ctx = self._mcp_server.request_context
                if ctx.experimental.is_task:
                    task_meta = ctx.experimental.task_metadata
            except (AttributeError, LookupError):
                pass

            # Check for task metadata and route appropriately
            async with fastmcp.server.context.Context(fastmcp=self):
                prompts = await self.get_prompts()
                prompt = prompts.get(name)
                if (
                    prompt
                    and self._should_enable_component(prompt)
                    and hasattr(prompt, "task_config")
                    and prompt.task_config
                ):
                    task_mode = prompt.task_config.mode  # type: ignore[union-attr]

                    # Enforce mode="required" - must have task metadata
                    if task_mode == "required" and not task_meta:
                        raise McpError(
                            ErrorData(
                                code=METHOD_NOT_FOUND,
                                message=f"Prompt '{name}' requires task-augmented execution",
                            )
                        )

                    # Route to background if task metadata present and mode allows
                    if task_meta and task_mode != "forbidden":
                        task_meta_dict = task_meta.model_dump(exclude_none=True)
                        result = await handle_prompt_as_task(
                            self, name, arguments, task_meta_dict
                        )
                        return mcp.types.ServerResult(result)

                    # Forbidden mode: task requested but mode="forbidden"
                    # Raise error since prompts don't have isError field
                    if task_meta and task_mode == "forbidden":
                        raise McpError(
                            ErrorData(
                                code=METHOD_NOT_FOUND,
                                message=f"Prompt '{name}' does not support task-augmented execution",
                            )
                        )

            # Synchronous execution
            result = await self._get_prompt_mcp(name, arguments)
            return mcp.types.ServerResult(result)

        self._mcp_server.request_handlers[mcp.types.GetPromptRequest] = handler

    def _setup_task_protocol_handlers(self) -> None:
        """Register SEP-1686 task protocol handlers with SDK."""
        from mcp.types import (
            CancelTaskRequest,
            GetTaskPayloadRequest,
            GetTaskRequest,
            ListTasksRequest,
            ServerResult,
        )

        from fastmcp.server.tasks.protocol import (
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

    async def get_tools(self) -> dict[str, Tool]:
        """Get all tools (unfiltered), including mounted servers, indexed by key."""
        all_tools = dict(await self._tool_manager.get_tools())

        for mounted in self._mounted_servers:
            try:
                child_tools = await mounted.server.get_tools()
                for key, tool in child_tools.items():
                    new_key = f"{mounted.prefix}_{key}" if mounted.prefix else key
                    all_tools[new_key] = tool.model_copy(key=new_key)
            except Exception as e:
                logger.warning(
                    f"Failed to get tools from mounted server {mounted.server.name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return all_tools

    async def get_tool(self, key: str) -> Tool:
        tools = await self.get_tools()
        if key not in tools:
            raise NotFoundError(f"Unknown tool: {key}")
        return tools[key]

    async def _get_tool_with_task_config(self, key: str) -> Tool | None:
        """Get a tool by key, returning None if not found.

        Used for task config checking where we need the actual tool object
        (including from mounted servers and proxies) but don't want to raise.
        """
        try:
            return await self.get_tool(key)
        except NotFoundError:
            return None

    async def _get_resource_or_template_or_none(
        self, uri: str
    ) -> Resource | ResourceTemplate | None:
        """Get a resource or template by URI, searching recursively. Returns None if not found."""
        try:
            return await self.get_resource(uri)
        except NotFoundError:
            pass

        templates = await self.get_resource_templates()
        for template in templates.values():
            if template.matches(uri):
                return template

        return None

    async def get_resources(self) -> dict[str, Resource]:
        """Get all resources (unfiltered), including mounted servers, indexed by key."""
        all_resources = dict(await self._resource_manager.get_resources())

        for mounted in self._mounted_servers:
            try:
                child_resources = await mounted.server.get_resources()
                for key, resource in child_resources.items():
                    new_key = (
                        add_resource_prefix(key, mounted.prefix)
                        if mounted.prefix
                        else key
                    )
                    update = (
                        {"name": f"{mounted.prefix}_{resource.name}"}
                        if mounted.prefix and resource.name
                        else {}
                    )
                    all_resources[new_key] = resource.model_copy(
                        key=new_key, update=update
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to get resources from mounted server {mounted.server.name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return all_resources

    async def get_resource(self, key: str) -> Resource:
        resources = await self.get_resources()
        if key not in resources:
            raise NotFoundError(f"Unknown resource: {key}")
        return resources[key]

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all resource templates (unfiltered), including mounted servers, indexed by key."""
        all_templates = dict(await self._resource_manager.get_resource_templates())

        for mounted in self._mounted_servers:
            try:
                child_templates = await mounted.server.get_resource_templates()
                for key, template in child_templates.items():
                    new_key = (
                        add_resource_prefix(key, mounted.prefix)
                        if mounted.prefix
                        else key
                    )
                    update: dict[str, Any] = {}
                    if mounted.prefix:
                        if template.name:
                            update["name"] = f"{mounted.prefix}_{template.name}"
                        # Update uri_template so matches() works with prefixed URIs
                        update["uri_template"] = new_key
                    all_templates[new_key] = template.model_copy(
                        key=new_key, update=update
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to get resource templates from mounted server {mounted.server.name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return all_templates

    async def get_resource_template(self, key: str) -> ResourceTemplate:
        """Get a registered resource template by key."""
        templates = await self.get_resource_templates()
        if key not in templates:
            raise NotFoundError(f"Unknown resource template: {key}")
        return templates[key]

    async def get_prompts(self) -> dict[str, Prompt]:
        """Get all prompts (unfiltered), including mounted servers, indexed by key."""
        all_prompts = dict(await self._prompt_manager.get_prompts())

        for mounted in self._mounted_servers:
            try:
                child_prompts = await mounted.server.get_prompts()
                for key, prompt in child_prompts.items():
                    new_key = f"{mounted.prefix}_{key}" if mounted.prefix else key
                    all_prompts[new_key] = prompt.model_copy(key=new_key)
            except Exception as e:
                logger.warning(
                    f"Failed to get prompts from mounted server {mounted.server.name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return all_prompts

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
        """Get all additional HTTP routes including from mounted servers.

        Returns a list of all custom HTTP routes from this server and
        recursively from all mounted servers.

        Returns:
            List of Starlette BaseRoute objects
        """
        routes = list(self._additional_http_routes)

        # Recursively get routes from mounted servers
        for mounted_server in self._mounted_servers:
            mounted_routes = mounted_server.server._get_additional_http_routes()
            routes.extend(mounted_routes)

        return routes

    async def _list_tools_mcp(self) -> list[MCPTool]:
        """
        List all available tools, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_tools")

        async with fastmcp.server.context.Context(fastmcp=self):
            tools = await self._list_tools_middleware()
            return [
                tool.to_mcp_tool(
                    name=tool.key,
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
        """
        # 1. Get local tools and filter them
        local_tools = await self._tool_manager.get_tools()
        filtered_local = [
            tool for tool in local_tools.values() if self._should_enable_component(tool)
        ]

        # 2. Get tools from mounted servers
        # Mounted servers apply their own filtering, but we also apply parent's filtering
        # Use a dict to implement "later wins" deduplication by key
        all_tools: dict[str, Tool] = {tool.key: tool for tool in filtered_local}

        for mounted in self._mounted_servers:
            try:
                child_tools = await mounted.server._list_tools_middleware()
                for tool in child_tools:
                    # Apply parent server's filtering to mounted components
                    if not self._should_enable_component(tool):
                        continue

                    key = tool.key
                    if mounted.prefix:
                        key = f"{mounted.prefix}_{tool.key}"
                        tool = tool.model_copy(key=key)
                    # Later mounted servers override earlier ones
                    all_tools[key] = tool
            except Exception as e:
                server_name = getattr(
                    getattr(mounted, "server", None), "name", repr(mounted)
                )
                logger.warning(
                    f"Failed to list tools from mounted server {server_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return list(all_tools.values())

    async def _list_resources_mcp(self) -> list[MCPResource]:
        """
        List all available resources, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resources")

        async with fastmcp.server.context.Context(fastmcp=self):
            resources = await self._list_resources_middleware()
            return [
                resource.to_mcp_resource(
                    uri=resource.key,
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
        """
        # 1. Filter local resources
        local_resources = await self._resource_manager.get_resources()
        filtered_local = [
            resource
            for resource in local_resources.values()
            if self._should_enable_component(resource)
        ]

        # 2. Get from mounted servers with resource prefix handling
        # Mounted servers apply their own filtering, but we also apply parent's filtering
        # Use a dict to implement "later wins" deduplication by key
        all_resources: dict[str, Resource] = {
            resource.key: resource for resource in filtered_local
        }

        for mounted in self._mounted_servers:
            try:
                child_resources = await mounted.server._list_resources_middleware()
                for resource in child_resources:
                    # Apply parent server's filtering to mounted components
                    if not self._should_enable_component(resource):
                        continue

                    key = resource.key
                    if mounted.prefix:
                        key = add_resource_prefix(resource.key, mounted.prefix)
                        resource = resource.model_copy(
                            key=key,
                            update={"name": f"{mounted.prefix}_{resource.name}"},
                        )
                    # Later mounted servers override earlier ones
                    all_resources[key] = resource
            except Exception as e:
                server_name = getattr(
                    getattr(mounted, "server", None), "name", repr(mounted)
                )
                logger.warning(f"Failed to list resources from {server_name!r}: {e}")
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return list(all_resources.values())

    async def _list_resource_templates_mcp(self) -> list[MCPResourceTemplate]:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_resource_templates")

        async with fastmcp.server.context.Context(fastmcp=self):
            templates = await self._list_resource_templates_middleware()
            return [
                template.to_mcp_template(
                    uriTemplate=template.key,
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
        """
        # 1. Filter local templates
        local_templates = await self._resource_manager.get_resource_templates()
        filtered_local = [
            template
            for template in local_templates.values()
            if self._should_enable_component(template)
        ]

        # 2. Get from mounted servers with resource prefix handling
        # Mounted servers apply their own filtering, but we also apply parent's filtering
        # Use a dict to implement "later wins" deduplication by key
        all_templates: dict[str, ResourceTemplate] = {
            template.key: template for template in filtered_local
        }

        for mounted in self._mounted_servers:
            try:
                child_templates = (
                    await mounted.server._list_resource_templates_middleware()
                )
                for template in child_templates:
                    # Apply parent server's filtering to mounted components
                    if not self._should_enable_component(template):
                        continue

                    key = template.key
                    if mounted.prefix:
                        key = add_resource_prefix(template.key, mounted.prefix)
                        template = template.model_copy(
                            key=key,
                            update={"name": f"{mounted.prefix}_{template.name}"},
                        )
                    # Later mounted servers override earlier ones
                    all_templates[key] = template
            except Exception as e:
                server_name = getattr(
                    getattr(mounted, "server", None), "name", repr(mounted)
                )
                logger.warning(
                    f"Failed to list resource templates from {server_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return list(all_templates.values())

    async def _list_prompts_mcp(self) -> list[MCPPrompt]:
        """
        List all available prompts, in the format expected by the low-level MCP
        server.
        """
        logger.debug(f"[{self.name}] Handler called: list_prompts")

        async with fastmcp.server.context.Context(fastmcp=self):
            prompts = await self._list_prompts_middleware()
            return [
                prompt.to_mcp_prompt(
                    name=prompt.key,
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
        """
        # 1. Filter local prompts
        local_prompts = await self._prompt_manager.get_prompts()
        filtered_local = [
            prompt
            for prompt in local_prompts.values()
            if self._should_enable_component(prompt)
        ]

        # 2. Get from mounted servers
        # Mounted servers apply their own filtering, but we also apply parent's filtering
        # Use a dict to implement "later wins" deduplication by key
        all_prompts: dict[str, Prompt] = {
            prompt.key: prompt for prompt in filtered_local
        }

        for mounted in self._mounted_servers:
            try:
                child_prompts = await mounted.server._list_prompts_middleware()
                for prompt in child_prompts:
                    # Apply parent server's filtering to mounted components
                    if not self._should_enable_component(prompt):
                        continue

                    key = prompt.key
                    if mounted.prefix:
                        key = f"{mounted.prefix}_{prompt.key}"
                        prompt = prompt.model_copy(key=key)
                    # Later mounted servers override earlier ones
                    all_prompts[key] = prompt
            except Exception as e:
                server_name = getattr(
                    getattr(mounted, "server", None), "name", repr(mounted)
                )
                logger.warning(
                    f"Failed to list prompts from mounted server {server_name!r}: {e}"
                )
                if fastmcp.settings.mounted_components_raise_on_load_error:
                    raise
                continue

        return list(all_prompts.values())

    async def _call_tool_mcp(
        self, key: str, arguments: dict[str, Any]
    ) -> (
        list[ContentBlock]
        | tuple[list[ContentBlock], dict[str, Any]]
        | mcp.types.CallToolResult
    ):
        """
        Handle MCP 'callTool' requests.

        Detects SEP-1686 task metadata and routes to background execution if supported.

        Args:
            key: The name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            List of MCP Content objects containing the tool results
        """
        logger.debug(
            f"[{self.name}] Handler called: call_tool %s with %s", key, arguments
        )

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Check for SEP-1686 task metadata via request context
                task_meta = None
                try:
                    # Access task metadata from SDK's request context
                    ctx = self._mcp_server.request_context
                    if ctx.experimental.is_task:
                        task_meta = ctx.experimental.task_metadata
                except (AttributeError, LookupError):
                    # No request context available - proceed without task metadata
                    pass

                # Get tool from local manager, mounted servers, or proxy
                tool = await self._get_tool_with_task_config(key)
                if (
                    tool
                    and self._should_enable_component(tool)
                    and hasattr(tool, "task_config")
                ):
                    task_mode = tool.task_config.mode  # type: ignore[union-attr]

                    # Enforce mode="required" - must have task metadata
                    if task_mode == "required" and not task_meta:
                        raise McpError(
                            ErrorData(
                                code=METHOD_NOT_FOUND,
                                message=f"Tool '{key}' requires task-augmented execution",
                            )
                        )

                    # Route to background if task metadata present and mode allows
                    if task_meta and task_mode != "forbidden":
                        # For FunctionTool, use Docket for background execution
                        if isinstance(tool, FunctionTool):
                            task_meta_dict = task_meta.model_dump(exclude_none=True)
                            return await handle_tool_as_task(
                                self, key, arguments, task_meta_dict
                            )
                        # For ProxyTool/mounted tools, proceed with normal execution
                        # They will forward task metadata to their backend

                    # Forbidden mode: task requested but mode="forbidden"
                    # Return error result with returned_immediately=True
                    if task_meta and task_mode == "forbidden":
                        return mcp.types.CallToolResult(
                            content=[
                                mcp.types.TextContent(
                                    type="text",
                                    text=f"Tool '{key}' does not support task-augmented execution",
                                )
                            ],
                            isError=True,
                            _meta={
                                "modelcontextprotocol.io/task": {
                                    "returned_immediately": True
                                }
                            },
                        )

                # Synchronous execution (normal path)
                result = await self._call_tool_middleware(key, arguments)
                return result.to_mcp_result()
            except DisabledError as e:
                raise NotFoundError(f"Unknown tool: {key}") from e
            except NotFoundError as e:
                raise NotFoundError(f"Unknown tool: {key}") from e

    async def _call_tool_middleware(
        self,
        key: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """
        Applies this server's middleware and delegates the filtered call to the manager.
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
    ) -> ToolResult:
        """
        Call a tool
        """
        tool_name = context.message.name

        # Try mounted servers in reverse order (later wins)
        for mounted in reversed(self._mounted_servers):
            try_name = tool_name
            if mounted.prefix:
                if not tool_name.startswith(f"{mounted.prefix}_"):
                    continue
                try_name = tool_name[len(mounted.prefix) + 1 :]

            try:
                # First, get the tool to check if parent's filter allows it
                # Use get_tool() instead of _tool_manager.get_tool() to support
                # nested mounted servers (tools mounted more than 2 levels deep)
                tool = await mounted.server.get_tool(try_name)
                if not self._should_enable_component(tool):
                    # Parent filter blocks this tool, continue searching
                    continue

                return await mounted.server._call_tool_middleware(
                    try_name, context.message.arguments or {}
                )
            except NotFoundError:
                continue

        # Try local tools last (mounted servers override local)
        try:
            tool = await self._tool_manager.get_tool(tool_name)
            if self._should_enable_component(tool):
                return await self._tool_manager.call_tool(
                    key=tool_name, arguments=context.message.arguments or {}
                )
        except NotFoundError:
            pass

        raise NotFoundError(f"Unknown tool: {tool_name!r}")

    async def _read_resource_mcp(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        """
        Handle MCP 'readResource' requests.

        Delegates to _read_resource, which should be overridden by FastMCP subclasses.
        """
        logger.debug(f"[{self.name}] Handler called: read_resource %s", uri)

        async with fastmcp.server.context.Context(fastmcp=self):
            try:
                # Task routing handled by custom handler
                return list[ReadResourceContents](
                    await self._read_resource_middleware(uri)
                )
            except DisabledError as e:
                # convert to NotFoundError to avoid leaking resource presence
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e
            except NotFoundError as e:
                # standardize NotFound message
                raise NotFoundError(f"Unknown resource: {str(uri)!r}") from e

    async def _read_resource_middleware(
        self,
        uri: AnyUrl | str,
    ) -> list[ReadResourceContents]:
        """
        Applies this server's middleware and delegates the filtered call to the manager.
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
        return list(
            await self._apply_middleware(
                context=mw_context, call_next=self._read_resource
            )
        )

    async def _read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
    ) -> list[ReadResourceContents]:
        """
        Read a resource
        """
        uri_str = str(context.message.uri)

        # Try mounted servers in reverse order (later wins)
        for mounted in reversed(self._mounted_servers):
            key = uri_str
            if mounted.prefix:
                if not has_resource_prefix(key, mounted.prefix):
                    continue
                key = remove_resource_prefix(key, mounted.prefix)

            # First, get the resource/template to check if parent's filter allows it
            # Use get_resource_or_template to support nested mounted servers
            # (resources/templates mounted more than 2 levels deep)
            resource = await mounted.server._get_resource_or_template_or_none(key)
            if resource is None:
                continue
            if not self._should_enable_component(resource):
                # Parent filter blocks this resource, continue searching
                continue
            try:
                result = list(await mounted.server._read_resource_middleware(key))
                return result
            except NotFoundError:
                continue

        # Try local resources last (mounted servers override local)
        try:
            resource = await self._resource_manager.get_resource(uri_str)
            if self._should_enable_component(resource):
                content = await self._resource_manager.read_resource(uri_str)
                return [
                    ReadResourceContents(
                        content=content,
                        mime_type=resource.mime_type,
                    )
                ]
        except NotFoundError:
            pass

        raise NotFoundError(f"Unknown resource: {uri_str!r}")

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
    ) -> GetPromptResult:
        name = context.message.name

        # Try mounted servers in reverse order (later wins)
        for mounted in reversed(self._mounted_servers):
            try_name = name
            if mounted.prefix:
                if not name.startswith(f"{mounted.prefix}_"):
                    continue
                try_name = name[len(mounted.prefix) + 1 :]

            try:
                # First, get the prompt to check if parent's filter allows it
                # Use get_prompt() instead of _prompt_manager.get_prompt() to support
                # nested mounted servers (prompts mounted more than 2 levels deep)
                prompt = await mounted.server.get_prompt(try_name)
                if not self._should_enable_component(prompt):
                    # Parent filter blocks this prompt, continue searching
                    continue
                return await mounted.server._get_prompt_middleware(
                    try_name, context.message.arguments
                )
            except NotFoundError:
                continue

        # Try local prompts last (mounted servers override local)
        try:
            prompt = await self._prompt_manager.get_prompt(name)
            if self._should_enable_component(prompt):
                return await self._prompt_manager.render_prompt(
                    name=name, arguments=context.message.arguments
                )
        except NotFoundError:
            pass

        raise NotFoundError(f"Unknown prompt: {name!r}")

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
            exclude_args: Optional list of argument names to exclude from the tool schema.
                Note: `exclude_args` will be deprecated in FastMCP 2.14 in favor of dependency
                injection with `Depends()` for better lifecycle management.
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

            # Resolve task parameter
            supports_task: bool | TaskConfig = (
                task if task is not None else self._support_tasks_by_default
            )

            # Register the tool immediately and return the tool object
            # Note: Deprecation warning for exclude_args is handled in Tool.from_function
            tool = Tool.from_function(
                fn,
                name=tool_name,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                output_schema=output_schema,
                annotations=annotations,
                exclude_args=exclude_args,
                meta=meta,
                serializer=self._tool_serializer,
                enabled=enabled,
                task=supports_task,
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
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            exclude_args=exclude_args,
            meta=meta,
            enabled=enabled,
            task=task,
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
        if isinstance(annotations, dict):
            annotations = Annotations(**annotations)

        # Check if user passed function directly instead of calling decorator
        if inspect.isroutine(uri):
            raise TypeError(
                "The @resource decorator was used incorrectly. "
                "Did you forget to call it? Use @resource('uri') instead of @resource"
            )

        def decorator(fn: AnyFunction) -> Resource | ResourceTemplate:
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

            # Resolve task parameter
            supports_task: bool | TaskConfig = (
                task if task is not None else self._support_tasks_by_default
            )

            # Check if this should be a template
            has_uri_params = "{" in uri and "}" in uri
            # Use wrapper to check for user-facing parameters
            from fastmcp.server.dependencies import without_injected_parameters

            wrapper_fn = without_injected_parameters(fn)
            has_func_params = bool(inspect.signature(wrapper_fn).parameters)

            if has_uri_params or has_func_params:
                template = ResourceTemplate.from_function(
                    fn=fn,
                    uri_template=uri,
                    name=name,
                    title=title,
                    description=description,
                    icons=icons,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                    annotations=annotations,
                    meta=meta,
                    task=supports_task,
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
                    icons=icons,
                    mime_type=mime_type,
                    tags=tags,
                    enabled=enabled,
                    annotations=annotations,
                    meta=meta,
                    task=supports_task,
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

            # Resolve task parameter
            supports_task: bool | TaskConfig = (
                task if task is not None else self._support_tasks_by_default
            )

            # Register the prompt immediately
            prompt = Prompt.from_function(
                fn=fn,
                name=prompt_name,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                enabled=enabled,
                meta=meta,
                task=supports_task,
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
            icons=icons,
            tags=tags,
            enabled=enabled,
            meta=meta,
            task=task,
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
                path = app.state.path.lstrip("/")  # type: ignore
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
        prefix: str | None = None,
        as_proxy: bool | None = None,
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
        """
        from fastmcp.server.proxy import FastMCPProxy

        # if as_proxy is not specified and the server has a custom lifespan,
        # we should treat it as a proxy
        if as_proxy is None:
            as_proxy = server._lifespan != default_lifespan

        if as_proxy and not isinstance(server, FastMCPProxy):
            server = FastMCP.as_proxy(server)

        # Mark the server as mounted so it skips creating its own Docket/Worker.
        # The parent's Docket handles task execution, avoiding race conditions
        # with multiple workers competing for tasks from the same queue.
        server._is_mounted = True

        # Delegate mounting to all three managers
        mounted_server = MountedServer(
            prefix=prefix,
            server=server,
        )
        self._mounted_servers.append(mounted_server)

    async def import_server(
        self,
        server: FastMCP[LifespanResultT],
        prefix: str | None = None,
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
        """
        # Import tools from the server
        for key, tool in (await server.get_tools()).items():
            if prefix:
                tool = tool.model_copy(key=f"{prefix}_{key}")
            self._tool_manager.add_tool(tool)

        # Import resources and templates from the server
        for key, resource in (await server.get_resources()).items():
            if prefix:
                resource_key = add_resource_prefix(key, prefix)
                resource = resource.model_copy(
                    update={"name": f"{prefix}_{resource.name}"}, key=resource_key
                )
            self._resource_manager.add_resource(resource)

        for key, template in (await server.get_resource_templates()).items():
            if prefix:
                template_key = add_resource_prefix(key, prefix)
                template = template.model_copy(
                    update={"name": f"{prefix}_{template.name}"}, key=template_key
                )
            self._resource_manager.add_template(template)

        # Import prompts from the server
        for key, prompt in (await server.get_prompts()).items():
            if prefix:
                prompt = prompt.model_copy(key=f"{prefix}_{key}")
            self._prompt_manager.add_prompt(prompt)

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
        from fastmcp.server.proxy import FastMCPProxy, ProxyClient

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
            base_client = ProxyClient(backend)  # type: ignore

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


@dataclass
class MountedServer:
    prefix: str | None
    server: FastMCP[Any]


def add_resource_prefix(uri: str, prefix: str) -> str:
    """Add a prefix to a resource URI using path formatting (resource://prefix/path).

    Args:
        uri: The original resource URI
        prefix: The prefix to add

    Returns:
        The resource URI with the prefix added

    Examples:
        ```python
        add_resource_prefix("resource://path/to/resource", "prefix")
        "resource://prefix/path/to/resource"
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

    # Split the URI into protocol and path
    match = URI_PATTERN.match(uri)
    if not match:
        raise ValueError(f"Invalid URI format: {uri}. Expected protocol://path format.")

    protocol, path = match.groups()

    # Add the prefix to the path
    return f"{protocol}{prefix}/{path}"


def remove_resource_prefix(uri: str, prefix: str) -> str:
    """Remove a prefix from a resource URI.

    Args:
        uri: The resource URI with a prefix
        prefix: The prefix to remove

    Returns:
        The resource URI with the prefix removed

    Examples:
        ```python
        remove_resource_prefix("resource://prefix/path/to/resource", "prefix")
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

    # Split the URI into protocol and path
    match = URI_PATTERN.match(uri)
    if not match:
        raise ValueError(f"Invalid URI format: {uri}. Expected protocol://path format.")

    protocol, path = match.groups()

    # Check if the path starts with the prefix followed by a /
    prefix_pattern = f"^{re.escape(prefix)}/(.*?)$"
    path_match = re.match(prefix_pattern, path)
    if not path_match:
        return uri

    # Return the URI without the prefix
    return f"{protocol}{path_match.group(1)}"


def has_resource_prefix(uri: str, prefix: str) -> bool:
    """Check if a resource URI has a specific prefix.

    Args:
        uri: The resource URI to check
        prefix: The prefix to look for

    Returns:
        True if the URI has the specified prefix, False otherwise

    Examples:
        ```python
        has_resource_prefix("resource://prefix/path/to/resource", "prefix")
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

    # Split the URI into protocol and path
    match = URI_PATTERN.match(uri)
    if not match:
        raise ValueError(f"Invalid URI format: {uri}. Expected protocol://path format.")

    _, path = match.groups()

    # Check if the path starts with the prefix followed by a /
    prefix_pattern = f"^{re.escape(prefix)}/"
    return bool(re.match(prefix_pattern, path))
