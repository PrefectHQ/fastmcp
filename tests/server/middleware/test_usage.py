"""Tests for usage metrics middleware."""

import hashlib
import hmac

import mcp_types as mt
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import AuthorizationError, NotFoundError, ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware import usage as usage_module
from fastmcp.server.middleware.usage import UsageMiddleware
from fastmcp.tools.base import ToolResult


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
    @pytest.mark.parametrize("method", ["tool", "prompt"])
    async def test_unknown_names_share_one_series(self, meter_provider, reader, method):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        for name in ["missing-one", "missing-two", "missing-three"]:
            with pytest.raises(NotFoundError):
                if method == "tool":
                    await mcp.call_tool(name)
                else:
                    await mcp.render_prompt(name)

        [point] = data_points(reader)
        assert point.count == 3
        assert f"gen_ai.{method}.name" not in point.attributes
        assert point.attributes["error.type"] == "NotFoundError"

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

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    async def test_records_client_identity(self, meter_provider, reader, mode):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        for name in ["Claude", "Cursor"]:
            async with Client(
                mcp, mode=mode, client_info=mt.Implementation(name=name, version="1.0")
            ) as client:
                await client.call_tool("add", {"a": 1, "b": 2})
                await client.get_prompt("greet", {"name": "nate"})

        points = data_points(reader)
        assert len(points) == 4
        assert {point.attributes["fastmcp.client.name"] for point in points} == {
            "Claude",
            "Cursor",
        }
        assert all(
            point.attributes["fastmcp.client.version"] == "1.0" for point in points
        )

    async def test_records_tool_calls(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id_salt="salt")
        )

        async with Client(
            mcp, client_info=mt.Implementation(name="test-client", version="1.0")
        ) as client:
            await client.call_tool("add", {"a": 1, "b": 2})
            await client.call_tool("add", {"a": 3, "b": 4})

        [point] = data_points(reader)
        assert point.count == 2
        assert dict(point.attributes) == {
            "mcp.method.name": "tools/call",
            "gen_ai.tool.name": "add",
            "fastmcp.server.name": "usage-test",
            "fastmcp.client.name": "test-client",
            "fastmcp.client.version": "1.0",
        }

    async def test_direct_calls_have_no_client_identity(self, meter_provider, reader):
        mcp = make_server(
            UsageMiddleware(meter_provider=meter_provider, user_id="omit")
        )
        await mcp.call_tool("add", {"a": 1, "b": 2})
        [point] = data_points(reader)
        assert point.attributes["gen_ai.tool.name"] == "add"
        assert not any(key.startswith("fastmcp.client.") for key in point.attributes)

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
