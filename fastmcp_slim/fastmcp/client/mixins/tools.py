"""Tool-related methods for FastMCP Client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp.client.caching import CacheMode
from opentelemetry.trace import Status, StatusCode

if TYPE_CHECKING:
    import datetime

    from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.telemetry import client_span
from fastmcp.exceptions import ToolError
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.json_schema_type import json_schema_to_type
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.timeout import normalize_timeout_to_seconds
from fastmcp.utilities.types import get_cached_typeadapter

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


class ClientToolsMixin:
    """Mixin providing tool-related methods for Client."""

    # --- Tools ---

    async def list_tools_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> mcp_types.ListToolsResult:
        """Send a tools/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.
            cache_mode: Response-cache behavior for this call (only active when the
                client was built with a cache and the connection is modern). `"use"`
                (default) serves and stores; `"refresh"` stores without serving;
                `"bypass"` skips the cache. A cursor page always skips the cache.

        Returns:
            mcp_types.ListToolsResult: The complete response object from the protocol,
                containing the list of tools and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "tools/list",
            "tools/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called list_tools")

            params = (
                mcp_types.PaginatedRequestParams(cursor=cursor)
                if cursor is not None
                else None
            )

            async def _send() -> mcp_types.ListToolsResult:
                return await self._await_with_session_monitoring(
                    self.session.list_tools(params=params)
                )

            return await self._cached_fetch(
                "tools/list",
                cursor=cursor,
                cache_mode=cache_mode,
                send=_send,
                # A cache hit skips session.list_tools, so re-absorb the served
                # listing to rebuild the session's derived per-tool state. Hits are
                # cursorless, but a cached page 1 can carry next_cursor — never prune
                # on a partial listing.
                absorb=lambda hit: self.session._absorb_tool_listing(
                    hit, complete=hit.next_cursor is None
                ),
            )

    async def list_tools(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
    ) -> list[mcp_types.Tool]:
        """Retrieve all tools available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_tools_mcp() with the cursor parameter.

        Args:
            max_pages: Maximum number of pages to fetch before raising. Defaults to 250.

        Returns:
            list[mcp_types.Tool]: A list of all Tool objects.

        Raises:
            RuntimeError: If the page limit is reached before pagination completes.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        all_tools: list[mcp_types.Tool] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_tools_mcp(cursor=cursor)
            all_tools.extend(result.tools)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    f"[{self.name}] Server returned duplicate pagination cursor"
                    f" {result.next_cursor!r} for list_tools; stopping pagination"
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit"
                f" ({max_pages} pages) for list_tools."
                " Use list_tools_mcp() with cursor for manual pagination,"
                " or increase max_pages."
            )

        return all_tools

    # --- Call Tool ---

    async def call_tool_mcp(
        self: Client,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp_types.CallToolResult:
        """Send a tools/call request and return the complete MCP protocol result.

        This method returns the raw CallToolResult object, which includes an isError flag
        and other metadata. It does not raise an exception if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any]): Arguments to pass to the tool.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.
            meta (dict[str, Any] | None, optional): Additional metadata to include with the request.
                This is useful for passing contextual information (like user IDs, trace IDs, or preferences)
                that shouldn't be tool arguments but may influence server-side processing. The server
                can access this via `context.request_context.meta`. Defaults to None.

        Returns:
            mcp_types.CallToolResult: The complete response object from the protocol,
                containing the tool result and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the tool call requests results in a TimeoutError | JSONRPCError
        """
        with client_span(
            f"tools/call {name}",
            "tools/call",
            name,
            session_id=self.transport.get_session_id(),
            tool_name=name,
        ) as span:
            logger.debug(f"[{self.name}] called call_tool: {name}")

            # Inject trace context into meta for propagation to server.
            # SDK v2: request `_meta` is `RequestParamsMeta` (a TypedDict), not
            # the old `RequestParams.Meta` nested model.
            propagated_meta = inject_trace_context(meta)
            request_meta = cast(
                "mcp_types.RequestParamsMeta | None",
                propagated_meta if propagated_meta else None,
            )

            read_timeout_seconds = normalize_timeout_to_seconds(timeout)
            progress_callback = progress_handler or self._progress_handler

            # Only opt into claimed results (SEP-2133) when this client registered
            # an extension that claims one; otherwise keep the SDK's default, which
            # surfaces an unexpected claimed result as an error rather than parsing
            # a shape we have no resolver for.
            has_claims = bool(self._claim_by_model)

            async def _retry(
                input_responses: mcp_types.InputResponses | None,
                request_state: str | None,
            ) -> (
                mcp_types.CallToolResult
                | mcp_types.InputRequiredResult
                | mcp_types.Result
            ):
                return await self.session.call_tool(
                    name=name,
                    arguments=arguments,
                    read_timeout_seconds=read_timeout_seconds,
                    progress_callback=progress_callback,
                    meta=request_meta,
                    input_responses=input_responses,
                    request_state=request_state,
                    allow_input_required=True,
                    allow_claimed=has_claims,
                )

            first = await self._await_with_session_monitoring(_retry(None, None))
            driven = await self._await_with_session_monitoring(
                self._drive_input_required(first, _retry)
            )
            if isinstance(driven, mcp_types.CallToolResult):
                result = driven
            else:
                # A claimed extension result (SEP-2133): resolve it through the
                # owning extension's resolver into an ordinary CallToolResult.
                # Resolution issues further session requests of its own (result
                # validation lists tools; a resolver may make more), so it needs
                # the same session monitoring as the calls above — otherwise a
                # transport-level failure can kill the session runner while this
                # await waits forever.
                result = await self._await_with_session_monitoring(
                    self._resolve_claimed_result(name, driven, read_timeout_seconds)
                )

            # Reflect tool-level errors on the span so callers see ERROR
            # status even though the MCP protocol call itself succeeded.
            if result.is_error and span.is_recording():
                span.set_attribute("error.type", "tool_error")
                description = ""
                if result.content and isinstance(
                    result.content[0], mcp_types.TextContent
                ):
                    description = result.content[0].text
                span.set_status(Status(StatusCode.ERROR, description))

            return result

    async def _parse_call_tool_result(
        self: Client,
        name: str,
        result: mcp_types.CallToolResult,
        raise_on_error: bool = False,
    ) -> CallToolResult:
        """Parse an mcp_types.CallToolResult into our CallToolResult dataclass.

        Args:
            name: Tool name (for schema lookup)
            result: Raw MCP protocol result
            raise_on_error: Whether to raise ToolError on errors

        Returns:
            CallToolResult: Parsed result with structured data
        """

        return await _parse_call_tool_result(
            name=name,
            result=result,
            tool_output_schemas=self.session._tool_output_schemas,
            list_tools_fn=self.session.list_tools,
            client_name=self.name,
            raise_on_error=raise_on_error,
        )

    async def call_tool(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Call a tool on the server.

        Unlike call_tool_mcp, this method raises a ToolError if the tool call results in an error.

        Args:
            name (str): The name of the tool to call.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the tool. Defaults to None.
            version (str | None, optional): Specific tool version to call. If None, calls highest version.
            timeout (datetime.timedelta | float | int | None, optional): The timeout for the tool call. Defaults to None.
            progress_handler (ProgressHandler | None, optional): The progress handler to use for the tool call. Defaults to None.
            raise_on_error (bool, optional): Whether to raise an exception if the tool call results in an error. Defaults to True.
            meta (dict[str, Any] | None, optional): Additional metadata to include with the request.
                This is useful for passing contextual information (like user IDs, trace IDs, or preferences)
                that shouldn't be tool arguments but may influence server-side processing. The server
                can access this via `context.request_context.meta`. Defaults to None.

        Returns:
            CallToolResult: The content returned by the tool. If the tool returns
                structured outputs, they are returned as a dataclass (if an output
                schema is available) or a dictionary; otherwise, a list of content
                blocks is returned. Note: to receive both structured and
                unstructured outputs, use call_tool_mcp instead and access the
                raw result object.

        Raises:
            ToolError: If the tool call results in an error.
            MCPError: If the tool call request results in a TimeoutError | JSONRPCError
            RuntimeError: If called while the client is not connected.
        """
        # Merge version into request-level meta (not arguments)
        request_meta = dict(meta) if meta else {}
        if version is not None:
            request_meta["fastmcp"] = {
                **request_meta.get("fastmcp", {}),
                "version": version,
            }

        result = await self.call_tool_mcp(
            name=name,
            arguments=arguments or {},
            timeout=timeout,
            progress_handler=progress_handler,
            meta=request_meta or None,
        )
        return await self._parse_call_tool_result(
            name, result, raise_on_error=raise_on_error
        )


