"""Resource-related methods for FastMCP Client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp.client.caching import CacheMode
from pydantic import AnyUrl

if TYPE_CHECKING:
    from fastmcp.client.client import Client

from fastmcp.client.telemetry import client_span
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


class ClientResourcesMixin:
    """Mixin providing resource-related methods for Client."""

    # --- Resources ---

    async def list_resources_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> mcp_types.ListResourcesResult:
        """Send a resources/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.
            cache_mode: Response-cache behavior (only active with a cache and a modern
                connection). See `list_tools_mcp`.

        Returns:
            mcp_types.ListResourcesResult: The complete response object from the protocol,
                containing the list of resources and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "resources/list",
            "resources/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called list_resources")

            params = (
                mcp_types.PaginatedRequestParams(cursor=cursor)
                if cursor is not None
                else None
            )

            async def _send() -> mcp_types.ListResourcesResult:
                return await self._await_with_session_monitoring(
                    self.session.list_resources(params=params)
                )

            return await self._cached_fetch(
                "resources/list", cursor=cursor, cache_mode=cache_mode, send=_send
            )

    async def list_resources(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
    ) -> list[mcp_types.Resource]:
        """Retrieve all resources available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_resources_mcp() with the cursor parameter.

        Args:
            max_pages: Maximum number of pages to fetch before raising. Defaults to 250.

        Returns:
            list[mcp_types.Resource]: A list of all Resource objects.

        Raises:
            RuntimeError: If the page limit is reached before pagination completes.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        all_resources: list[mcp_types.Resource] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_resources_mcp(cursor=cursor)
            all_resources.extend(result.resources)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    f"[{self.name}] Server returned duplicate pagination cursor"
                    f" {result.next_cursor!r} for list_resources; stopping pagination"
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit"
                f" ({max_pages} pages) for list_resources."
                " Use list_resources_mcp() with cursor for manual pagination,"
                " or increase max_pages."
            )

        return all_resources

    async def list_resource_templates_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> mcp_types.ListResourceTemplatesResult:
        """Send a resources/listResourceTemplates request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.
            cache_mode: Response-cache behavior (only active with a cache and a modern
                connection). See `list_tools_mcp`.

        Returns:
            mcp_types.ListResourceTemplatesResult: The complete response object from the protocol,
                containing the list of resource templates and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        with client_span(
            "resources/templates/list",
            "resources/templates/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            logger.debug(f"[{self.name}] called list_resource_templates")

            params = (
                mcp_types.PaginatedRequestParams(cursor=cursor)
                if cursor is not None
                else None
            )

            async def _send() -> mcp_types.ListResourceTemplatesResult:
                return await self._await_with_session_monitoring(
                    self.session.list_resource_templates(params=params)
                )

            return await self._cached_fetch(
                "resources/templates/list",
                cursor=cursor,
                cache_mode=cache_mode,
                send=_send,
            )

    async def list_resource_templates(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
    ) -> list[mcp_types.ResourceTemplate]:
        """Retrieve all resource templates available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_resource_templates_mcp() with the
        cursor parameter.

        Args:
            max_pages: Maximum number of pages to fetch before raising. Defaults to 250.

        Returns:
            list[mcp_types.ResourceTemplate]: A list of all ResourceTemplate objects.

        Raises:
            RuntimeError: If the page limit is reached before pagination completes.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        all_templates: list[mcp_types.ResourceTemplate] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_resource_templates_mcp(cursor=cursor)
            all_templates.extend(result.resource_templates)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    f"[{self.name}] Server returned duplicate pagination cursor"
                    f" {result.next_cursor!r} for list_resource_templates;"
                    " stopping pagination"
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit"
                f" ({max_pages} pages) for list_resource_templates."
                " Use list_resource_templates_mcp() with cursor for manual pagination,"
                " or increase max_pages."
            )

        return all_templates

    async def read_resource_mcp(
        self: Client, uri: AnyUrl | str, meta: dict[str, Any] | None = None
    ) -> mcp_types.ReadResourceResult:
        """Send a resources/read request and return the complete MCP protocol result.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp_types.ReadResourceResult: The complete response object from the protocol,
                containing the resource contents and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            MCPError: If the request results in a TimeoutError | JSONRPCError
        """
        # SDK v2: the wire `uri` is a plain string, but resources are stored
        # under the AnyUrl-normalized form (e.g. a trailing slash for authority
        # URIs), so normalize through AnyUrl to keep server-side lookups aligned.
        uri_str = str(AnyUrl(uri)) if isinstance(uri, str) else str(uri)
        with client_span(
            "resources/read",
            "resources/read",
            uri_str,
            session_id=self.transport.get_session_id(),
            resource_uri=uri_str,
        ):
            logger.debug(f"[{self.name}] called read_resource: {uri}")

            # Inject trace context into meta for propagation to server
            propagated_meta = inject_trace_context(meta)
            request_meta = cast("mcp_types.RequestParamsMeta | None", propagated_meta)

            async def _retry(
                input_responses: mcp_types.InputResponses | None,
                request_state: str | None,
            ) -> mcp_types.ReadResourceResult | mcp_types.InputRequiredResult:
                return await self.session.read_resource(
                    uri_str,
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

    async def read_resource(
        self: Client,
        uri: AnyUrl | str,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> list[mcp_types.TextResourceContents | mcp_types.BlobResourceContents]:
        """Read the contents of a resource or resolved template.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            version (str | None): Specific version to read. If None, reads highest version.
            meta (dict[str, Any] | None): Optional request-level metadata.

        Returns:
            list[mcp_types.TextResourceContents | mcp_types.BlobResourceContents]:
                A list of content objects.

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

        if isinstance(uri, str):
            try:
                uri = AnyUrl(uri)  # Ensure AnyUrl
            except Exception as e:
                raise ValueError(
                    f"Provided resource URI is invalid: {str(uri)!r}"
                ) from e
        result = await self.read_resource_mcp(uri, meta=request_meta or None)
        return result.contents
