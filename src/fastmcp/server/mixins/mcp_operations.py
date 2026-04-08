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
        self._mcp_server.set_logging_level()(self._set_logging_level_mcp)

        # Register event protocol handlers
        self._setup_event_protocol_handlers()

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

        tools = dedupe_with_versions(list(await server.list_tools()), lambda t: t.name)
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

        resources = dedupe_with_versions(
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

        templates = dedupe_with_versions(
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

        prompts = dedupe_with_versions(
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
                # Extract version from _meta.fastmcp
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
            raise McpError(
                mcp.types.ErrorData(
                    code=-32002, message=f"Resource not found: {str(uri)!r}"
                )
            ) from e
        except NotFoundError as e:
            raise McpError(
                mcp.types.ErrorData(code=-32002, message=f"Resource not found: {e}")
            ) from e

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

    async def _set_logging_level_mcp(self, level: mcp.types.LoggingLevel) -> None:
        """Handle MCP 'logging/setLevel' requests.

        Stores the requested minimum log level on the session so that
        subsequent log messages below this level are suppressed.
        """
        from fastmcp.server.low_level import MiddlewareServerSession

        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: set_logging_level %s", level)
        try:
            ctx = server._mcp_server.request_context
            session = ctx.session
            if isinstance(session, MiddlewareServerSession):
                session._minimum_logging_level = level
        except LookupError:
            pass

    # -------------------------------------------------------------------------
    # Event protocol handlers
    # -------------------------------------------------------------------------

    def _setup_event_protocol_handlers(self: FastMCP) -> None:
        """Placeholder/documentation hook for event protocol handling.

        This method intentionally performs no registration.  Event requests
        (``events/subscribe``, ``events/unsubscribe``, ``events/list``) are
        intercepted at the raw JSON-RPC level by
        ``MiddlewareServerSession._receive_loop`` because their request types
        are not in the published SDK's ``ClientRequest`` union.  The actual
        handler methods (``_handle_subscribe_events``, etc.) live on this mixin
        so ``FastMCP`` can invoke them from the session's request dispatch.

        Capabilities are advertised by ``LowLevelServer.get_capabilities()``
        based on the presence of declared event topics.

        This method exists so the ``_setup_handlers`` initialization sequence
        has an explicit, documented step for events, consistent with the other
        ``_setup_*`` methods, rather than silently relying on ``_receive_loop``
        interception alone.
        """
        # No SDK-level registration needed; handled via _receive_loop interception

    async def _handle_subscribe_events(
        self, ctx: Any, params: Any
    ) -> dict:
        """Handle events/subscribe requests."""
        from fastmcp.server.events import (
            EventSubscribeResult,
            RejectedTopic,
            SubscribedTopic,
        )

        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: events/subscribe")

        # Get the session from the request context
        session = ctx.session
        session_id = getattr(session, "_fastmcp_event_session_id", None)

        if session_id is None:
            raise McpError(
                mcp.types.ErrorData(
                    code=-32603,
                    message="No session context available for subscription",
                )
            )

        topics = params.topics if hasattr(params, "topics") else params.get("topics", [])

        subscribed: list[SubscribedTopic] = []
        rejected: list[RejectedTopic] = []
        retained_events = []

        for pattern in topics:
            # Validate topic depth (max 8 segments)
            segments = pattern.split("/")
            if len(segments) > server._MAX_TOPIC_DEPTH:
                raise McpError(
                    mcp.types.ErrorData(
                        code=-32602,
                        message=(
                            f"Subscription pattern has {len(segments)} segments, "
                            f"maximum depth is {server._MAX_TOPIC_DEPTH}: {pattern!r}"
                        ),
                    )
                )

            # Check if the pattern matches any declared topic
            if not server._match_declared_topic(pattern):
                rejected.append(RejectedTopic(pattern=pattern, reason="unknown_topic"))
                continue

            await server._subscription_registry.add(session_id, pattern)
            subscribed.append(SubscribedTopic(pattern=pattern))

            # Deliver retained values for this pattern
            matching = await server._retained_store.get_matching(pattern)
            retained_events.extend(matching)

        result = EventSubscribeResult(
            subscribed=subscribed,
            rejected=rejected,
            retained=retained_events,
        )
        return result.model_dump(exclude_none=True)

    async def _handle_unsubscribe_events(
        self, ctx: Any, params: Any
    ) -> dict:
        """Handle events/unsubscribe requests."""
        from fastmcp.server.events import EventUnsubscribeResult

        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: events/unsubscribe")

        session = ctx.session
        session_id = getattr(session, "_fastmcp_event_session_id", None)

        topics = params.topics if hasattr(params, "topics") else params.get("topics", [])

        unsubscribed: list[str] = []
        if session_id is not None:
            for pattern in topics:
                await server._subscription_registry.remove(session_id, pattern)
                unsubscribed.append(pattern)

        result = EventUnsubscribeResult(unsubscribed=unsubscribed)
        return result.model_dump(exclude_none=True)

    async def _handle_list_events(
        self, ctx: Any, params: Any
    ) -> dict:
        """Handle events/list requests."""
        from fastmcp.server.events import EventListResult

        server = cast("FastMCP", self)
        logger.debug(f"[{server.name}] Handler called: events/list")

        topics = list(server._event_topics.values())
        result = EventListResult(topics=topics)
        return result.model_dump(exclude_none=True, by_alias=True)

    def _match_declared_topic(self: FastMCP, pattern: str) -> bool:
        """Check whether a subscription pattern matches any declared event topic.

        Handles both exact matches and wildcard patterns that could match
        declared topic patterns. For example, subscription pattern "myapp/+"
        matches declared topic "myapp/{session_id}".

        Uses regex-based matching in both directions: the subscription pattern
        is checked against declared patterns (with {param} as single-segment
        wildcards), and declared patterns are checked against the subscription
        pattern (with + and # as MQTT wildcards).
        """
        import re as _re

        from fastmcp.server.events import _pattern_to_regex

        # Direct match
        if pattern in self._event_topics:
            return True

        for declared_pattern in self._event_topics:
            # Forward: build regex from declared pattern's {param} placeholders
            # and test whether the subscription pattern (with wildcards replaced
            # by a synthetic single-segment value) matches.
            declared_regex_parts = []
            for segment in declared_pattern.split("/"):
                if segment.startswith("{") and segment.endswith("}"):
                    declared_regex_parts.append("[^/]+")
                else:
                    declared_regex_parts.append(_re.escape(segment))
            declared_regex = _re.compile(
                "^" + "/".join(declared_regex_parts) + "$"
            )

            # Replace MQTT wildcards with a synthetic literal segment for
            # testing against the declared pattern regex.
            test_pattern = _re.sub(r"[+#]", "x", pattern)
            if declared_regex.match(test_pattern):
                return True

            # Reverse: does the declared pattern (with {param} replaced by a
            # synthetic literal) match the subscription pattern's MQTT regex?
            concrete_declared = _re.sub(r"\{[^}]+\}", "x", declared_pattern)
            try:
                sub_regex = _pattern_to_regex(pattern)
                if sub_regex.match(concrete_declared):
                    return True
            except ValueError:
                continue

        return False
