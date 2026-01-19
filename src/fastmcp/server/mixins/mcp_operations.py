"""MCP protocol handler setup and wire-format handlers for FastMCP Server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import ContentBlock
from pydantic import AnyUrl

from fastmcp.exceptions import DisabledError, NotFoundError
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.pagination import paginate_sequence
from fastmcp.utilities.versions import VersionSpec, parse_version_key, version_sort_key

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)

C = TypeVar("C", bound=Any)
PaginateT = TypeVar("PaginateT")


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


def _apply_pagination(
    items: Sequence[PaginateT],
    cursor: str | None,
    page_size: int | None,
) -> tuple[list[PaginateT], str | None]:
    """Apply pagination to items, raising McpError for invalid cursors.

    If page_size is None, returns all items without pagination.
    """
    if page_size is None:
        return list(items), None
    try:
        return paginate_sequence(items, cursor, page_size)
    except ValueError as e:
        raise McpError(mcp.types.ErrorData(code=-32602, message=str(e))) from e


class MCPOperationsMixin:
    """Mixin providing MCP protocol handler setup and wire-format handlers.

    Note: Methods registered with SDK decorators (e.g., _list_tools_mcp, _call_tool_mcp)
    cannot use `self: FastMCP` type hints because the SDK's `get_type_hints()` fails
    to resolve FastMCP at runtime (it's only available under TYPE_CHECKING). When
    type hints fail to resolve, the SDK falls back to calling handlers with no arguments.
    These methods use untyped `self` to avoid this issue.
    """

    def _setup_handlers(self: FastMCP) -> None:
        """Set up core MCP protocol handlers.

        List handlers use SDK decorators that pass the request object to our handler
        (needed for pagination cursor). The SDK also populates caches like _tool_cache.

        Exception: list_resource_templates SDK decorator doesn't pass the request,
        so we register that handler directly.

        The call_tool decorator is from the SDK (supports CreateTaskResult + validate_input).
        The read_resource and get_prompt decorators are from LowLevelServer to add
        CreateTaskResult support until the SDK provides it natively.
        """
        self._mcp_server.list_tools()(self._list_tools_mcp)
        self._mcp_server.list_resources()(self._list_resources_mcp)
        self._mcp_server.list_prompts()(self._list_prompts_mcp)

        # list_resource_templates SDK decorator doesn't pass the request to handlers,
        # so we register directly to get cursor access for pagination
        self._mcp_server.request_handlers[mcp.types.ListResourceTemplatesRequest] = (
            self._wrap_list_handler(self._list_resource_templates_mcp)
        )

        self._mcp_server.call_tool(validate_input=self.strict_input_validation)(
            self._call_tool_mcp
        )
        self._mcp_server.read_resource()(self._read_resource_mcp)
        self._mcp_server.get_prompt()(self._get_prompt_mcp)

        # Register SEP-1686 task protocol handlers
        self._setup_task_protocol_handlers()

    def _wrap_list_handler(
        self: FastMCP, handler: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[mcp.types.ServerResult]]:
        """Wrap a list handler to pass the request and return ServerResult."""

        async def wrapper(request: Any) -> mcp.types.ServerResult:
            result = await handler(request)
            return mcp.types.ServerResult(result)

        return wrapper

    async def _list_tools_mcp(
        self, request: mcp.types.ListToolsRequest
    ) -> mcp.types.ListToolsResult:
        """
        List all available tools, in the format expected by the low-level MCP
        server. Supports pagination when list_page_size is configured.
        """
        # Cast self to FastMCP for type checking (see class docstring for why
        # we can't use `self: FastMCP` annotation on SDK-registered handlers)
        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: list_tools")

        tools = _dedupe_with_versions(list(await server.list_tools()), lambda t: t.name)
        sdk_tools = [tool.to_mcp_tool(name=tool.name) for tool in tools]
        # SDK may pass None for internal cache refresh despite type hint
        cursor = (
            request.params.cursor if request is not None and request.params else None
        )
        page, next_cursor = _apply_pagination(sdk_tools, cursor, server._list_page_size)
        return mcp.types.ListToolsResult(tools=page, nextCursor=next_cursor)

    async def _list_resources_mcp(
        self, request: mcp.types.ListResourcesRequest
    ) -> mcp.types.ListResourcesResult:
        """
        List all available resources, in the format expected by the low-level MCP
        server. Supports pagination when list_page_size is configured.
        """
        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: list_resources")

        resources = _dedupe_with_versions(
            list(await server.list_resources()), lambda r: str(r.uri)
        )
        sdk_resources = [
            resource.to_mcp_resource(uri=str(resource.uri)) for resource in resources
        ]
        cursor = request.params.cursor if request.params else None
        page, next_cursor = _apply_pagination(
            sdk_resources, cursor, server._list_page_size
        )
        return mcp.types.ListResourcesResult(resources=page, nextCursor=next_cursor)

    async def _list_resource_templates_mcp(
        self, request: mcp.types.ListResourceTemplatesRequest
    ) -> mcp.types.ListResourceTemplatesResult:
        """
        List all available resource templates, in the format expected by the low-level MCP
        server. Supports pagination when list_page_size is configured.
        """
        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: list_resource_templates")

        templates = _dedupe_with_versions(
            list(await server.list_resource_templates()), lambda t: t.uri_template
        )
        sdk_templates = [
            template.to_mcp_template(uriTemplate=template.uri_template)
            for template in templates
        ]
        cursor = request.params.cursor if request.params else None
        page, next_cursor = _apply_pagination(
            sdk_templates, cursor, server._list_page_size
        )
        return mcp.types.ListResourceTemplatesResult(
            resourceTemplates=page, nextCursor=next_cursor
        )

    async def _list_prompts_mcp(
        self, request: mcp.types.ListPromptsRequest
    ) -> mcp.types.ListPromptsResult:
        """
        List all available prompts, in the format expected by the low-level MCP
        server. Supports pagination when list_page_size is configured.
        """
        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: list_prompts")

        prompts = _dedupe_with_versions(
            list(await server.list_prompts()), lambda p: p.name
        )
        sdk_prompts = [prompt.to_mcp_prompt(name=prompt.name) for prompt in prompts]
        cursor = request.params.cursor if request.params else None
        page, next_cursor = _apply_pagination(
            sdk_prompts, cursor, server._list_page_size
        )
        return mcp.types.ListPromptsResult(prompts=page, nextCursor=next_cursor)

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
        server = cast("FastMCP", self)
        logger.debug(
            f"[{server.name}] Handler called: call_tool %s with %s", key, arguments
        )

        try:
            # Extract version and task metadata from request context.
            # fn_key is set by call_tool() after finding the tool.
            version_str: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = server._mcp_server.request_context
                # Extract version from request-level _meta.fastmcp.version
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    version_str = meta_dict.get("fastmcp", {}).get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            version = VersionSpec(eq=version_str) if version_str else None
            result = await server.call_tool(
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
        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: read_resource %s", uri)

        try:
            # Extract version and task metadata from request context.
            version_str: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = server._mcp_server.request_context
                # Extract version from _meta.fastmcp.version if provided
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    fastmcp_meta = meta_dict.get("fastmcp") or {}
                    version_str = fastmcp_meta.get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            version = VersionSpec(eq=version_str) if version_str else None
            result = await server.read_resource(
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
        server = cast("FastMCP", self)
        logger.debug(
            f"[{server.name}] Handler called: get_prompt %s with %s", name, arguments
        )

        try:
            # Extract version and task metadata from request context.
            # fn_key is set by render_prompt() after finding the prompt.
            version_str: str | None = None
            task_meta: TaskMeta | None = None
            try:
                ctx = server._mcp_server.request_context
                # Extract version from request-level _meta.fastmcp.version
                if ctx.meta:
                    meta_dict = ctx.meta.model_dump(exclude_none=True)
                    version_str = meta_dict.get("fastmcp", {}).get("version")
                # Extract SEP-1686 task metadata
                if ctx.experimental.is_task:
                    mcp_task_meta = ctx.experimental.task_metadata
                    task_meta_dict = mcp_task_meta.model_dump(exclude_none=True)
                    task_meta = TaskMeta(ttl=task_meta_dict.get("ttl"))
            except (AttributeError, LookupError):
                pass

            version = VersionSpec(eq=version_str) if version_str else None
            result = await server.render_prompt(
                name, arguments, version=version, task_meta=task_meta
            )

            if isinstance(result, mcp.types.CreateTaskResult):
                return result
            return result.to_mcp_prompt_result()
        except DisabledError as e:
            raise NotFoundError(f"Unknown prompt: {name!r}") from e
        except NotFoundError:
            raise
