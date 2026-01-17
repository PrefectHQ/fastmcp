"""Tests for server-level OpenTelemetry tracing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.auth import AccessToken


class TestToolTracing:
    async def test_call_tool_creates_span(self, trace_exporter: InMemorySpanExporter):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await mcp.call_tool("greet", {"name": "World"})
        assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call greet"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "tools/call"
        # Standard RPC semantic conventions
        assert span.attributes["rpc.system"] == "mcp"
        assert span.attributes["rpc.service"] == "test-server"
        assert span.attributes["rpc.method"] == "tools/call"
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "tool"
        assert span.attributes["fastmcp.component.key"] == "tool:greet@"

    async def test_call_tool_with_error_sets_status(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def failing_tool() -> str:
            raise ValueError("Something went wrong")

        with pytest.raises(ToolError):
            await mcp.call_tool("failing_tool", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call failing_tool"
        assert span.status.status_code == StatusCode.ERROR
        assert len(span.events) > 0  # Exception recorded

    async def test_call_nonexistent_tool_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.call_tool("nonexistent", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call nonexistent"
        assert span.status.status_code == StatusCode.ERROR


class TestResourceTracing:
    async def test_read_resource_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("config://app")
        def get_config() -> str:
            return "app_config_data"

        result = await mcp.read_resource("config://app")
        assert "app_config_data" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read config://app"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "resources/read"
        assert span.attributes["mcp.resource.uri"] == "config://app"
        # Standard RPC semantic conventions
        assert span.attributes["rpc.system"] == "mcp"
        assert span.attributes["rpc.service"] == "test-server"
        assert span.attributes["rpc.method"] == "resources/read"
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "resource"
        assert span.attributes["fastmcp.component.key"] == "resource:config://app@"

    async def test_read_resource_template_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("users://{user_id}/profile")
        def get_user_profile(user_id: str) -> str:
            return f"profile for {user_id}"

        result = await mcp.read_resource("users://123/profile")
        assert "profile for 123" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read users://123/profile"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "resources/read"
        assert span.attributes["mcp.resource.uri"] == "users://123/profile"
        # Standard RPC semantic conventions
        assert span.attributes["rpc.method"] == "resources/read"
        # Template component type is set by get_span_attributes
        assert span.attributes["fastmcp.component.type"] == "resource_template"
        assert (
            span.attributes["fastmcp.component.key"]
            == "template:users://{user_id}/profile@"
        )

    async def test_read_nonexistent_resource_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.read_resource("nonexistent://resource")

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read nonexistent://resource"
        assert span.status.status_code == StatusCode.ERROR


class TestPromptTracing:
    async def test_render_prompt_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        result = await mcp.render_prompt("greeting", {"name": "World"})
        assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "prompts/get greeting"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "prompts/get"
        # Standard RPC semantic conventions
        assert span.attributes["rpc.system"] == "mcp"
        assert span.attributes["rpc.service"] == "test-server"
        assert span.attributes["rpc.method"] == "prompts/get"
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "prompt"
        assert span.attributes["fastmcp.component.key"] == "prompt:greeting@"

    async def test_render_nonexistent_prompt_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.render_prompt("nonexistent", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "prompts/get nonexistent"
        assert span.status.status_code == StatusCode.ERROR


class TestAuthAttributesOnSpans:
    async def test_tool_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="test-client-123",
            scopes=["read", "write"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "test-client-123"
        assert span.attributes["enduser.scope"] == "read write"

    async def test_resource_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("config://app")
        def get_config() -> str:
            return "config_data"

        test_token = AccessToken(
            token="test-token",
            client_id="user-456",
            scopes=["config:read"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.read_resource("config://app")

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "user-456"
        assert span.attributes["enduser.scope"] == "config:read"

    async def test_prompt_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="prompt-user",
            scopes=["prompts"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.render_prompt("greeting", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "prompt-user"
        assert span.attributes["enduser.scope"] == "prompts"

    async def test_span_omits_auth_attributes_when_not_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # No mock - get_access_token returns None by default (no auth context)
        await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        # Auth attributes should not be present
        assert "enduser.id" not in span.attributes
        assert "enduser.scope" not in span.attributes

    async def test_span_omits_scope_when_no_scopes(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="client-no-scopes",
            scopes=[],  # Empty scopes
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "client-no-scopes"
        # Scope attribute should not be present when scopes list is empty
        assert "enduser.scope" not in span.attributes
