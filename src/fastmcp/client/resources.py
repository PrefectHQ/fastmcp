"""Resource-related methods for FastMCP Client."""

from __future__ import annotations

import uuid
import weakref
from typing import TYPE_CHECKING, Any, Literal, overload

import mcp.types
from pydantic import AnyUrl, RootModel

if TYPE_CHECKING:
    from fastmcp.client.client import Client

from fastmcp.client.tasks import ResourceTask
from fastmcp.client.telemetry import client_span
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# Type alias for task response union (SEP-1686 graceful degradation)
ResourceTaskResponseUnion = RootModel[
    mcp.types.CreateTaskResult | mcp.types.ReadResourceResult
]


class ClientResourcesMixin:
    """Mixin providing resource-related methods for Client."""

    # --- Resources ---

    async def list_resources_mcp(
        self: Client, *, cursor: str | None = None
    ) -> mcp.types.ListResourcesResult:
        """Send a resources/list request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.

        Returns:
            mcp.types.ListResourcesResult: The complete response object from the protocol,
                containing the list of resources and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called list_resources")

        result = await self._await_with_session_monitoring(
            self.session.list_resources(cursor=cursor)
        )
        return result

    async def list_resources(self: Client) -> list[mcp.types.Resource]:
        """Retrieve all resources available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_resources_mcp() with the cursor parameter.

        Returns:
            list[mcp.types.Resource]: A list of all Resource objects.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        all_resources: list[mcp.types.Resource] = []
        cursor: str | None = None

        while True:
            result = await self.list_resources_mcp(cursor=cursor)
            all_resources.extend(result.resources)
            if result.nextCursor is None:
                break
            cursor = result.nextCursor

        return all_resources

    async def list_resource_templates_mcp(
        self: Client, *, cursor: str | None = None
    ) -> mcp.types.ListResourceTemplatesResult:
        """Send a resources/listResourceTemplates request and return the complete MCP protocol result.

        Args:
            cursor: Optional pagination cursor from a previous request's nextCursor.

        Returns:
            mcp.types.ListResourceTemplatesResult: The complete response object from the protocol,
                containing the list of resource templates and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        logger.debug(f"[{self.name}] called list_resource_templates")

        result = await self._await_with_session_monitoring(
            self.session.list_resource_templates(cursor=cursor)
        )
        return result

    async def list_resource_templates(self: Client) -> list[mcp.types.ResourceTemplate]:
        """Retrieve all resource templates available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_resource_templates_mcp() with the
        cursor parameter.

        Returns:
            list[mcp.types.ResourceTemplate]: A list of all ResourceTemplate objects.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        all_templates: list[mcp.types.ResourceTemplate] = []
        cursor: str | None = None

        while True:
            result = await self.list_resource_templates_mcp(cursor=cursor)
            all_templates.extend(result.resourceTemplates)
            if result.nextCursor is None:
                break
            cursor = result.nextCursor

        return all_templates

    async def read_resource_mcp(
        self: Client, uri: AnyUrl | str, meta: dict[str, Any] | None = None
    ) -> mcp.types.ReadResourceResult:
        """Send a resources/read request and return the complete MCP protocol result.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            meta (dict[str, Any] | None, optional): Request metadata (e.g., for SEP-1686 tasks). Defaults to None.

        Returns:
            mcp.types.ReadResourceResult: The complete response object from the protocol,
                containing the resource contents and any additional metadata.

        Raises:
            RuntimeError: If called while the client is not connected.
            McpError: If the request results in a TimeoutError | JSONRPCError
        """
        uri_str = str(uri)
        with client_span(
            f"resources/read {uri_str}",
            "resources/read",
            uri_str,
            session_id=self.transport.get_session_id(),
            resource_uri=uri_str,
        ):
            logger.debug(f"[{self.name}] called read_resource: {uri}")

            if isinstance(uri, str):
                uri = AnyUrl(uri)  # Ensure AnyUrl

            # Inject trace context into meta for propagation to server
            propagated_meta = inject_trace_context(meta)

            # If meta provided, use send_request for SEP-1686 task support
            if propagated_meta:
                task_dict = propagated_meta.get("modelcontextprotocol.io/task")
                request = mcp.types.ReadResourceRequest(
                    params=mcp.types.ReadResourceRequestParams(
                        uri=uri,
                        task=mcp.types.TaskMetadata(**task_dict) if task_dict else None,
                        _meta=propagated_meta,  # type: ignore[unknown-argument]  # pydantic alias
                    )
                )
                result = await self._await_with_session_monitoring(
                    self.session.send_request(
                        request=request,  # type: ignore[arg-type]
                        result_type=mcp.types.ReadResourceResult,
                    )
                )
            else:
                result = await self._await_with_session_monitoring(
                    self.session.read_resource(uri)
                )
            return result

    @overload
    async def read_resource(
        self: Client,
        uri: AnyUrl | str,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: Literal[False] = False,
    ) -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]: ...

    @overload
    async def read_resource(
        self: Client,
        uri: AnyUrl | str,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ResourceTask: ...

    async def read_resource(
        self: Client,
        uri: AnyUrl | str,
        *,
        version: str | None = None,
        meta: dict[str, Any] | None = None,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> (
        list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]
        | ResourceTask
    ):
        """Read the contents of a resource or resolved template.

        Args:
            uri (AnyUrl | str): The URI of the resource to read. Can be a string or an AnyUrl object.
            version (str | None): Specific version to read. If None, reads highest version.
            meta (dict[str, Any] | None): Optional request-level metadata.
            task (bool): If True, execute as background task (SEP-1686). Defaults to False.
            task_id (str | None): Optional client-provided task ID (auto-generated if not provided).
            ttl (int): Time to keep results available in milliseconds (default 60s).

        Returns:
            list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents] | ResourceTask:
                A list of content objects if task=False, or a ResourceTask object if task=True.

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
            return await self._read_resource_as_task(
                uri, task_id, ttl, meta=request_meta or None
            )

        if isinstance(uri, str):
            try:
                uri = AnyUrl(uri)  # Ensure AnyUrl
            except Exception as e:
                raise ValueError(
                    f"Provided resource URI is invalid: {str(uri)!r}"
                ) from e
        result = await self.read_resource_mcp(uri, meta=request_meta or None)
        return result.contents

    async def _read_resource_as_task(
        self: Client,
        uri: AnyUrl | str,
        task_id: str | None = None,
        ttl: int = 60000,
        meta: dict[str, Any] | None = None,
    ) -> ResourceTask:
        """Read a resource for background execution (SEP-1686).

        Returns a ResourceTask object that handles both background and immediate execution.

        Args:
            uri: Resource URI to read
            task_id: Optional client-provided task ID (ignored, for backward compatibility)
            ttl: Time to keep results available in milliseconds (default 60s)
            meta: Optional metadata to pass with the request (e.g., version info)

        Returns:
            ResourceTask: Future-like object for accessing task status and results
        """
        # Per SEP-1686 final spec: client sends only ttl, server generates taskId
        # Inject trace context into meta for propagation to server
        propagated_meta = inject_trace_context(meta)

        if isinstance(uri, str):
            uri = AnyUrl(uri)

        request = mcp.types.ReadResourceRequest(
            params=mcp.types.ReadResourceRequestParams(
                uri=uri,
                task=mcp.types.TaskMetadata(ttl=ttl),
                _meta=propagated_meta,  # type: ignore[unknown-argument]  # pydantic alias
            )
        )

        # Server returns CreateTaskResult (task accepted) or ReadResourceResult (graceful degradation)
        wrapped_result = await self._await_with_session_monitoring(
            self.session.send_request(
                request=request,  # type: ignore[arg-type]
                result_type=ResourceTaskResponseUnion,
            )
        )
        raw_result = wrapped_result.root

        if isinstance(raw_result, mcp.types.CreateTaskResult):
            # Task was accepted - extract task info from CreateTaskResult
            server_task_id = raw_result.task.taskId
            self._submitted_task_ids.add(server_task_id)

            task_obj = ResourceTask(
                self, server_task_id, uri=str(uri), immediate_result=None
            )
            self._task_registry[server_task_id] = weakref.ref(task_obj)
            return task_obj
        else:
            # Graceful degradation - server returned ReadResourceResult
            synthetic_task_id = task_id or str(uuid.uuid4())
            return ResourceTask(
                self,
                synthetic_task_id,
                uri=str(uri),
                immediate_result=raw_result.contents,
            )
