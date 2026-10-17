from typing import Any
from unittest.mock import AsyncMock, patch

import mcp_types
import pytest
from jsonschema import validate
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider, ProxyTool
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools.base import RawStructuredToolResult, Tool, ToolResult
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.asgi_transport import run_asgi_lifespan
from fastmcp.utilities.tests import ASGIServer


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("cache_results", [False, True])
@pytest.mark.parametrize("transform_results", [False, True])
async def test_proxy_preserves_non_object_tool_outputs_across_protocol_versions(
    mode: str,
    cache_results: bool,
    transform_results: bool,
) -> None:
    schemas = {
        "numbers": {"type": "array", "items": {"type": "integer"}},
        "flag": {"type": "boolean"},
        "record": {"type": "object", "properties": {"value": {"type": "integer"}}},
        "matrix": {
            "type": "array",
            "items": {"$ref": "#/$defs/row"},
            "$defs": {"row": {"type": "array", "items": {"type": "integer"}}},
        },
        "old_matrix": {
            "type": "array",
            "items": {"$ref": "#/definitions/row"},
            "definitions": {"row": {"type": "array", "items": {"type": "integer"}}},
        },
        "missing": None,
        "unconstrained": {},
        "choice": {
            "oneOf": [
                {"type": "object", "properties": {"value": {"type": "integer"}}},
                {"type": "array", "items": {"type": "integer"}},
            ]
        },
    }
    values = {
        "numbers": [1, 2, 3],
        "flag": False,
        "record": {"value": 1},
        "matrix": [[1, 2]],
        "old_matrix": [[3, 4]],
        "choice": {"value": 2},
        "unconstrained": {"value": 3},
    }
    calls = {name: 0 for name in schemas}

    async def list_tools(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name=name,
                    input_schema={"type": "object", "properties": {}},
                    output_schema=schema,
                )
                for name, schema in schemas.items()
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        calls[params.name] += 1
        if params.name == "missing":
            return mcp_types.CallToolResult(content=[])
        value = values[params.name]
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(value))],
            structured_content=value,
            _meta={"source": "backend"},
        )

    backend = Server("backend", on_list_tools=list_tools, on_call_tool=call_tool)
    app = backend.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1"]),
    )
    async with run_asgi_lifespan(app):
        upstream = ASGIServer("http://127.0.0.1/mcp", app, "http")
        proxy = FastMCP("proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(upstream.transport(), mode="auto"))
        )
        tool_name = "renamed_numbers" if transform_results else "numbers"
        if transform_results:
            proxy.add_transform(
                ToolTransform({"numbers": ToolTransformConfig(name="renamed_numbers")})
            )

        @proxy.tool(output_schema={"properties": {"value": {"type": "integer"}}})
        def plain_record() -> dict[str, int]:
            return {"value": 7}

        if cache_results:
            proxy.add_middleware(ResponseCachingMiddleware())

        async with Client(proxy, mode=mode) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            numbers = await client.call_tool(tool_name, {})
            flag = await client.call_tool("flag", {})
            record = await client.call_tool("record", {})
            matrix = await client.call_tool("matrix", {})
            old_matrix = await client.call_tool("old_matrix", {})
            missing = await client.call_tool_mcp("missing", {})
            choice = await client.call_tool("choice", {})
            unconstrained = await client.call_tool("unconstrained", {})
            plain = await client.call_tool("plain_record", {})
            if cache_results:
                numbers_cached = await client.call_tool(tool_name, {})
                assert numbers_cached.structured_content == numbers.structured_content
                assert calls["numbers"] == 1

        if cache_results:
            other_mode = "auto" if mode == "legacy" else "legacy"
            async with Client(proxy, mode=other_mode) as other_client:
                other_numbers = await other_client.call_tool(tool_name, {})
                assert other_numbers.structured_content == (
                    [1, 2, 3] if mode == "legacy" else {"result": [1, 2, 3]}
                )
            assert calls["numbers"] == 2

    assert numbers.data == [1, 2, 3]
    assert flag.data is False
    assert record.data.value == 1
    assert numbers.meta["source"] == "backend"
    assert flag.meta["source"] == "backend"
    assert record.structured_content == {"value": 1}
    assert matrix.data == [[1, 2]]
    assert old_matrix.data == [[3, 4]]
    assert choice.data == {"value": 2}
    assert unconstrained.data == {"value": 3}
    assert missing.structured_content is None
    assert plain.structured_content == {"value": 7}
    assert tools["plain_record"].output_schema == {
        **({"type": "object"} if mode == "legacy" else {}),
        "properties": {"value": {"type": "integer"}},
    }
    assert tools["record"].output_schema == schemas["record"]
    if mode == "auto":
        assert tools[tool_name].output_schema == schemas["numbers"]
        assert tools["unconstrained"].output_schema == {}
        assert numbers.structured_content == [1, 2, 3]
        assert flag.structured_content is False
        assert choice.structured_content == {"value": 2}
    else:
        assert tools[tool_name].output_schema["type"] == "object"
        assert (
            tools[tool_name].output_schema["properties"]["result"] == schemas["numbers"]
        )
        assert tools["unconstrained"].output_schema["type"] == "object"
        assert tools["unconstrained"].output_schema["properties"]["result"] == {}
        assert numbers.structured_content == {"result": [1, 2, 3]}
        assert unconstrained.structured_content == {"result": {"value": 3}}
        assert flag.structured_content == {"result": False}
        assert choice.structured_content == {"result": {"value": 2}}
        validate(matrix.structured_content, tools["matrix"].output_schema)
        validate(old_matrix.structured_content, tools["old_matrix"].output_schema)


