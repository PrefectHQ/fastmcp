"""Tool-related methods for FastMCP Client."""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import mcp_types
from mcp.client.caching import CacheMode
from opentelemetry.trace import Status, StatusCode

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult, Client
from fastmcp.client._version_pin import PinnedCall, pinned_call
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.telemetry import client_span
from fastmcp.exceptions import ToolError
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.json_schema_type import json_schema_to_type_adapter
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.timeout import normalize_timeout_to_seconds

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


def _round_trips_to_payload(
    type_adapter: Any, typed: Any, original: Any, *, output_schema: Any
) -> bool:
    """Check that the typed hydration re-serializes to the server's payload.

    Pydantic unions validate with smart mode, which may coerce across
    branches (0 → False) or drop keys the model does not declare, so the
    hydrated object can disagree with `structured_content`. Re-serializing
    through the same adapter compares them in JSON terms, with three
    hydration-legitimate asymmetries: fields the payload omitted may appear
    in the dump (schema defaults serialize out), date-time strings may
    be spelled differently for the same instant (pydantic renders UTC as
    ``Z`` while a payload may say ``+00:00``), and ``format: json`` text
    is compared as its decoded value (pydantic's ``Json`` type) — only at
    the schema positions that actually declare it.
    """
    try:
        # by_alias: schemas whose property names are pydantic-reserved
        # (``model_config``, ``_private``, ...) generate aliased fields, and
        # the wire payload uses the alias spellings — the default dump
        # re-emits the renamed field names instead.
        rehydrated = json.loads(type_adapter.dump_json(typed, by_alias=True))
    except Exception:
        # If the typed form cannot be re-serialized, fidelity is unknowable;
        # keep the hydrated value rather than second-guessing a valid model.
        return True
    return _hydration_covers_payload(
        rehydrated, original, [output_schema], output_schema
    )


def _ref_target(node: dict[str, Any], root: Any) -> Any:
    """Resolve a local ``$ref`` (``#/$defs/Name``) against the root schema."""
    ref = node.get("$ref")
    if not (isinstance(ref, str) and ref.startswith("#/")):
        return node
    target: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(target, dict):
            return node
        target = target.get(part.replace("~1", "/").replace("~0", "~"))
    return target if target is not None else node


def _schema_branches(node: Any, root: Any) -> list[Any]:
    """Schema node(s) governing one value position: local ``$ref``s resolved
    and ``anyOf``/``oneOf``/``allOf`` flattened. Recursive references are
    safe; unresolvable ones keep the original node."""
    branches: list[Any] = []
    seen: set[int] = set()
    frontier: list[Any] = [node]
    while frontier:
        current = frontier.pop()
        if isinstance(current, dict):
            current = _ref_target(current, root)
            if not isinstance(current, dict) or id(current) in seen:
                continue
            seen.add(id(current))
            flattened = False
            for keyword in ("anyOf", "oneOf", "allOf"):
                sub = current.get(keyword)
                if isinstance(sub, list):
                    frontier.extend(sub)
                    flattened = True
                    break
            if not flattened:
                branches.append(current)
        else:
            branches.append(current)
    return branches


def _node_asks_json_text(node: Any, root: Any) -> bool:
    """True when any branch of `node` declares ``format: json``."""
    return any(
        isinstance(branch, dict) and branch.get("format") == "json"
        for branch in _schema_branches(node, root)
    )


def _property_schemas(
    schemas: list[Any] | None, key: str, root: Any
) -> list[Any] | None:
    """Candidate schema nodes for `key` under each candidate node."""
    if schemas is None:
        return None
    children: list[Any] = []
    for node in schemas:
        for branch in _schema_branches(node, root):
            if not isinstance(branch, dict):
                continue
            properties = branch.get("properties")
            if isinstance(properties, dict) and key in properties:
                children.append(properties[key])
                continue
            matched_pattern = False
            pattern_properties = branch.get("patternProperties")
            if isinstance(pattern_properties, dict):
                for pattern, sub in pattern_properties.items():
                    try:
                        if re.search(pattern, key):
                            children.append(sub)
                            matched_pattern = True
                    except re.error:
                        pass
            if matched_pattern:
                continue
            additional = branch.get("additionalProperties")
            if isinstance(additional, dict):
                children.append(additional)
    return children


def _item_schemas(schemas: list[Any] | None, index: int, root: Any) -> list[Any] | None:
    """Candidate schema nodes for item `index` under each candidate node."""
    if schemas is None:
        return None
    children: list[Any] = []
    for node in schemas:
        for branch in _schema_branches(node, root):
            if not isinstance(branch, dict):
                continue
            prefix_items = branch.get("prefixItems")
            if isinstance(prefix_items, list) and index < len(prefix_items):
                children.append(prefix_items[index])
                continue
            items = branch.get("items")
            if isinstance(items, list):
                if index < len(items):
                    children.append(items[index])
            elif isinstance(items, dict):
                children.append(items)
    return children


