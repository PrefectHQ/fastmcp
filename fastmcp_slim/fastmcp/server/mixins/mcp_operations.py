"""MCP protocol handler setup and wire-format handlers for FastMCP Server."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import mcp_types
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolRequestParams,
    EmptyResult,
    GetPromptRequestParams,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    SetLevelRequestParams,
)

from fastmcp.exceptions import DisabledError, FastMCPError, NotFoundError
from fastmcp.server.dependencies import bind_request_context, extract_version_spec
from fastmcp.server.tasks.config import TaskMeta
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
        raise MCPError(code=-32602, message=str(e)) from e


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
    ) -> mcp_types.CallToolResult | mcp_types.CreateTaskResult:
        """Handle MCP 'tools/call' requests.

        Task metadata is a first-class params field (``params.task``); its
        presence triggers backgrounding. The tool's ``_run()`` handles the
        backgrounding decision so middleware runs before Docket.
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
                raise MCPError(
                    code=-32002, message=f"Resource not found: {str(uri)!r}"
                ) from e

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
                raise MCPError(code=-32602, message=f"Unknown prompt: {name!r}") from e

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
