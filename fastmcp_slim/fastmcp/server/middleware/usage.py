"""Per-user, per-component usage metrics.

FastMCP's spans already carry the user, tool, latency and error for every
request, but traces are often sampled, which makes them a poor source for
usage counts. `UsageMiddleware` records the same facts as an OpenTelemetry
metric, independently of trace sampling.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any, Literal

import anyio
import mcp_types as mt
from opentelemetry import metrics
from opentelemetry.metrics import Histogram, MeterProvider

from fastmcp.exceptions import (
    AuthorizationError,
    DisabledError,
    FastMCPError,
    MCPError,
    NotFoundError,
    to_mcp_error,
)
from fastmcp.prompts.base import PromptResult
from fastmcp.server.context import _current_transport
from fastmcp.server.dependencies import fastmcp_request_ctx, get_access_token
from fastmcp.telemetry import INSTRUMENTATION_NAME
from fastmcp.tools.base import ToolResult

from .middleware import CallNext, Middleware, MiddlewareContext

UserIdMode = Literal["hashed", "raw", "omit"]


OPERATION_DURATION_METRIC = "mcp.server.operation.duration"

# Bucket boundaries the MCP semantic conventions recommend for this metric.
OPERATION_DURATION_BUCKETS = (
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1,
    2,
    5,
    10,
    30,
    60,
    120,
    300,
)

# JSON-RPC error codes the MCP semantic conventions say describe a request the
# server could not serve, not a failure of the server: parse error, invalid
# request, method not found, invalid params, and resource not found.
NON_ERROR_CODES = frozenset({-32700, -32600, -32601, -32602, -32002})

# Rejections that happen before the request resolves to a component. The name
# they carry is whatever the client sent, so it is left off the data point.
_ROUTING_ERRORS = (NotFoundError, DisabledError, AuthorizationError)


class UsageMiddleware(Middleware):
    """Record tool and prompt usage as an OpenTelemetry metric.

    Each `tools/call` and `prompts/get` request is recorded once on the
    `mcp.server.operation.duration` histogram (seconds), following the
    OpenTelemetry MCP semantic conventions for that metric. The histogram's
    count is the number of calls, so one instrument answers "who called what,
    how often, how fast, and how often it failed".

    Attributes on each data point:

    - `mcp.method.name`: `tools/call` or `prompts/get`
    - `gen_ai.tool.name` or `gen_ai.prompt.name`: the component name the
      client asked for, omitted for routing and access failures
    - `gen_ai.operation.name`: `execute_tool`, on tool calls only
    - `error.type`: present only when the call failed. A JSON-RPC error
      reports its code, an error tool result reports `tool_error`, and a
      cancelled or timed-out request reports `cancelled`
    - `rpc.status_code`: the JSON-RPC error code, when the response is a
      JSON-RPC error. Codes that the conventions classify as client errors,
      such as `-32602`, are reported here without `error.type`
    - `mcp.protocol.version`, `network.transport`, `network.protocol.name`
      and `network.protocol.version`: when the request came over a transport
    - `fastmcp.server.name`: the server handling the request
    - `enduser.pseudo.id` (default) or `enduser.id`: the authenticated
      user, when the server has auth (see `user_id`)

    The duration runs from when the request reaches this middleware until the
    result returns to it, so add `UsageMiddleware` first, before error
    handling and any middleware that can answer without calling the next
    handler. In that position the duration comes as close as middleware can to
    the conventions' receipt-to-response definition, and the error
    classification matches the response the client receives.

    FastMCP depends only on the OpenTelemetry API, so this middleware records
    nothing until you configure an OpenTelemetry SDK `MeterProvider` with an
    exporter.

    Background tasks measure submission, not eventual execution or failure.
    Guard continuations count as separate requests.

    Args:
        user_id: How to report the authenticated user. `"hashed"` (default)
            reports a keyed SHA-256 digest as `enduser.pseudo.id`, so usage
            can be grouped per user without exporting the identity itself.
            `"raw"` reports the token subject (or client ID) as `enduser.id`.
            `"omit"` reports no user attribute.
        user_id_salt: Secret key for the `"hashed"` digest. Required when
            `user_id="hashed"`, because an unkeyed digest of a known
            identifier such as an email address can be recomputed and
            matched. Ignored for `"raw"` and `"omit"`.
        meter_provider: Meter provider to use. Defaults to the global
            provider.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.middleware.usage import UsageMiddleware

        mcp = FastMCP("MyServer")
        mcp.add_middleware(UsageMiddleware(user_id_salt="a-secret-value"))
        ```
    """

    def __init__(
        self,
        *,
        user_id: UserIdMode = "hashed",
        user_id_salt: str | bytes | None = None,
        meter_provider: MeterProvider | None = None,
    ):
        if user_id not in ("hashed", "raw", "omit"):
            raise ValueError(
                f"user_id must be 'hashed', 'raw', or 'omit', not {user_id!r}"
            )
        if user_id == "hashed" and not user_id_salt:
            raise ValueError(
                "UsageMiddleware hashes user IDs by default and needs a secret "
                "key to do it safely. Pass user_id_salt='<secret>', or choose "
                "user_id='raw' to record identities as-is or user_id='omit' to "
                "record no user."
            )
        self.user_id = user_id
        if isinstance(user_id_salt, str):
            user_id_salt = user_id_salt.encode()
        self._salt = user_id_salt or b""
        meter = metrics.get_meter(INSTRUMENTATION_NAME, meter_provider=meter_provider)
        self._duration: Histogram = meter.create_histogram(
            name=OPERATION_DURATION_METRIC,
            unit="s",
            description="MCP request duration as observed by the usage middleware.",
            explicit_bucket_boundaries_advisory=list(OPERATION_DURATION_BUCKETS),
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        return await self._record(
            context,
            call_next,
            method="tools/call",
            name_attribute="gen_ai.tool.name",
            name=context.message.name,
        )

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        return await self._record(
            context,
            call_next,
            method="prompts/get",
            name_attribute="gen_ai.prompt.name",
            name=context.message.name,
        )

    async def _record(
        self,
        context: MiddlewareContext[Any],
        call_next: Callable[[MiddlewareContext[Any]], Any],
        *,
        method: str,
        name_attribute: str,
        name: str,
    ) -> Any:
        attributes: dict[str, str] = {
            "mcp.method.name": method,
            name_attribute: name,
            **self._request_attributes(),
            **self._server_attributes(context),
            **self._user_attributes(),
        }
        if method == "tools/call":
            attributes["gen_ai.operation.name"] = "execute_tool"
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except anyio.get_cancelled_exc_class():
            attributes["error.type"] = "cancelled"
            self._duration.record(time.perf_counter() - start, attributes)
            raise
        except Exception as e:
            if _is_routing_error(e):
                attributes.pop(name_attribute, None)
            attributes.update(_error_attributes(method, e))
            self._duration.record(time.perf_counter() - start, attributes)
            raise
        if isinstance(result, ToolResult) and result.is_error:
            attributes["error.type"] = "tool_error"
        self._duration.record(time.perf_counter() - start, attributes)
        return result

    @staticmethod
    def _request_attributes() -> dict[str, str]:
        request_ctx = fastmcp_request_ctx.get()
        if request_ctx is None:
            return {}
        attrs: dict[str, str] = {}
        if request_ctx.protocol_version:
            attrs["mcp.protocol.version"] = request_ctx.protocol_version
        if request_ctx.request is not None:
            attrs["network.transport"] = "tcp"
            attrs["network.protocol.name"] = "http"
            http_version = request_ctx.request.scope.get("http_version")
            if http_version:
                attrs["network.protocol.version"] = http_version
        elif _current_transport.get() == "stdio":
            attrs["network.transport"] = "pipe"
        return attrs

    @staticmethod
    def _server_attributes(context: MiddlewareContext[Any]) -> dict[str, str]:
        if context.fastmcp_context is None:
            return {}
        return {"fastmcp.server.name": context.fastmcp_context.fastmcp.name}

    def _user_attributes(self) -> dict[str, str]:
        if self.user_id == "omit":
            return {}
        token = get_access_token()
        if token is None:
            return {}
        identity = token.subject or token.client_id
        if not identity:
            return {}
        if self.user_id == "raw":
            return {"enduser.id": identity}
        digest = hmac.new(self._salt, identity.encode(), hashlib.sha256).hexdigest()
        return {"enduser.pseudo.id": digest[:32]}


def _is_routing_error(e: Exception) -> bool:
    """Whether the request was rejected before it resolved to a component.

    `ErrorHandlingMiddleware` re-raises such a rejection as an `MCPError` whose
    cause is the original exception, so that case counts too.
    """
    if isinstance(e, _ROUTING_ERRORS):
        return True
    return isinstance(e, MCPError) and isinstance(e.__cause__, _ROUTING_ERRORS)


def _error_attributes(method: str, e: Exception) -> dict[str, str]:
    """Classify a failed request the way the client will see it.

    `tools/call` returns FastMCP errors, including unknown tools, as a
    `CallToolResult` with `isError` set, which the conventions report as
    `tool_error`. Other FastMCP errors reach the wire as JSON-RPC errors, whose
    code is reported as `rpc.status_code`, and as `error.type` unless the code
    is one the conventions do not count as an error. Any other exception is
    turned into a JSON-RPC error by the MCP SDK after this middleware returns,
    so it is reported by its class name.
    """
    if method == "tools/call" and isinstance(
        e, (FastMCPError, NotFoundError, DisabledError)
    ):
        return {"error.type": "tool_error"}
    if not isinstance(e, (MCPError, FastMCPError, NotFoundError, DisabledError)):
        return {"error.type": type(e).__qualname__}
    code = to_mcp_error(e).error.code
    attrs = {"rpc.status_code": str(code)}
    if code not in NON_ERROR_CODES:
        attrs["error.type"] = str(code)
    return attrs
