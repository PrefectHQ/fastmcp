from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import mcp_types
import pytest
from mcp.server import Server as LowLevelServer
from mcp.server.mcpserver import MCPServer
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from fastmcp import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.exceptions import (
    InvalidToolOutputSchemaError,
    ToolError,
    ToolOutputValidationError,
)
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import FastMCPProxy

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
}
SENTINEL = "returned-payload-sentinel-9eabda"


def make_upstream(
    result: mcp_types.CallToolResult,
    *,
    output_schema: dict[str, Any] = OUTPUT_SCHEMA,
) -> MCPServer:
    async def list_tools(_context: Any, _params: Any) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name="get_headers",
                    input_schema={"type": "object"},
                    output_schema=output_schema,
                )
            ]
        )

    async def call_tool(
        _context: Any, _params: mcp_types.CallToolRequestParams
    ) -> mcp_types.CallToolResult:
        return result

    server = MCPServer("upstream")
    server._lowlevel_server = LowLevelServer(
        "upstream", on_list_tools=list_tools, on_call_tool=call_tool
    )
    return server


def text_of(result: mcp_types.CallToolResult) -> str:
    assert result.content
    assert isinstance(result.content[0], mcp_types.TextContent)
    return result.content[0].text


@pytest.mark.parametrize(
    ("returned", "received_type"),
    [
        ({"secret": SENTINEL}, "object"),
        ([SENTINEL], "array"),
    ],
)
async def test_client_safely_translates_output_validation_errors(
    returned: object, received_type: str
):
    upstream = make_upstream(
        mcp_types.CallToolResult(content=[], structured_content={"result": returned})
    )

    async with Client(upstream) as client:
        tools = await client.list_tools()
        assert tools[0].output_schema == OUTPUT_SCHEMA

        with pytest.raises(ToolOutputValidationError) as exc_info:
            await client.call_tool_mcp("get_headers", {})

    error = exc_info.value
    assert error.tool_name == "get_headers"
    assert error.path == ("result",)
    assert error.rule == "type"
    assert error.expected_types == ("string",)
    assert error.received_type == received_type
    assert SENTINEL not in str(error)
    assert error.__context__ is None
    assert error.__cause__ is None


async def test_proxy_returns_payload_safe_output_validation_error(
    trace_exporter: InMemorySpanExporter,
):
    upstream = make_upstream(
        mcp_types.CallToolResult(
            content=[],
            structured_content={"result": {"authorization": SENTINEL}},
        )
    )

    async with Client(upstream) as upstream_client:
        await upstream_client.list_tools()
        with pytest.raises(ToolOutputValidationError) as exc_info:
            await upstream_client.call_tool_mcp("get_headers", {})
    assert SENTINEL not in str(exc_info.value)

    proxy = create_proxy(upstream)
    async with Client(proxy) as client:
        tools = await client.list_tools()
        assert tools[0].output_schema == OUTPUT_SCHEMA
        result = await client.call_tool_mcp("get_headers", {})

    assert result.is_error is True
    assert text_of(result) == (
        "Tool 'get_headers' returned data that does not match its declared output "
        "schema at 'result': expected string, received object."
    )
    assert SENTINEL not in result.model_dump_json()

    for span in trace_exporter.get_finished_spans():
        assert SENTINEL not in str(span.status.description)
        assert SENTINEL not in repr(span.attributes)
        for event in span.events:
            assert SENTINEL not in repr(event.attributes)
            if event.attributes is not None:
                assert SENTINEL not in str(
                    event.attributes.get("exception.stacktrace", "")
                )


async def test_proxy_reports_invalid_output_schema_separately():
    invalid_schema = {
        "type": "object",
        "properties": {"result": {"type": "not-a-json-type"}},
    }
    upstream = make_upstream(
        mcp_types.CallToolResult(content=[], structured_content={"result": SENTINEL}),
        output_schema=invalid_schema,
    )

    async with Client(upstream) as upstream_client:
        await upstream_client.list_tools()
        with pytest.raises(InvalidToolOutputSchemaError) as exc_info:
            await upstream_client.call_tool_mcp("get_headers", {})
    assert str(exc_info.value) == (
        "Tool 'get_headers' advertised an invalid output schema."
    )
    assert SENTINEL not in str(exc_info.value)

    proxy = create_proxy(upstream)
    async with Client(proxy) as client:
        tools = await client.list_tools()
        assert tools[0].output_schema == invalid_schema
        result = await client.call_tool_mcp("get_headers", {})

    assert result.is_error is True
    assert text_of(result) == (
        "Tool 'get_headers' advertised an invalid output schema."
    )
    assert SENTINEL not in result.model_dump_json()


def make_pydantic_error() -> PydanticValidationError:
    try:
        TypeAdapter(int).validate_python("not an integer")
    except PydanticValidationError as error:
        return error
    raise AssertionError("Expected Pydantic validation to fail")


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("unrelated runtime failure"),
        ToolError("upstream-owned tool failure"),
        make_pydantic_error(),
        httpx.ConnectError("backend transport failure"),
    ],
)
async def test_client_does_not_reclassify_unrelated_failures(failure: Exception):
    upstream = make_upstream(
        mcp_types.CallToolResult(content=[], structured_content={"result": "valid"})
    )

    async with Client(upstream) as client:
        with patch.object(
            client.session,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=failure,
        ):
            with pytest.raises(type(failure)) as exc_info:
                await client.call_tool_mcp("get_headers", {})

    assert exc_info.value is failure


class RuntimeFailureClient(Client):
    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> mcp_types.CallToolResult:
        raise RuntimeError("unrelated backend details")


@pytest.mark.parametrize("mask_error_details", [False, True])
async def test_proxy_preserves_masking_for_unrelated_runtime_errors(
    mask_error_details: bool,
):
    upstream = make_upstream(
        mcp_types.CallToolResult(content=[], structured_content={"result": "valid"})
    )
    proxy = FastMCPProxy(
        client_factory=lambda: RuntimeFailureClient(upstream),
        mask_error_details=mask_error_details,
    )

    async with Client(proxy) as client:
        result = await client.call_tool_mcp("get_headers", {})

    assert result.is_error is True
    if mask_error_details:
        assert text_of(result) == "Error calling tool 'get_headers'"
    else:
        assert text_of(result) == (
            "Error calling tool 'get_headers': unrelated backend details"
        )


async def test_proxy_preserves_valid_results_and_upstream_tool_errors():
    valid_result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="valid")],
        structured_content={"result": "valid"},
    )
    valid_proxy = create_proxy(make_upstream(valid_result))

    async with Client(valid_proxy) as client:
        result = await client.call_tool_mcp("get_headers", {})

    assert result == valid_result

    upstream_error = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="upstream rejected call")],
        structured_content={"result": {"detail": "owned by upstream"}},
        is_error=True,
    )
    error_proxy = create_proxy(make_upstream(upstream_error))

    async with Client(error_proxy) as client:
        result = await client.call_tool_mcp("get_headers", {})

    assert result == upstream_error