async def test_modern_proxy_middleware_edits_reach_clients_and_cache() -> None:
    observed: list[Any] = []
    backend_calls = 0

    async def list_tools(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name="numbers",
                    input_schema={"type": "object"},
                    output_schema={"type": "array", "items": {"type": "integer"}},
                )
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        nonlocal backend_calls
        backend_calls += 1
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="original")],
            structured_content=[1, 2, 3],
            _meta={"source": "backend"},
        )

    class RedactMiddleware(Middleware):
        async def on_call_tool(self, context, call_next):
            result = await call_next(context)
            observed.append(result.structured_content)
            return result.model_copy(update={"structured_content": [9]})

    backend = Server("backend", on_list_tools=list_tools, on_call_tool=call_tool)
    app = backend.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1"]),
    )
    async with run_asgi_lifespan(app):
        upstream = ASGIServer("http://127.0.0.1/mcp", app, "http")
        proxy = FastMCP("proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(upstream.transport(), mode="auto"))
        )
        proxy.add_middleware(ResponseCachingMiddleware())
        proxy.add_middleware(RedactMiddleware())

        async with Client(proxy, mode="auto") as client:
            first = await client.call_tool("numbers", {})
            cached = await client.call_tool("numbers", {})

    assert observed == [[1, 2, 3]]
    assert first.structured_content == [9]
    assert cached.structured_content == [9]
    assert first.meta["source"] == "backend"
    assert backend_calls == 1


async def test_legacy_proxy_wraps_explicit_null_from_backend_model() -> None:
    # The SDK currently omits null fields during wire serialization, so
    # inject the backend model directly to exercise presence detection.
    explicit_null = mcp_types.CallToolResult(content=[], structured_content=None)
    absent = mcp_types.CallToolResult(content=[])
    assert "structured_content" in explicit_null.model_fields_set
    assert "structured_content" not in absent.model_fields_set

    backend = FastMCP("backend")
    proxy = FastMCP("proxy")
    proxy.add_tool(
        ProxyTool(
            client_factory=lambda: ProxyClient(backend, mode="legacy"),
            name="nullable",
            parameters={"type": "object"},
            output_schema={"type": "null"},
        )
    )

    with patch.object(
        ProxyClient,
        "call_tool_mcp",
        new_callable=AsyncMock,
        return_value=explicit_null,
    ):
        async with Client(proxy, mode="legacy") as client:
            result = await client.call_tool_mcp("nullable", {})

    assert result.structured_content == {"result": None}


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("transform_results", [False, True])
@pytest.mark.parametrize("raw_output", [False, True])
@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "oneOf": [
                {"type": "object", "properties": {"value": {"type": "integer"}}},
                {"type": "array", "items": {"type": "integer"}},
            ]
        },
    ],
)
async def test_custom_tool_result_matches_legacy_advertised_wrapper(
    mode: str,
    transform_results: bool,
    raw_output: bool,
    schema: dict[str, Any],
) -> None:
    class CustomTool(Tool):
        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            if raw_output:
                return RawStructuredToolResult(content=[], structured_content=[1])
            return self.convert_result({"value": 1})

    server = FastMCP("custom")
    server.add_tool(
        CustomTool(
            name="custom",
            parameters={"type": "object", "properties": {}},
            output_schema=schema,
        )
    )
    tool_name = "renamed_custom" if transform_results else "custom"
    if transform_results:
        server.add_transform(
            ToolTransform({"custom": ToolTransformConfig(name=tool_name)})
        )

    direct = await server.call_tool(tool_name, {})
    expected = [1] if raw_output else {"value": 1}
    assert direct.structured_content == expected

    async with Client(server, mode=mode) as client:
        advertised = (await client.list_tools())[0].output_schema
        result = await client.call_tool_mcp(tool_name, {})

    if mode == "legacy":
        assert advertised["properties"]["result"] == schema
        assert result.structured_content == {"result": expected}
    else:
        assert advertised == schema
        assert result.structured_content == expected