def _json_text_decodes_to(original: Any, dumped: Any) -> bool:
    """True when `original` is JSON text whose decode equals `dumped`.

    ``format: json`` properties hydrate through pydantic's ``Json`` type:
    the server sends JSON text and the typed value is the decoded object,
    so the dump re-emits the decoded form rather than the original string.
    Callers gate this on the schema position actually declaring
    ``format: json``; without it, a JSON-looking string must not stand in
    for its decoded value.
    """
    if not isinstance(original, str):
        return False
    try:
        decoded = json.loads(original)
    except (ValueError, RecursionError):
        return False
    if isinstance(decoded, bool) != isinstance(dumped, bool):
        # 0 == False in Python; keep booleans strict.
        return False
    return decoded == dumped


def _hydration_covers_payload(
    dumped: Any, original: Any, schemas: list[Any] | None = None, root: Any = None
) -> bool:
    """Every value the server sent must survive hydration; extra keys in the
    dump are defaults for fields the payload omitted and are allowed.

    `schemas` holds the candidate output-schema nodes for THIS position and
    `root` is the schema document ``$ref``s resolve against. They gate the
    JSON-text decode asymmetry to the paths that declare ``format: json``;
    with no schema context (direct callers) the asymmetry stays open, so a
    coerced value can never slip through at a path the schema does not
    exempt."""
    if schemas and root is not None:
        node_asks = any(_node_asks_json_text(node, root) for node in schemas)
    else:
        node_asks = schemas is None
    if node_asks and _json_text_decodes_to(original, dumped):
        # Intentional decode: format: json hydrates JSON text into a value.
        return True
    if isinstance(original, bool) or isinstance(dumped, bool):
        # JSON booleans are not numbers: 0 does not hydrate to False.
        return (
            isinstance(original, bool)
            and isinstance(dumped, bool)
            and (original == dumped)
        )
    if isinstance(original, Mapping) or isinstance(dumped, Mapping):
        if not isinstance(original, Mapping) or not isinstance(dumped, Mapping):
            return False
        return all(
            key in dumped
            and _hydration_covers_payload(
                dumped[key],
                value,
                _property_schemas(schemas, str(key), root),
                root,
            )
            for key, value in original.items()
        )
    if isinstance(original, list) or isinstance(dumped, list):
        if (
            not isinstance(original, list)
            or not isinstance(dumped, list)
            or len(original) != len(dumped)
        ):
            return False
        return all(
            _hydration_covers_payload(
                d,
                o,
                _item_schemas(schemas, position, root),
                root,
            )
            for position, (o, d) in enumerate(zip(original, dumped, strict=True))
        )
    if original == dumped:
        return True
    # Same instant, different spelling: "+00:00" and "Z".
    left = _parse_iso_instant(original)
    right = _parse_iso_instant(dumped)
    if left is not None and right is not None:
        return left == right
    return False


