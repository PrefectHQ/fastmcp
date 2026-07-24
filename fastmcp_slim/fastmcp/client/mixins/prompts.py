"""Prompt-related methods for FastMCP Client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import mcp_types
import pydantic_core
from mcp.client.caching import CacheMode

if TYPE_CHECKING:
    from fastmcp.client.client import Client

from fastmcp.client.telemetry import client_span
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


class ClientPromptsMixin:
    """Mixin providing prompt-related methods for Client."""

    # --- Prompts ---

    async def list_prompts_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> mcp_types.ListPromptsResult:
        """Send a prompts/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.
            cache_mode: Response-cache behavior (only active with a cache and a modern
                connection). See `list_tools_mcp`.

        Returns:
            mcp_types.ListPromptsResult: The complete response object from the protocol,
                containing the list of prompts and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "prompts/list",
            "prompts/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called list_prompts")

            params = (
                mcp_types.PaginatedRequestParams(cursor=cursor)
                if cursor is not None
                else None
            )

            async def _send() -> mcp_types.ListPromptsResult:
                return await self._await_with_session_monitoring(
                    self.session.list_prompts(params=params)
                )

            return await self._cached_fetch(
                "prompts/list", cursor=cursor, cache_mode=cache_mode, send=_send
            )

    async def list_prompts(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
    ) -> list[mcp_types.Prompt]:
        """Retrieve all prompts available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_prompts_mcp() with the cursor parameter.

        Args:
            max_pages: Maximum number of pages to fetch before raising. Defaults to 250.

        Returns:
            list[mcp_types.Prompt]: A list of all Prompt objects.

        Raises:
            RuntimeError: If the page limit is reached before pagination completes.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        all_prompts: list[mcp_types.Prompt] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_prompts_mcp(cursor=cursor)
            all_prompts.extend(result.prompts)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    f"[{self.name}] Server returned duplicate pagination cursor"
                    f" {result.next_cursor!r} for list_prompts; stopping pagination"
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit"
                f" ({max_pages} pages) for list_prompts."
                " Use list_prompts_mcp() with cursor for manual pagination,"
                " or increase max_pages."
            )

        return all_prompts

    # --- Prompt ---
    async def get_prompt_mcp(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp_types.GetPromptResult:
        """Send a prompts/get request and return the complete MCP protocol result.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp_types.GetPromptResult: The complete response object from the protocol,
                containing the prompt messages and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            f"prompts/get {name}",
            "prompts/get",
            name,
            session_id=self.transport.get_session_id(),
            prompt_name=name,
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
            request_meta = cast("mcp_types.RequestParamsMeta | None", propagated_meta)

            async def _retry(
                input_responses: mcp_types.InputResponses | None,
                request_state: str | None,
            ) -> mcp_types.GetPromptResult | mcp_types.InputRequiredResult:
                return await self.session.get_prompt(
                    name=name,
                    arguments=serialized_arguments,
                    meta=request_meta,
                    input_responses=input_responses,
                    request_state=request_state,
                    allow_input_required=True,
                )

            first = await self._await_with_session_monitoring(_retry(None, None))
            result = await self._await_with_session_monitoring(
                self._drive_input_required(first, _retry)
            )
            return result

    async def get_prompt(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp_types.GetPromptResult:
        """Retrieve a rendered prompt message list from the server.

        Args:
            name (str): The name of the prompt to retrieve.
            arguments (dict[str, Any] | None, optional): Arguments to pass to the prompt. Defaults to None.
            version (str | None, optional): Specific prompt version to get. If None, gets highest version.
            meta (dict[str, Any] | None): Optional request-level metadata.

        Returns:
            mcp_types.GetPromptResult: The complete response object.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        # Merge version into request-level meta (not arguments)
        request_meta = dict(meta) if meta else {}
        if version is not None:
            request_meta["fastmcp"] = {
                **request_meta.get("fastmcp", {}),
                "version": version,
            }

        result = await self.get_prompt_mcp(
            name=name, arguments=arguments, meta=request_meta or None
        )
        return result
