"""Prompt-related methods for FastMCP Client."""

from __future__ import annotations

import uuid
import weakref
from typing import TYPE_CHECKING, Any, Literal, overload

import mcp.types
import pydantic_core
from pydantic import RootModel

if TYPE_CHECKING:
    from fastmcp.client.client import Client

from fastmcp.client.tasks import PromptTask
from fastmcp.client.telemetry import client_span
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# Type alias for task response union (SEP-1686 graceful degradation)
PromptTaskResponseUnion = RootModel[
    mcp.types.CreateTaskResult | mcp.types.GetPromptResult
]


class ClientPromptsMixin:
    """Mixin providing prompt-related methods for Client."""

    # --- Prompts ---

    async def list_prompts_mcp(
        self: Client, *, cursor: str | None = None
    ) -> mcp.types.ListPromptsResult:
        """Send a prompts/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.

        Returns:
            mcp.types.ListPromptsResult: The complete response object from the protocol,
                containing the list of prompts and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called list_prompts")

        result = await self._await_with_session_monitoring(
            self.session.list_prompts(cursor=cursor)
        )
        return result

    async def list_prompts(self: Client) -> list[mcp.types.Prompt]:
        """Retrieve all prompts available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_prompts_mcp() with the cursor parameter.

        Returns:
            list[mcp.types.Prompt]: A list of all Prompt objects.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        all_prompts: list[mcp.types.Prompt] = []
        cursor: str | None = None

        while True:
            result = await self.list_prompts_mcp(cursor=cursor)
            all_prompts.extend(result.prompts)
            if result.nextCursor is None:
                break
            cursor = result.nextCursor

        return all_prompts

    # --- Prompt ---
    async def get_prompt_mcp(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp.types.GetPromptResult:
        """Send a prompts/get request and return the complete MCP protocol result.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp.types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            f"prompts/get {name}",
            "prompts/get",
            name,
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called get_prompt: {name}")

            # Serialize arguments for MCP protocol - convert non-string values to JSON
            serialized_arguments: dict[str, str] | None = None
            if arguments:
                serialized_arguments = {}
                for key, value in arguments.items():
                    if isinstance(value, str):
                        serialized_arguments[key] = value
                    else:
                        # Use pydantic_core.to_json for consistent serialization
                        serialized_arguments[key] = pydantic_core.to_json(value).decode(
                            "utf-8"
                        )

            # Inject trace context into meta for propagation to server
            propagated_meta = inject_trace_context(meta)

            # If meta provided, use send_request for SEP-1686 task support
            if propagated_meta:
                task_dict = propagated_meta.get("modelcontextprotocol.io/task")
                request = mcp.types.GetPromptRequest(
                    params=mcp.types.GetPromptRequestParams(
                        name=name,
                        arguments=serialized_arguments,
                        task=mcp.types.TaskMetadata(**task_dict) if task_dict else None,
                        _meta=propagated_meta,  # type: ignore[unknown-argument]  # pydantic alias
                    )
                )
                result = await self._await_with_session_monitoring(
                    self.session.send_request(
                        request=request,  # type: ignore[arg-type]
                        result_type=mcp.types.GetPromptResult,
                    )
                )
            else:
                result = await self._await_with_session_monitoring(
                    self.session.get_prompt(name=name, arguments=serialized_arguments)
                )
            return result

    @overload
    async def get_prompt(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: Literal[False] = False,
    ) -> mcp.types.GetPromptResult: ...

    @overload
    async def get_prompt(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> PromptTask: ...

    async def get_prompt(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> mcp.types.GetPromptResult | PromptTask:
        """Retrieve a rendered prompt message list from the server.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            version (str | None, optional): Specific prompt version to get. If None, gets highest version.
            meta (dict[str, Any] | None): Optional request-level metadata.
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            mcp.types.GetPromptResult | PromptTask: The complete response object if task=False,
                or a PromptTask object if task=True.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        # Merge version into request-level meta (not arguments)
        request_meta = dict(meta) if meta else {}
        if version is not None:
            request_meta["fastmcp"] = {
                **request_meta.get("fastmcp", {}),
                "version": version,
            }

        if task:
            return await self._get_prompt_as_task(
                name, arguments, task_id, ttl, meta=request_meta or None
            )

        result = await self.get_prompt_mcp(
            name=name, arguments=arguments, meta=request_meta or None
        )
        return result

    async def _get_prompt_as_task(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str | None = None,
        ttl: int = 60000,
        meta: dict[str, Any] | None = None,
    ) -> PromptTask:
        """Get a prompt for background execution (SEP-1686).

        Returns a PromptTask object that handles both background and immediate execution.

        Args:
            name: Prompt name to get
            arguments: Prompt arguments
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)
            meta: Optional request metadata (e.g., version info)

        Returns:
            PromptTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Inject trace context into meta for propagation to server
        propagated_meta = inject_trace_context(meta)

        # Serialize arguments for MCP protocol
        serialized_arguments: dict[str, str] | None = None
        if arguments:
            serialized_arguments = {}
            for key, value in arguments.items():
                if isinstance(value, str):
                    serialized_arguments[key] = value
                else:
                    serialized_arguments[key] = pydantic_core.to_json(value).decode(
                        "utf-8"
                    )

        request = mcp.types.GetPromptRequest(
            params=mcp.types.GetPromptRequestParams(
                name=name,
                arguments=serialized_arguments,
                task=mcp.types.TaskMetadata(ttl=ttl),
                _meta=propagated_meta,  # type: ignore[unknown-argument]  # pydantic alias
            )
        )

        # Server returns CreateTaskResult (task accepted) or GetPromptResult (graceful degradation)
        wrapped_result = await self._await_with_session_monitoring(
            self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=PromptTaskResponseUnion,
            )
        )
        raw_result = wrapped_result.root

        if isinstance(raw_result, mcp.types.CreateTaskResult):
            # Task was accepted - extract task info from CreateTaskResult
            server_task_id = raw_result.task.taskId
            self._submitted_task_ids.add(server_task_id)

            task_obj = PromptTask(
                self, server_task_id, prompt_name=name, immediate_result=None
            )
            self._task_registry[server_task_id] = weakref.ref(task_obj)
            return task_obj
        else:
            # Graceful degradation - server returned GetPromptResult
            synthetic_task_id = task_id or str(uuid.uuid4())
            return PromptTask(
                self, synthetic_task_id, prompt_name=name, immediate_result=raw_result
            )
