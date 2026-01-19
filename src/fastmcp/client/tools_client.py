"""Tool-related methods for FastMCP Client."""

from __future__ import annotations

import uuid
import weakref
from typing import TYPE_CHECKING, Any, Literal, overload

import mcp.types
from pydantic import RootModel

if TYPE_CHECKING:
    import datetime

    from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.tasks import ToolTask
from fastmcp.client.telemetry import client_span
from fastmcp.exceptions import ToolError
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.json_schema_type import json_schema_to_type
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.timeout import normalize_timeout_to_timedelta
from fastmcp.utilities.types import get_cached_typeadapter

logger = get_logger(__name__)

# Type alias for task response union (SEP-1686 graceful degradation)
ToolTaskResponseUnion = RootModel[mcp.types.CreateTaskResult | mcp.types.CallToolResult]


class ClientToolsMixin:
    """Mixin providing tool-related methods for Client."""

    # --- Tools ---

    async def list_tools_mcp(
        self: Client, *, cursor: str | None = None
    ) -> mcp.types.ListToolsResult:
        """Send a tools/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.

        Returns:
            mcp.types.ListToolsResult: The complete response object from the protocol,
                containing the list of tools and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called list_tools")

        result = await self._await_with_session_monitoring(
            self.session.list_tools(cursor=cursor)
        )
        return result

    async def list_tools(self: Client) -> list[mcp.types.Tool]:
        """Retrieve all tools available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_tools_mcp() with the cursor parameter.

        Returns:
            list[mcp.types.Tool]: A list of all Tool objects.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        all_tools: list[mcp.types.Tool] = []
        cursor: str | None = None

        while True:
            result = await self.list_tools_mcp(cursor=cursor)
            all_tools.extend(result.tools)
            if result.nextCursor is None:
                break
            cursor = result.nextCursor

        return all_tools

    # --- Call Tool ---

    async def call_tool_mcp(
        self: Client,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp.types.CallToolResult:
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
            mcp.types.CallToolResult: The complete response object from the protocol,
                containing the tool result and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the tool call requests results in a TimeoutError | JSONRPCError
        """
        with client_span(
            f"tools/call {name}",
            "tools/call",
            name,
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called call_tool: {name}")

            # Inject trace context into meta for propagation to server
            propagated_meta = inject_trace_context(meta)

            result = await self._await_with_session_monitoring(
                self.session.call_tool(
                    name=name,
                    arguments=arguments,
                    read_timeout_seconds=normalize_timeout_to_timedelta(timeout),
                    progress_callback=progress_handler or self._progress_handler,
                    meta=propagated_meta if propagated_meta else None,
                )
            )
            return result

    async def _parse_call_tool_result(
        self: Client,
        name: str,
        result: mcp.types.CallToolResult,
        raise_on_error: bool = False,
    ) -> CallToolResult:
        """Parse an mcp.types.CallToolResult into our CallToolResult dataclass.

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

    @overload
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
        task: Literal[False] = False,
    ) -> CallToolResult: ...

    @overload
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
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ToolTask: ...

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
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> CallToolResult | ToolTask:
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
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            CallToolResult | ToolTask: The content returned by the tool if task=False,
                or a ToolTask object if task=True. If the tool returns structured
                outputs, they are returned as a dataclass (if an output schema
                is available) or a dictionary; otherwise, a list of content
                blocks is returned. Note: to receive both structured and
                unstructured outputs, use call_tool_mcp instead and access the
                raw result object.

        Raises:
            ToolError: If the tool call results in an error.
            McpError: If the tool call request results in a TimeoutError | JSONRPCError
            RuntimeError: If called while the client is not connected.
        """
        # Merge version into request-level meta (not arguments)
        request_meta = dict(meta) if meta else {}
        if version is not None:
            request_meta["fastmcp"] = {
                **request_meta.get("fastmcp", {}),
                "version": version,
            }

        if task:
            return await self._call_tool_as_task(
                name, arguments, task_id, ttl, meta=request_meta or None
            )

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

    async def _call_tool_as_task(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str | None = None,
        ttl: int = 60000,
        meta: dict[str, Any] | None = None,
    ) -> ToolTask:
        """Call a tool for background execution (SEP-1686).

        Returns a ToolTask object that handles both background and immediate execution.
        If the server accepts background execution, ToolTask will poll for results.
        If the server declines (graceful degradation), ToolTask wraps the immediate result.

        Args:
            name: Tool name to call
            arguments: Tool arguments
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)
            meta: Optional request metadata (e.g., version info)

        Returns:
            ToolTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Inject trace context into meta for propagation to server
        propagated_meta = inject_trace_context(meta)

        # Build request with task metadata
        request = mcp.types.CallToolRequest(
            params=mcp.types.CallToolRequestParams(
                name=name,
                arguments=arguments or {},
                task=mcp.types.TaskMetadata(ttl=ttl),
                _meta=propagated_meta,  # type: ignore[unknown-argument]  # pydantic alias
            )
        )

        # Server returns CreateTaskResult (task accepted) or CallToolResult (graceful degradation)
        # Use RootModel with Union to handle both response types (SDK calls model_validate)
        wrapped_result = await self._await_with_session_monitoring(
            self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=ToolTaskResponseUnion,
            )
        )
        raw_result = wrapped_result.root

        if isinstance(raw_result, mcp.types.CreateTaskResult):
            # Task was accepted - extract task info from CreateTaskResult
            server_task_id = raw_result.task.taskId
            self._submitted_task_ids.add(server_task_id)

            task_obj = ToolTask(
                self, server_task_id, tool_name=name, immediate_result=None
            )
            self._task_registry[server_task_id] = weakref.ref(task_obj)
            return task_obj
        else:
            # Graceful degradation - server returned CallToolResult
            parsed_result = await self._parse_call_tool_result(name, raw_result)
            synthetic_task_id = task_id or str(uuid.uuid4())
            return ToolTask(
                self,
                synthetic_task_id,
                tool_name=name,
                immediate_result=parsed_result,
            )


async def _parse_call_tool_result(
    name: str,
    result: mcp.types.CallToolResult,
    tool_output_schemas: dict[str, dict[str, Any] | None],
    list_tools_fn: Any,  # Callable[[], Awaitable[None]]
    client_name: str | None = None,
    raise_on_error: bool = False,
) -> CallToolResult:
    """Parse an mcp.types.CallToolResult into our CallToolResult dataclass.

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
    from typing import cast

    from fastmcp.client.client import CallToolResult

    data = None
    if result.isError and raise_on_error:
        msg = cast(mcp.types.TextContent, result.content[0]).text
        raise ToolError(msg)
    elif result.structuredContent:
        try:
            if name not in tool_output_schemas:
                await list_tools_fn()
            if name in tool_output_schemas:
                output_schema = tool_output_schemas.get(name)
                if output_schema:
                    if output_schema.get("x-fastmcp-wrap-result"):
                        output_schema = output_schema.get("properties", {}).get(
                            "result"
                        )
                        structured_content = result.structuredContent.get("result")
                    else:
                        structured_content = result.structuredContent
                    output_type = json_schema_to_type(output_schema)
                    type_adapter = get_cached_typeadapter(output_type)
                    data = type_adapter.validate_python(structured_content)
                else:
                    data = result.structuredContent
        except Exception as e:
            logger.error(
                f"[{client_name or 'client'}] Error parsing structured content: {e}"
            )

    return CallToolResult(
        content=result.content,
        structured_content=result.structuredContent,
        meta=result.meta,
        data=data,
        is_error=result.isError,
    )
