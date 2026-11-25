"""Tests for usage metrics middleware."""

import hashlib
import hmac

import anyio
import mcp_types as mt
import pytest
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import (
    AuthorizationError,
    MCPError,
    NotFoundError,
    PromptError,
    ToolError,
)
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import reset_transport, set_transport
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware import usage as usage_module
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.usage import UsageMiddleware
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.tests import asgi_client


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def meter_provider(reader: InMemoryMetricReader) -> MeterProvider:
    return MeterProvider(metric_readers=[reader])


def data_points(reader: InMemoryMetricReader) -> list:
    metrics_data = reader.get_metrics_data()
    if metrics_data is None:
        return []
    return [
        point
        for resource_metrics in metrics_data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == "mcp.server.operation.duration"
        for point in metric.data.data_points
    ]


def make_server(middleware: UsageMiddleware) -> FastMCP:
    mcp = FastMCP("usage-test")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    @mcp.tool
    def fail() -> None:
        raise ToolError("nope")

    @mcp.prompt
    def greet(name: str) -> str:
        return f"hello {name}"

    mcp.add_middleware(middleware)
    return mcp


def use_token(monkeypatch: pytest.MonkeyPatch, subject: str | None) -> None:
    token = AccessToken(
        token="t",
        client_id="client-1",
        scopes=[],
        expires_at=None,
        subject=subject,
    )
    monkeypatch.setattr(usage_module, "get_access_token", lambda: token)