async def _parse_call_tool_result(
    name: str,
    result: mcp_types.CallToolResult,
    tool_output_schemas: dict[str, dict[str, Any] | None],
    list_tools_fn: Any,  # Callable[[], Awaitable[None]]
    client_name: str | None = None,
    raise_on_error: bool = False,
) -> CallToolResult:
    """Parse an mcp_types.CallToolResult into our CallToolResult dataclass.

    Args:
        name: Tool name (for schema lookup)
        result: Raw MCP protocol result
        tool_output_schemas: Dictionary mapping tool names to their output schemas
        list_tools_fn: Async function to refresh tool schemas if needed
        client_name: Optional client name for logging
        raise_on_error: Whether to raise ToolError on errors

    Returns:
        CallToolResult: Parsed result with structured data
    """
    # Local import: CallToolResult is under TYPE_CHECKING at module level to
    # avoid a circular import (client.client -> mixins.tools -> client.client),
    # but we need the concrete class here to construct the return value.
    from fastmcp.client.client import CallToolResult

    data = None
    if result.is_error and raise_on_error:
        if result.content and isinstance(result.content[0], mcp_types.TextContent):
            msg = result.content[0].text
        else:
            msg = f"Tool '{name}' returned an error"
        raise ToolError(msg)
    elif result.structured_content and not result.is_error:
        try:
            raw_fastmcp_meta = (result.meta or {}).get("fastmcp")
            fastmcp_meta = (
                raw_fastmcp_meta if isinstance(raw_fastmcp_meta, dict) else {}
            )
            wrap_from_meta = fastmcp_meta.get("wrap_result", False)

            # Ensure the schema cache is populated for type validation.
            # When meta tells us the result is wrapped we can skip the
            # schema check for *wrap detection*, but we still need the
            # schema for proper type coercion (e.g. list → set, str → datetime).
            if name not in tool_output_schemas:
                await list_tools_fn()

            if wrap_from_meta:
                # Meta tells us the result is wrapped — unwrap and validate.
                structured_content = result.structured_content.get("result")
            elif name in tool_output_schemas:
                output_schema = tool_output_schemas.get(name)
                if output_schema and output_schema.get("x-fastmcp-wrap-result"):
                    structured_content = result.structured_content.get("result")
                else:
                    structured_content = result.structured_content
            else:
                structured_content = result.structured_content

            # Type-validate through the schema if available.
            output_schema = tool_output_schemas.get(name)
            if output_schema:
                if wrap_from_meta or output_schema.get("x-fastmcp-wrap-result"):
                    output_schema = output_schema.get("properties", {}).get(
                        "result", output_schema
                    )
                output_type = json_schema_to_type(output_schema)
                type_adapter = get_cached_typeadapter(output_type)
                data = type_adapter.validate_python(structured_content)
            else:
                data = structured_content
        except Exception as e:
            logger.error(
                f"[{client_name or 'client'}] Error parsing structured content: {e}"
            )

    return CallToolResult(
        content=result.content,
        structured_content=result.structured_content,
        meta=result.meta,
        data=data,
        is_error=result.is_error,
    )
