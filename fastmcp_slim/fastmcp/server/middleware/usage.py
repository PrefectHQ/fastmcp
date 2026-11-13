"""Per-user, per-component usage metrics.

FastMCP's spans already carry the user, tool, latency and error for every
request, but traces are often sampled, which makes them a poor source for
usage counts. `UsageMiddleware` records the same facts as an OpenTelemetry
metric, so counts stay exact regardless of trace sampling.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any, Literal

import mcp_types as mt
from opentelemetry import metrics
from opentelemetry.metrics import Histogram, MeterProvider

from fastmcp.prompts.base import PromptResult
from fastmcp.server.dependencies import get_access_token
from fastmcp.telemetry import INSTRUMENTATION_NAME, error_span_attributes
from fastmcp.tools.base import ToolResult

from .middleware import CallNext, Middleware, MiddlewareContext

UserIdMode = Literal["hashed", "raw", "omit"]

OPERATION_DURATION_METRIC = "mcp.server.operation.duration"


class UsageMiddleware(Middleware):
    """Record tool and prompt usage as an OpenTelemetry metric.

    Each `tools/call` and `prompts/get` request is recorded once on the
    `mcp.server.operation.duration` histogram (seconds), the server-side
    operation metric from the OpenTelemetry MCP semantic conventions. The
    histogram's count is the number of calls, so one instrument answers
    "who called what, how often, how fast, and how often it failed".

    Attributes on each data point:

    - `mcp.method.name`: `tools/call` or `prompts/get`
    - `gen_ai.tool.name` or `gen_ai.prompt.name`: the component name the
      client asked for
    - `fastmcp.server.name`: the server handling the request
    - `enduser.pseudo.id` (default) or `enduser.id`: the authenticated
      user, when the server has auth (see `user_id`)
    - `error.type`: present only when the call failed

    FastMCP depends only on the OpenTelemetry API, so this middleware records
    nothing until you configure an OpenTelemetry SDK `MeterProvider` with an
    exporter.

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
            description=(
                "MCP request duration as observed on the server, from receipt "
                "until the result is sent."
            ),
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
            **self._server_attributes(context),
            **self._user_attributes(),
        }
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as e:
            attributes["error.type"] = error_span_attributes(e)["error.type"]
            self._duration.record(time.perf_counter() - start, attributes)
            raise
        self._duration.record(time.perf_counter() - start, attributes)
        return result

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