class TestUsageMiddleware:
    async def test_unknown_tools_share_one_series(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        for name in ["missing-one", "missing-two", "missing-three"]:
            with pytest.raises(NotFoundError):
                await mcp.call_tool(name)

        [point] = data_points(reader)
        assert point.count == 3
        assert "gen_ai.tool.name" not in point.attributes
        # tools/call answers an unknown tool with an isError result.
        assert point.attributes["error.type"] == "tool_error"
        assert "rpc.status_code" not in point.attributes

    async def test_unknown_prompts_share_one_series(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        for name in ["missing-one", "missing-two", "missing-three"]:
            with pytest.raises(NotFoundError):
                await mcp.render_prompt(name)

        [point] = data_points(reader)
        assert point.count == 3
        assert "gen_ai.prompt.name" not in point.attributes
        # prompts/get answers with -32602, which the semconv does not count
        # as an error.
        assert point.attributes["rpc.status_code"] == "-32602"
        assert "error.type" not in point.attributes

    async def test_error_handling_middleware_keeps_unknown_names_bounded(
        self, meter_provider, reader
    ):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        mcp.add_middleware(ErrorHandlingMiddleware())
        async with Client(mcp) as client:
            for name in ["missing-one", "missing-two"]:
                with pytest.raises(MCPError):
                    await client.call_tool(name, {})

        [point] = data_points(reader)
        assert point.count == 2
        assert "gen_ai.tool.name" not in point.attributes
        assert point.attributes["error.type"] == "-32001"
        assert point.attributes["rpc.status_code"] == "-32001"

    async def test_denied_names_share_one_series(self, meter_provider, reader):
        class DenyRequests(Middleware):
            async def on_call_tool(self, context, call_next):
                raise AuthorizationError("denied")

        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        mcp.add_middleware(DenyRequests())
        for name in ["add", "missing-one", "missing-two"]:
            with pytest.raises(AuthorizationError):
                await mcp.call_tool(name)

        [point] = data_points(reader)
        assert point.count == 3
        assert "gen_ai.tool.name" not in point.attributes
        assert point.attributes["error.type"] == "tool_error"

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    async def test_client_info_does_not_add_series(self, meter_provider, reader, mode):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        for i in range(3):
            async with Client(
                mcp,
                mode=mode,
                client_info=mt.Implementation(name=f"client-{i}", version=f"{i}.0"),
            ) as client:
                await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.count == 3
        assert not any(key.startswith("fastmcp.client.") for key in point.attributes)

    async def test_uses_semconv_bucket_boundaries(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        await mcp.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert list(point.explicit_bounds) == [
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
        ]

    async def test_records_cancelled_calls(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        started = anyio.Event()
        returned = False

        @mcp.tool
        async def hang() -> str:
            started.set()
            await anyio.sleep(30)
            return "done"

        async def call() -> None:
            nonlocal returned
            await mcp.call_tool("hang")
            returned = True

        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            await started.wait()
            tg.cancel_scope.cancel()

        assert not returned
        [point] = data_points(reader)
        assert point.count == 1
        assert point.attributes["gen_ai.tool.name"] == "hang"
        assert point.attributes["error.type"] == "cancelled"

    async def test_records_client_timeouts(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        @mcp.tool
        async def hang() -> str:
            await anyio.sleep(30)
            return "done"

        async with Client(mcp) as client:
            with pytest.raises(Exception, match="timed out"):
                await client.call_tool("hang", {}, timeout=0.2)
            with anyio.fail_after(2):
                while not data_points(reader):
                    await anyio.sleep(0.02)

        [point] = data_points(reader)
        assert point.attributes["error.type"] == "cancelled"

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    async def test_records_tool_calls(self, meter_provider, reader, mode):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="salt")
        )

        async with Client(
            mcp,
            mode=mode,
            client_info=mt.Implementation(name="test-client", version="1.0"),
        ) as client:
            await client.call_tool("add", {"a": 1, "b": 2})
            await client.call_tool("add", {"a": 3, "b": 4})

        [point] = data_points(reader)
        assert point.count == 2
        attributes = dict(point.attributes)
        protocol_version = attributes.pop("mcp.protocol.version")
        assert (protocol_version in MODERN_PROTOCOL_VERSIONS) == (mode == "auto")
        assert attributes == {
            "mcp.method.name": "tools/call",
            "gen_ai.tool.name": "add",
            "gen_ai.operation.name": "execute_tool",
            "fastmcp.server.name": "usage-test",
        }

    async def test_records_http_transport(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        async with asgi_client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["network.transport"] == "tcp"
        assert point.attributes["network.protocol.name"] == "http"
        assert point.attributes["network.protocol.version"] == "1.1"
        assert "mcp.protocol.version" in point.attributes

    async def test_records_stdio_transport(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        token = set_transport("stdio")
        try:
            async with Client(mcp) as client:
                await client.call_tool("add", {"a": 1, "b": 2})
        finally:
            reset_transport(token)

        [point] = data_points(reader)
        assert point.attributes["network.transport"] == "pipe"
        assert "network.protocol.name" not in point.attributes

    async def test_in_memory_calls_have_no_network_attributes(
        self, meter_provider, reader
    ):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        async with Client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert not any(key.startswith("network.") for key in point.attributes)

    async def test_json_rpc_errors_report_their_code(self, meter_provider, reader):
        class RejectRequests(Middleware):
            async def on_call_tool(self, context, call_next):
                raise MCPError(code=-32001, message="busy")

        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        mcp.add_middleware(RejectRequests())
        async with Client(mcp) as client:
            with pytest.raises(MCPError):
                await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["gen_ai.tool.name"] == "add"
        assert point.attributes["error.type"] == "-32001"
        assert point.attributes["rpc.status_code"] == "-32001"

    @pytest.mark.parametrize("code", [-32700, -32600, -32601, -32602, -32002])
    async def test_client_error_codes_are_not_errors(
        self, meter_provider, reader, code
    ):
        class RejectRequests(Middleware):
            async def on_call_tool(self, context, call_next):
                raise MCPError(code=code, message="rejected")

        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        mcp.add_middleware(RejectRequests())
        with pytest.raises(MCPError):
            await mcp.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["rpc.status_code"] == str(code)
        assert "error.type" not in point.attributes

    async def test_unhandled_exceptions_report_their_class(
        self, meter_provider, reader
    ):
        class Explode(Middleware):
            async def on_call_tool(self, context, call_next):
                raise RuntimeError("boom")

        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        mcp.add_middleware(Explode())
        with pytest.raises(RuntimeError):
            await mcp.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["error.type"] == "RuntimeError"
        assert "rpc.status_code" not in point.attributes

    async def test_prompt_errors_report_json_rpc_codes(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        @mcp.prompt
        def broken() -> str:
            raise PromptError("nope")

        async with Client(mcp) as client:
            with pytest.raises(MCPError):
                await client.get_prompt("broken")

        [point] = data_points(reader)
        assert point.attributes["gen_ai.prompt.name"] == "broken"
        assert point.attributes["error.type"] == "-32603"
        assert point.attributes["rpc.status_code"] == "-32603"

    async def test_prompt_invalid_arguments_are_not_errors(
        self, meter_provider, reader
    ):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        async with Client(mcp) as client:
            with pytest.raises(MCPError):
                await client.get_prompt("greet")

        [point] = data_points(reader)
        assert point.attributes["gen_ai.prompt.name"] == "greet"
        assert point.attributes["rpc.status_code"] == "-32602"
        assert "error.type" not in point.attributes

    async def test_execution_not_found_keeps_tool_name(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        @mcp.tool
        def lookup() -> None:
            raise NotFoundError("record missing")

        with pytest.raises(ToolError, match="record missing"):
            await mcp.call_tool("lookup")
        [point] = data_points(reader)
        assert point.attributes["gen_ai.tool.name"] == "lookup"
        assert point.attributes["error.type"] == "tool_error"

    async def test_records_errors(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="salt")
        )

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("fail", {})

        [point] = data_points(reader)
        assert point.attributes["gen_ai.tool.name"] == "fail"
        assert point.attributes["error.type"] == "tool_error"

    async def test_records_returned_tool_errors(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        @mcp.tool
        def soft_fail() -> ToolResult:
            return ToolResult(content="nope", is_error=True)

        async with Client(mcp) as client:
            result = await client.call_tool("soft_fail", {}, raise_on_error=False)

        assert result.is_error
        [point] = data_points(reader)
        assert point.attributes["gen_ai.tool.name"] == "soft_fail"
        assert point.attributes["error.type"] == "tool_error"

    async def test_records_prompts(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="salt")
        )

        async with Client(mcp) as client:
            await client.get_prompt("greet", {"name": "nate"})

        [point] = data_points(reader)
        assert point.attributes["mcp.method.name"] == "prompts/get"
        assert point.attributes["gen_ai.prompt.name"] == "greet"
        assert "gen_ai.operation.name" not in point.attributes
        assert "error.type" not in point.attributes

    async def test_ignores_list_requests(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="salt")
        )

        async with Client(mcp) as client:
            await client.list_tools()

        assert data_points(reader) == []

    async def test_hashes_user_by_default(self, meter_provider, reader, monkeypatch):
        use_token(monkeypatch, "user@example.com")
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="s3cret")
        )

        async with Client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        digest = hmac.new(b"s3cret", b"user@example.com", hashlib.sha256).hexdigest()
        assert point.attributes["enduser.pseudo.id"] == digest[:32]
        assert "enduser.id" not in point.attributes

    async def test_raw_user_id(self, meter_provider, reader, monkeypatch):
        use_token(monkeypatch, "user@example.com")
        mcp = make_server(UsageMiddleware(meter_provider=meter_provider, user_id="raw"))

        async with Client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["enduser.id"] == "user@example.com"

    async def test_raw_user_id_falls_back_to_client_id(
        self, meter_provider, reader, monkeypatch
    ):
        use_token(monkeypatch, None)
        mcp = make_server(UsageMiddleware(meter_provider=meter_provider, user_id="raw"))

        async with Client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert point.attributes["enduser.id"] == "client-1"

    async def test_omit_user_id(self, meter_provider, reader, monkeypatch):
        use_token(monkeypatch, "user@example.com")
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )

        async with Client(mcp) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        [point] = data_points(reader)
        assert not any(key.startswith("enduser.") for key in point.attributes)

    async def test_no_meter_provider_is_a_no_op(self):
        mcp = make_server(UsageMiddleware(user_id_salt="salt"))

        async with Client(mcp) as client:
            result = await client.call_tool("add", {"a": 1, "b": 2})

        assert result.data == 3

    @pytest.mark.parametrize("salt", [None, "", b""])
    def test_hashing_requires_a_salt(self, salt):
        with pytest.raises(ValueError, match="user_id_salt"):
            UsageMiddleware(user_id_salt=salt)

    @pytest.mark.parametrize("mode", ["raw", "omit"])
    def test_salt_not_required_without_hashing(self, mode):
        UsageMiddleware(user_id=mode)

    def test_rejects_unknown_user_id_mode(self):
        with pytest.raises(ValueError, match="user_id"):
            UsageMiddleware(user_id="plain")  # ty: ignore[invalid-argument-type]