def _parse_iso_instant(value: Any) -> Any:
    if not isinstance(value, str) or len(value) < 8:
        return None
    if re.search(r"\.\d{7,}", value):
        # Sub-microsecond precision: parsing would truncate it, so an
        # instant comparison could bless a lossy value.
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        pass
    if value.endswith("Z"):
        try:
            return datetime.datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            return None
    return None


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
        *,
        cache_mode: CacheMode = "use",
    ) -> list[mcp_types.Tool]:
        """Retrieve all tools available on the server.

        This method automatically fetches all pages if the server paginates results,
        returning the complete list. For manual pagination control (e.g., to handle
        large result sets incrementally), use list_tools_mcp() with the cursor parameter.

        Args:
            max_pages: Maximum number of pages to fetch before raising. Defaults to 250.
            cache_mode: Response-cache behavior for the first page (only active when
                the client was built with a cache and the connection is modern).
                `"use"` (default) serves and stores; `"refresh"` stores without
                serving; `"bypass"` skips the cache. Subsequent cursor pages always
                skip the cache.

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
            result = await self.list_tools_mcp(cursor=cursor, cache_mode=cache_mode)
            all_tools.extend(result.tools)
            if result.next_cursor is None:
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

            # A pinned call runs a specific version on the server, but the
            # session's output-schema cache only holds the listed version's
            # schema. Mark the call for the duration of the request so the
            # session can skip revalidation when the pinned version is a
            # different one; nothing else is affected. (#5721)
            fastmcp_meta = (meta or {}).get("fastmcp")
            pinned_version = (
                fastmcp_meta.get("version") if isinstance(fastmcp_meta, dict) else None
            )
            pin_token = (
                pinned_call.set(PinnedCall(self.session, str(pinned_version)))
                if pinned_version is not None
                else None
            )
            try:
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
            finally:
                if pin_token is not None:
                    pinned_call.reset(pin_token)

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
        pinned_version: str | None = None,
    ) -> CallToolResult:
        """Parse an mcp_types.CallToolResult into our CallToolResult dataclass.

        Args:
            name: Tool name (for schema lookup)
            result: Raw MCP protocol result
            raise_on_error: Whether to raise ToolError on errors
            pinned_version: The tool version the call requested, if any. A
                result pinned to a version other than the listed one is not
                coerced through the cached schema; see the module-level parser.

        Returns:
            CallToolResult: Parsed result with structured data
        """

        return await _parse_call_tool_result(
            name=name,
            result=result,
            tool_output_schemas=self.session._tool_output_schemas,
            # Fetch every page: a tool beyond the first page is otherwise never
            # found, and its result is left unhydrated.
            list_tools_fn=self.list_tools,
            client_name=self.name,
            raise_on_error=raise_on_error,
            pinned_version=pinned_version,
            listed_versions=getattr(self.session, "_tool_listed_versions", None),
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
            name,
            result,
            raise_on_error=raise_on_error,
            pinned_version=version,
        )


def _wrapped_result_schema(output_schema: dict[str, Any]) -> dict[str, Any] | bool:
    """Narrow a wrapped output schema to `properties.result` and keep its defs.

    The server leaves `$defs` and draft-07 `definitions` on the root schema,
    beside `properties.result`. Validating the inner schema without them makes
    every local `$ref` resolve to an empty object.
    """
    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        return output_schema
    narrowed = properties.get("result")
    if isinstance(narrowed, bool):
        return narrowed
    if not isinstance(narrowed, dict):
        return output_schema
    carried = {
        key: output_schema[key]
        for key in ("$defs", "definitions")
        if key in output_schema and key not in narrowed
    }
    if not carried:
        return narrowed
    return {**narrowed, **carried}


async def _parse_call_tool_result(
    name: str,
    result: mcp_types.CallToolResult,
    tool_output_schemas: dict[str, dict[str, Any] | None],
    list_tools_fn: Any,  # Callable[[], Awaitable[None]]
    client_name: str | None = None,
    raise_on_error: bool = False,
    pinned_version: str | None = None,
    listed_versions: Mapping[str, str | None] | None = None,
) -> CallToolResult:
    """Parse an mcp_types.CallToolResult into our CallToolResult dataclass.

    Args:
        name: Tool name (for schema lookup)
        result: Raw MCP protocol result
        tool_output_schemas: Dictionary mapping tool names to their output schemas
        list_tools_fn: Async function to refresh tool schemas if needed
        client_name: Optional client name for logging
        raise_on_error: Whether to raise ToolError on errors
        pinned_version: The tool version the call requested, if any.
        listed_versions: The version each listed tool advertises, keyed by name.
            The cached schema describes that version only, so a result pinned
            to any other version (or when the listed version is unknown) is
            hydrated from ``structured_content`` as it arrived instead.

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
    elif result.structured_content is not None and not result.is_error:
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

            # The cached schema describes the listed version only. A call
            # pinned to another version has no advertised schema, so coercing
            # through the cached one would reject content that is valid for
            # the version that ran; only the result meta's wrap flag applies.
            if pinned_version is None or (
                listed_versions is not None
                and listed_versions.get(name) == pinned_version
            ):
                output_schema = tool_output_schemas.get(name)
            else:
                output_schema = None

            if wrap_from_meta or (
                output_schema and output_schema.get("x-fastmcp-wrap-result")
            ):
                structured_content = result.structured_content.get("result")
            else:
                structured_content = result.structured_content

            # Type-validate through the schema if available.
            if output_schema:
                if wrap_from_meta or output_schema.get("x-fastmcp-wrap-result"):
                    output_schema = _wrapped_result_schema(output_schema)
                type_adapter = json_schema_to_type_adapter(output_schema)
                typed = type_adapter.validate_python(structured_content)
                # `data` is a convenience view of `structured_content`, so it
                # must not disagree with what the server sent: a pydantic
                # union can coerce across branches (0 → False) or drop keys.
                # When the typed form re-serializes differently, hand back
                # the payload untouched.
                if _round_trips_to_payload(
                    type_adapter,
                    typed,
                    structured_content,
                    output_schema=output_schema,
                ):
                    data = typed
                else:
                    data = structured_content
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
