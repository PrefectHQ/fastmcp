"""MCP protocol handler setup and wire-format handlers for FastMCP Server."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast

import mcp_types
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CompleteRequestParams,
    EmptyResult,
    GetPromptRequestParams,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    SetLevelRequestParams,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.exceptions import (
    DisabledError,
    FastMCPError,
    NotFoundError,
    to_mcp_error,
)
from fastmcp.server.completions import CompletionValues, normalize_completion
from fastmcp.server.dependencies import bind_request_context, extract_version_spec
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.tools.base import InputRequiredToolResult
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.pagination import paginate_sequence
from fastmcp.utilities.versions import VersionSpec, dedupe_with_versions

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)

PaginateT = TypeVar("PaginateT")


def _apply_pagination(
    items: Sequence[PaginateT],
    cursor: str | None,
    page_size: int | None,
) -> tuple[list[PaginateT], str | None]:
    """Apply pagination to items, raising MCPError for invalid cursors.

    If page_size is None, returns all items without pagination.
    """
    if page_size is None:
        return list(items), None
    try:
        return paginate_sequence(items, cursor, page_size)
    except ValueError as e:
        raise MCPError(code=INVALID_PARAMS, message=str(e)) from e


def _normalize_call_tool_result(
    result: Any,
) -> mcp_types.CallToolResult:
    """Normalize a tool's ``to_mcp_result()`` output into a ``CallToolResult``.

    ``ToolResult.to_mcp_result()`` returns one of three shapes for backward
    compatibility: a ``CallToolResult`` (error/meta case), a bare
    ``list[ContentBlock]`` (unstructured), or a ``(content, structured)`` tuple.
    The SDK v2 runner requires a ``BaseModel`` result, so wrap the shorthand
    forms here (the SDK's old ``call_tool`` decorator used to do this).
    """
    if isinstance(result, mcp_types.CallToolResult):
        return result
    if isinstance(result, tuple):
        content, structured = result
        return mcp_types.CallToolResult(content=content, structured_content=structured)
    return mcp_types.CallToolResult(content=result)


def _version_from_ctx(ctx: ServerRequestContext) -> VersionSpec | None:
    """Extract the FastMCP component version from the request's lifted _meta."""
    from fastmcp.server.dependencies import _lift_meta

    version_str = extract_version_spec(_lift_meta(ctx))
    return VersionSpec(eq=version_str) if version_str else None


class MCPOperationsMixin:
    """Mixin providing MCP protocol handler setup and wire-format handlers.

    Handlers are registered via ``add_request_handler(method, params_type,
    handler)`` on the low-level SDK server. Each adapter takes
    ``(ctx: ServerRequestContext, params)`` and returns the bare SDK result
    model (no ``ServerResult`` wrapping — the SDK v2 runner serializes the
    result itself).
    """

    def _setup_handlers(self: FastMCP) -> None:
        """Register core MCP protocol handlers with the low-level SDK server."""
        s = self._mcp_server
        s.add_request_handler("tools/list", PaginatedRequestParams, self._on_list_tools)
        s.add_request_handler(
            "resources/list", PaginatedRequestParams, self._on_list_resources
        )
        s.add_request_handler(
            "resources/templates/list",
            PaginatedRequestParams,
            self._on_list_resource_templates,
        )
        s.add_request_handler(
            "prompts/list", PaginatedRequestParams, self._on_list_prompts
        )
        s.add_request_handler("tools/call", CallToolRequestParams, self._on_call_tool)
        s.add_request_handler(
            "resources/read", ReadResourceRequestParams, self._on_read_resource
        )
        s.add_request_handler(
            "prompts/get", GetPromptRequestParams, self._on_get_prompt
        )
        s.add_request_handler(
            "logging/setLevel", SetLevelRequestParams, self._on_set_logging_level
        )

        # Register SEP-1686 task protocol handlers
        self._setup_task_protocol_handlers()

    async def _on_list_tools(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        """List all available tools. Supports pagination via params.cursor."""
        with bind_request_context(ctx):
            logger.debug(f"[{self.name}] Handler called: list_tools")

            tools = dedupe_with_versions(
                list(await self.list_tools()), lambda t: t.name
            )
            sdk_tools = [tool.to_mcp_tool(name=tool.name) for tool in tools]
            cursor = params.cursor if params else None
            page, next_cursor = _apply_pagination(
                sdk_tools, cursor, self._list_page_size
            )
            return mcp_types.ListToolsResult(tools=page, next_cursor=next_cursor)

    async def _on_list_resources(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> mcp_types.ListResourcesResult:
        """List all available resources. Supports pagination via params.cursor."""
        with bind_request_context(ctx):
            logger.debug(f"[{self.name}] Handler called: list_resources")

            resources = dedupe_with_versions(
                list(await self.list_resources()), lambda r: str(r.uri)
            )
            sdk_resources = [
                resource.to_mcp_resource(uri=str(resource.uri))
                for resource in resources
            ]
            cursor = params.cursor if params else None
            page, next_cursor = _apply_pagination(
                sdk_resources, cursor, self._list_page_size
            )
            return mcp_types.ListResourcesResult(
                resources=page, next_cursor=next_cursor
            )

    async def _on_list_resource_templates(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> mcp_types.ListResourceTemplatesResult:
        """List all available resource templates. Supports pagination."""
        with bind_request_context(ctx):
            logger.debug(f"[{self.name}] Handler called: list_resource_templates")

            templates = dedupe_with_versions(
                list(await self.list_resource_templates()), lambda t: t.uri_template
            )
            sdk_templates = [
                template.to_mcp_template(uri_template=template.uri_template)
                for template in templates
            ]
            cursor = params.cursor if params else None
            page, next_cursor = _apply_pagination(
                sdk_templates, cursor, self._list_page_size
            )
            return mcp_types.ListResourceTemplatesResult(
                resource_templates=page, next_cursor=next_cursor
            )

    async def _on_list_prompts(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> mcp_types.ListPromptsResult:
        """List all available prompts. Supports pagination via params.cursor."""
        with bind_request_context(ctx):
            logger.debug(f"[{self.name}] Handler called: list_prompts")

            prompts = dedupe_with_versions(
                list(await self.list_prompts()), lambda p: p.name
            )
            sdk_prompts = [prompt.to_mcp_prompt(name=prompt.name) for prompt in prompts]
            cursor = params.cursor if params else None
            page, next_cursor = _apply_pagination(
                sdk_prompts, cursor, self._list_page_size
            )
            return mcp_types.ListPromptsResult(prompts=page, next_cursor=next_cursor)

    async def _on_call_tool(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: CallToolRequestParams,
    ) -> (
        mcp_types.CallToolResult
        | mcp_types.InputRequiredResult
        | mcp_types.CreateTaskResult
    ):
        """Handle MCP 'tools/call' requests.

        Task metadata is a first-class params field (``params.task``); its
        presence triggers backgrounding. The tool's ``_run()`` handles the
        backgrounding decision so middleware runs before Docket.

        A guard tool (SEP-2322 multi-round-trip) requests client input by
        returning an ``InputRequiredResult`` from its body; the run machinery
        wraps that in an ``InputRequiredToolResult`` (a ``ToolResult``
        subclass), which flows back through the middleware chain as an ordinary
        result. Here we unwrap it and hand the raw ``InputRequiredResult`` to
        the runner so it reaches the wire as ``resultType: "input_required"``
        (the request-state boundary seals its ``requestState`` on egress). This
        result shape only exists at 2026-07-28; on an earlier connection the
        runner cannot serialize it, so we reject with a clear era error rather
        than let it fail as a generic "invalid result".
        """
        with bind_request_context(ctx):
            key = params.name
            arguments = params.arguments or {}
            logger.debug(
                f"[{self.name}] Handler called: call_tool %s with %s", key, arguments
            )

            version = _version_from_ctx(ctx)
            task_meta = (
                TaskMeta(ttl=params.task.ttl) if params.task is not None else None
            )

            try:
                result = await self.call_tool(
                    key, arguments, version=version, task_meta=task_meta
                )
            except (DisabledError, NotFoundError):
                # Unknown/disabled tool: return an error result (matching the
                # v1 SDK's call_tool behavior) so the client surfaces a
                # ToolError rather than a raw protocol error.
                return mcp_types.CallToolResult(
                    content=[
                        mcp_types.TextContent(
                            type="text", text=f"Unknown tool: {key!r}"
                        )
                    ],
                    is_error=True,
                )
            except FastMCPError as e:
                # Tool-visible errors (ToolError, ValidationError, ...) must be
                # RETURNED as an error result, never raised — the SDK v2 runner
                # turns a raise into a -32603 wire error. Masking already
                # happened inside call_tool.
                return mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(type="text", text=str(e))],
                    is_error=True,
                )

            if isinstance(result, mcp_types.CreateTaskResult):
                return result
            if isinstance(result, InputRequiredToolResult):
                # A guard tool requested client input (SEP-2322). The
                # multi-round-trip result type only exists at 2026-07-28; on an
                # earlier connection the runner cannot serialize it, so name the
                # era problem instead of failing as a generic "invalid result".
                if ctx.protocol_version not in MODERN_PROTOCOL_VERSIONS:
                    raise MCPError(
                        code=INVALID_PARAMS,
                        message=(
                            f"Tool {key!r} returned an InputRequiredResult to request "
                            "client input, but the multi-round-trip result type "
                            "(SEP-2322) only exists at MCP 2026-07-28; this connection "
                            f"negotiated {ctx.protocol_version!r}. Use ctx.elicit() for "
                            "server-initiated input on handshake-era connections."
                        ),
                    )
                return result.input_required
            return _normalize_call_tool_result(result.to_mcp_result())

    async def _on_read_resource(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: ReadResourceRequestParams,
    ) -> mcp_types.ReadResourceResult | mcp_types.CreateTaskResult:
        """Handle MCP 'resources/read' requests.

        Note: ``ReadResourceRequestParams`` has no ``task`` field in this SDK
        version, so resource task submission over the wire is not expressible;
        ``task_meta`` is always None here. The CreateTaskResult return branch is
        retained harmlessly pending an upstream ``task`` field on these params.
        """
        with bind_request_context(ctx):
            uri = params.uri
            logger.debug(f"[{self.name}] Handler called: read_resource %s", uri)

            version = _version_from_ctx(ctx)

            try:
                result = await self.read_resource(str(uri), version=version)
            except (DisabledError, NotFoundError) as e:
                raise to_mcp_error(
                    NotFoundError(f"Resource not found: {str(uri)!r}")
                ) from e
            except FastMCPError as e:
                # Resource-visible errors (ResourceError, ValidationError, ...)
                # must reach the wire as an MCPError. Resources have no
                # error-result shape the way tools do, so the equivalent of
                # _on_call_tool's error result is a translated MCPError: at
                # 2026-07-28 the runner only preserves MCPError/ValidationError
                # messages and masks anything else as "Internal server error",
                # which would hide a legitimate client-input error. Masking
                # already happened inside read_resource.
                raise to_mcp_error(e) from e

            if isinstance(result, mcp_types.CreateTaskResult):
                return result
            return result.to_mcp_result(uri)

    async def _on_get_prompt(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: GetPromptRequestParams,
    ) -> mcp_types.GetPromptResult | mcp_types.CreateTaskResult:
        """Handle MCP 'prompts/get' requests.

        Note: ``GetPromptRequestParams`` has no ``task`` field in this SDK
        version, so prompt task submission over the wire is not expressible;
        ``task_meta`` is always None here.
        """
        with bind_request_context(ctx):
            name = params.name
            arguments = params.arguments
            logger.debug(
                f"[{self.name}] Handler called: get_prompt %s with %s",
                name,
                arguments,
            )

            version = _version_from_ctx(ctx)

            try:
                result = await self.render_prompt(name, arguments, version=version)
            except (DisabledError, NotFoundError) as e:
                raise to_mcp_error(NotFoundError(f"Unknown prompt: {name!r}")) from e
            except FastMCPError as e:
                # Prompt-visible errors (PromptError, ValidationError, ...) must
                # reach the wire as an MCPError for the same reason as
                # resources: at 2026-07-28 anything that is not an
                # MCPError/ValidationError is masked as "Internal server error".
                # Masking already happened inside render_prompt.
                raise to_mcp_error(e) from e

            if isinstance(result, mcp_types.CreateTaskResult):
                return result
            return result.to_mcp_prompt_result()

    async def _on_set_logging_level(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: SetLevelRequestParams,
    ) -> mcp_types.EmptyResult:
        """Handle MCP 'logging/setLevel' requests.

        Stores the requested minimum log level keyed by session id so that
        subsequent log messages below this level are suppressed. v2 sessions are
        per-request, so this state lives on the FastMCP server.
        """
        from fastmcp.server.context import _log_level_session_key

        with bind_request_context(ctx) as rc:
            logger.debug(
                f"[{self.name}] Handler called: set_logging_level %s", params.level
            )
            session_id = _log_level_session_key(rc.session)
            self._client_log_levels[session_id] = params.level
            return EmptyResult()

    async def _on_complete(
        self: FastMCP,
        ctx: ServerRequestContext,
        params: CompleteRequestParams,
    ) -> mcp_types.CompleteResult:
        """Handle MCP 'completion/complete' requests.

        Routes to the server's registered completion handler (set via
        ``@mcp.completion``). The handler switches on the reference and argument
        and returns candidate values. A handler that does not recognize the
        reference/argument returns ``None`` or an empty sequence, which becomes
        an empty completion rather than an error — an unknown reference is not a
        protocol failure. This handler is registered on the low-level server
        only once a completion handler exists, so the completions capability is
        declared exactly when the server can answer.
        """
        with bind_request_context(ctx):
            logger.debug(f"[{self.name}] Handler called: complete %s", params.ref)
            handler = self._completion_handler
            if handler is None:
                return mcp_types.CompleteResult(
                    completion=mcp_types.Completion(values=[])
                )

            if is_coroutine_function(handler):
                raw = handler(params.ref, params.argument, params.context)
            else:
                # A sync handler may perform blocking work (a database lookup,
                # say); run it in a threadpool so it does not stall the event
                # loop, matching how sync tools/prompts/resources are invoked.
                raw = await call_sync_fn_in_threadpool(
                    handler, params.ref, params.argument, params.context
                )
            result = await raw if inspect.isawaitable(raw) else raw
            completion = normalize_completion(cast(CompletionValues, result))
            return mcp_types.CompleteResult(completion=completion)

    def _register_completion_handler(self: FastMCP) -> None:
        """Register the low-level ``completion/complete`` handler.

        Called when a completion handler is set (via
        ``add_completion_handler``) so the SDK derives the completions
        capability from the handler's presence. Registration is idempotent —
        re-registering replaces the handler.
        """
        self._mcp_server.add_request_handler(
            "completion/complete",
            CompleteRequestParams,
            self._on_complete,
        )
