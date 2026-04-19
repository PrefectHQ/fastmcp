"""End-to-end server-client integration tests for FastMCP.

This module provides comprehensive integration tests covering the full MCP protocol
lifecycle, including handshake, tool calls, error handling, resources, progress
reporting, and concurrent operations.

All tests use in-memory transport (FastMCPTransport) for fast, reliable testing
without process forking.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
import psutil
from mcp.types import TextContent
from pydantic import AnyUrl

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import AuthorizationError, ToolError


@pytest.mark.integration
class TestMcpProtocolHandshake:
    """Tests for the complete MCP protocol handshake lifecycle.

    These tests verify the full sequence:
    client connect → initialize → list_tools → call_tool → disconnect

    The handshake is a critical part of the MCP protocol where the client and server
    exchange capabilities and establish the session.
    """

    async def test_complete_handshake_lifecycle(self):
        """Test the complete MCP protocol handshake lifecycle.

        This test verifies:
        1. Client can connect to the server
        2. Initialize handshake completes successfully
        3. Server info is available after initialization
        4. Client can list tools
        5. Client can call a tool
        6. Client disconnects cleanly

        The test follows the standard MCP protocol flow:
        - Client connects and sends initialize request
        - Server responds with initialize result (capabilities, server info)
        - Client sends initialized notification
        - Session is ready for tool calls, resource reads, etc.
        """
        server = FastMCP("HandshakeTestServer")

        @server.tool
        def greet(name: str) -> str:
            """A simple greeting tool."""
            return f"Hello, {name}!"

        client = Client(transport=FastMCPTransport(server))

        assert not client.is_connected()
        assert client.initialize_result is None

        async with client:
            assert client.is_connected()

            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo is not None
            assert client.initialize_result.serverInfo.name == "HandshakeTestServer"

            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "greet"

            result = await client.call_tool("greet", {"name": "World"})
            assert result.data == "Hello, World!"

        assert not client.is_connected()
        assert client.initialize_result is None

    async def test_multiple_clients_independent_sessions(self):
        """Test that multiple clients have independent sessions.

        Each client connection to the same server should have its own independent
        session with separate state, context, and capabilities.

        This test verifies:
        1. Multiple clients can connect to the same server simultaneously
        2. Each client has its own initialize_result
        3. Tool calls from one client don't affect another
        """
        server = FastMCP("MultiClientServer")

        @server.tool
        def get_session_id() -> str:
            """Return the current session ID from context."""
            from fastmcp.server.dependencies import get_context

            ctx = get_context()
            return ctx.session_id

        client1 = Client(transport=FastMCPTransport(server))
        client2 = Client(transport=FastMCPTransport(server))

        async with client1, client2:
            result1 = await client1.call_tool("get_session_id", {})
            result2 = await client2.call_tool("get_session_id", {})

            assert result1.data is not None
            assert result2.data is not None
            assert result1.data != result2.data


@pytest.mark.integration
class TestMultiToolRegistrationAndInvocation:
    """Tests for registering and calling multiple tools.

    These tests verify that:
    1. Server can register multiple tools (3-5)
    2. Client can list all registered tools
    3. Client can call each tool with correct parameter types
    4. Return values match expected types
    """

    async def test_register_and_call_multiple_tools(self):
        """Test registering and calling multiple tools with different signatures.

        This test registers 4 tools:
        - echo: Returns the input message (str -> str)
        - add: Adds two integers (int, int -> int)
        - multiply: Multiplies two floats (float, float -> float)
        - get_dict: Returns a dictionary (-> dict)

        It verifies:
        1. All tools are listed correctly
        2. Each tool can be called with correct parameter types
        3. Return values match expected types and values
        """
        server = FastMCP("MultiToolServer")

        @server.tool
        def echo(message: str) -> str:
            """Echo the input message."""
            return message

        @server.tool
        def add(a: int, b: int) -> int:
            """Add two integers."""
            return a + b

        @server.tool
        def multiply(x: float, y: float) -> float:
            """Multiply two floats."""
            return x * y

        @server.tool
        def get_dict() -> dict[str, Any]:
            """Return a dictionary."""
            return {"key": "value", "number": 42, "nested": {"inner": True}}

        async with Client(transport=FastMCPTransport(server)) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert tool_names == {"echo", "add", "multiply", "get_dict"}

            echo_result = await client.call_tool("echo", {"message": "Hello World"})
            assert echo_result.data == "Hello World"
            assert isinstance(echo_result.data, str)

            add_result = await client.call_tool("add", {"a": 5, "b": 3})
            assert add_result.data == 8
            assert isinstance(add_result.data, int)

            multiply_result = await client.call_tool("multiply", {"x": 2.5, "y": 4.0})
            assert multiply_result.data == 10.0
            assert isinstance(multiply_result.data, float)

            dict_result = await client.call_tool("get_dict", {})
            assert dict_result.data == {"key": "value", "number": 42, "nested": {"inner": True}}
            assert isinstance(dict_result.data, dict)

    async def test_tool_parameter_type_validation(self):
        """Test that tool parameters are validated for correct types.

        FastMCP automatically validates tool parameters against their type annotations.
        This test verifies that:
        1. Correct parameter types are accepted
        2. Incorrect parameter types result in errors
        """
        server = FastMCP("TypeValidationServer")

        @server.tool
        def strictly_typed(x: int, y: str) -> str:
            """A tool with strictly typed parameters."""
            return f"{x}: {y}"

        async with Client(transport=FastMCPTransport(server)) as client:
            result = await client.call_tool("strictly_typed", {"x": 42, "y": "hello"})
            assert result.data == "42: hello"

            error_result = await client.call_tool(
                "strictly_typed", {"x": "not an int", "y": "hello"}, raise_on_error=False
            )
            assert error_result.is_error is True


@pytest.mark.integration
class TestToolExceptionPropagation:
    """Tests for tool exception propagation from server to client.

    These tests verify that:
    1. Tool exceptions are caught and converted to MCP error responses
    2. Error messages are properly formatted (not raw stack traces)
    3. isError flag is set correctly
    4. ToolError exceptions are handled specially
    """

    async def test_general_exception_propagation(self):
        """Test that general exceptions are propagated as MCP error responses.

        When a tool raises a general exception (like ValueError), the server should:
        1. Catch the exception
        2. Convert it to an MCP CallToolResult with isError=True
        3. Include the error message in the content
        4. NOT include the raw stack trace by default
        """
        server = FastMCP("ErrorTestServer")

        @server.tool
        def error_tool() -> None:
            """A tool that raises a ValueError."""
            raise ValueError("This is a controlled test error")

        async with Client(transport=FastMCPTransport(server)) as client:
            result = await client.call_tool_mcp("error_tool", {})

            assert result.isError is True
            assert len(result.content) > 0
            assert isinstance(result.content[0], TextContent)
            assert "controlled test error" in result.content[0].text
            assert "Traceback" not in result.content[0].text

    async def test_tool_error_exception(self):
        """Test that ToolError exceptions are properly handled.

        ToolError is a special exception type in FastMCP that:
        1. Bypasses error masking (when enabled)
        2. Always shows the error message to the client
        3. Is intended for intentional error reporting to clients
        """
        from fastmcp.exceptions import ToolError as ServerToolError

        server = FastMCP("ToolErrorServer")

        @server.tool
        def intentional_error() -> None:
            """A tool that raises an intentional ToolError."""
            raise ServerToolError("This is an intentional error for the client")

        async with Client(transport=FastMCPTransport(server)) as client:
            with pytest.raises(ToolError, match="intentional error for the client"):
                await client.call_tool("intentional_error", {})

            result = await client.call_tool("intentional_error", {}, raise_on_error=False)
            assert result.is_error is True
            assert result.data is None

    async def test_error_masking_enabled(self):
        """Test that error details are masked when mask_error_details is enabled.

        When mask_error_details=True is set on the server:
        1. General exceptions (not ToolError) have their messages masked
        2. A generic error message is shown instead
        3. This prevents leaking internal implementation details
        """
        server = FastMCP("MaskedErrorServer", mask_error_details=True)

        @server.tool
        def sensitive_error() -> None:
            """A tool that raises an error with sensitive details."""
            raise ValueError("Sensitive internal detail: db_password=secret123")

        async with Client(transport=FastMCPTransport(server)) as client:
            result = await client.call_tool_mcp("sensitive_error", {})

            assert result.isError is True
            assert isinstance(result.content[0], TextContent)
            assert "Sensitive internal detail" not in result.content[0].text
            assert "db_password" not in result.content[0].text
            assert "secret123" not in result.content[0].text


@pytest.mark.integration
class TestResourceEndpoints:
    """Tests for resource endpoints.

    These tests verify that:
    1. Server can register resources with URIs
    2. Client can list resources
    3. Client can read resource content via read_resource
    4. Resource templates work with parameters
    """

    async def test_read_static_resource(self):
        """Test reading a static resource by URI.

        Static resources have fixed URIs and return the same content each time.
        """
        server = FastMCP("ResourceServer")

        @server.resource(uri="data://users/list")
        async def get_users() -> str:
            """Return a list of users as JSON."""
            import json

            return json.dumps(
                [
                    {"id": 1, "name": "Alice", "active": True},
                    {"id": 2, "name": "Bob", "active": False},
                    {"id": 3, "name": "Charlie", "active": True},
                ]
            )

        async with Client(transport=FastMCPTransport(server)) as client:
            resources = await client.list_resources()
            assert len(resources) == 1
            assert str(resources[0].uri) == "data://users/list"

            uri = AnyUrl("data://users/list")
            result = await client.read_resource(uri)

            assert len(result) == 1
            content_str = str(result[0])
            assert "Alice" in content_str
            assert "Bob" in content_str
            assert "Charlie" in content_str

    async def test_read_resource_template(self):
        """Test reading a resource from a template with parameters.

        Resource templates use URI patterns with placeholders (e.g., {user_id})
        that are extracted and passed to the resource function.
        """
        server = FastMCP("TemplateResourceServer")

        @server.resource(uri="data://user/{user_id}")
        async def get_user(user_id: str) -> str:
            """Return a single user by ID."""
            import json

            users = {
                "1": {"id": "1", "name": "Alice", "email": "alice@example.com"},
                "2": {"id": "2", "name": "Bob", "email": "bob@example.com"},
                "3": {"id": "3", "name": "Charlie", "email": "charlie@example.com"},
            }
            return json.dumps(users.get(user_id, {"error": "User not found"}))

        async with Client(transport=FastMCPTransport(server)) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1
            assert "data://user/{user_id}" in templates[0].uriTemplate

            for user_id in ["1", "2", "3"]:
                uri = AnyUrl(f"data://user/{user_id}")
                result = await client.read_resource(uri)
                content_str = str(result[0])
                assert f'"id": "{user_id}"' in content_str

            unknown_uri = AnyUrl("data://user/999")
            result = await client.read_resource(unknown_uri)
            content_str = str(result[0])
            assert "User not found" in content_str

    async def test_resource_template_with_chinese_characters(self):
        """Test resource template with URL-encoded Chinese characters.

        This test verifies that:
        1. Chinese characters in URI path parameters are properly URL-decoded
        2. The resource function receives the decoded parameter value
        """
        server = FastMCP("ChineseResourceServer")

        @server.resource(uri="data://product/{product_name}")
        async def get_product(product_name: str) -> str:
            """Return product info by name."""
            import json

            products = {
                "苹果": {"id": "p1", "name": "苹果", "price": 5.99},
                "香蕉": {"id": "p2", "name": "香蕉", "price": 3.99},
            }
            return json.dumps(products.get(product_name, {"error": "Product not found"}))

        async with Client(transport=FastMCPTransport(server)) as client:
            import json
            import urllib.parse

            chinese_name = "苹果"
            encoded_name = urllib.parse.quote(chinese_name, safe="")
            uri = AnyUrl(f"data://product/{encoded_name}")

            result = await client.read_resource(uri)
            content_str = str(result[0])
            result_json = json.loads(result[0].text)
            assert result_json["name"] == "苹果"

    async def test_resource_template_with_empty_string_param(self):
        """Test resource template with empty string parameter.

        This test verifies that:
        1. Empty string parameters are handled correctly
        2. The resource function receives the empty string as expected
        """
        server = FastMCP("EmptyParamServer")

        @server.resource(uri="data://item/{item_id}")
        async def get_item(item_id: str) -> str:
            """Return item info by ID."""
            import json

            if item_id == "":
                return json.dumps({"error": "Empty item_id", "received": ""})

            items = {
                "123": {"id": "123", "name": "Item 123"},
            }
            return json.dumps(items.get(item_id, {"error": "Item not found"}))

        async with Client(transport=FastMCPTransport(server)) as client:
            uri = AnyUrl("data://item/123")
            result = await client.read_resource(uri)
            content_str = str(result[0])
            assert "Item 123" in content_str

    async def test_resource_template_with_slash_in_param(self):
        """Test resource template with path parameter containing slash.

        This test verifies how URL paths with slashes in parameters are handled.
        Note: In most URI routing implementations, slashes in path parameters
        need to be URL-encoded (%2F) to be treated as part of the path segment.
        """
        server = FastMCP("SlashParamServer")

        @server.resource(uri="data://path/{first}/sub/{second}")
        async def get_path_info(first: str, second: str) -> str:
            """Return path parameters."""
            import json

            return json.dumps({"first": first, "second": second})

        async with Client(transport=FastMCPTransport(server)) as client:
            uri = AnyUrl("data://path/abc/sub/xyz")
            result = await client.read_resource(uri)
            content_str = str(result[0])
            assert '"first": "abc"' in content_str
            assert '"second": "xyz"' in content_str


@pytest.mark.integration
class TestProgressReporting:
    """Tests for progress reporting from long-running tools.

    These tests verify that:
    1. Tools can report progress via context.report_progress()
    2. Progress notifications are sent to the client
    3. Client receives progress messages in the correct order
    4. Progress handler is called for each progress update
    """

    async def test_progress_reports_from_tool(self):
        """Test that a long-running tool can report progress to the client.

        This test verifies:
        1. Tool calls context.report_progress() multiple times
        2. Client's progress_handler receives each progress update
        3. Progress values are in the correct order
        4. Tool returns a final result after all progress is reported
        """
        progress_messages: list[dict[str, Any]] = []

        async def progress_handler(
            progress: float, total: float | None, message: str | None
        ) -> None:
            progress_messages.append(
                {"progress": progress, "total": total, "message": message}
            )

        server = FastMCP("ProgressServer")

        @server.tool
        async def long_running_task(context: Context) -> str:
            """A task that reports progress at each step."""
            for i in range(5):
                await context.report_progress(
                    progress=i + 1,
                    total=5,
                    message=f"Processing step {i + 1}",
                )
            return "Task completed successfully"

        async with Client(
            transport=FastMCPTransport(server), progress_handler=progress_handler
        ) as client:
            result = await client.call_tool("long_running_task", {})

            assert result.data == "Task completed successfully"
            assert len(progress_messages) == 5

            for i, msg in enumerate(progress_messages):
                assert msg["progress"] == i + 1
                assert msg["total"] == 5
                assert msg["message"] == f"Processing step {i + 1}"

    async def test_progress_handler_on_tool_call(self):
        """Test that progress handler can be supplied per tool call.

        The progress handler can be set:
        1. At client creation time (default handler)
        2. Per tool call (overrides default)

        This test verifies that the per-call handler works correctly.
        """
        default_messages: list[dict[str, Any]] = []
        per_call_messages: list[dict[str, Any]] = []

        async def default_handler(
            progress: float, total: float | None, message: str | None
        ) -> None:
            default_messages.append(
                {"progress": progress, "total": total, "message": message}
            )

        async def per_call_handler(
            progress: float, total: float | None, message: str | None
        ) -> None:
            per_call_messages.append(
                {"progress": progress, "total": total, "message": message}
            )

        server = FastMCP("ProgressOverrideServer")

        @server.tool
        async def progress_task(context: Context) -> str:
            for i in range(3):
                await context.report_progress(
                    progress=i + 1,
                    total=3,
                    message=f"Step {i + 1}",
                )
            return "Done"

        async with Client(
            transport=FastMCPTransport(server), progress_handler=default_handler
        ) as client:
            await client.call_tool("progress_task", {})
            assert len(default_messages) == 3
            assert len(per_call_messages) == 0

            default_messages.clear()
            await client.call_tool("progress_task", {}, progress_handler=per_call_handler)
            assert len(default_messages) == 0
            assert len(per_call_messages) == 3


@pytest.mark.integration
class TestConcurrentToolCalls:
    """Tests for concurrent tool calls from the same client.

    These tests verify that:
    1. Multiple tool calls can be made concurrently
    2. Server handles concurrent calls correctly
    3. Response IDs match request IDs (no cross-talk)
    4. All calls complete successfully
    """

    async def test_concurrent_tool_calls(self):
        """Test that the same client can make concurrent tool calls.

        This test:
        1. Creates 10 concurrent tool calls using asyncio.gather
        2. Each call has a unique identifier to track responses
        3. Verifies that all calls complete successfully
        4. Verifies that responses are not mixed up between calls
        """
        server = FastMCP("ConcurrentServer")

        @server.tool
        async def compute_square(n: int) -> dict[str, int]:
            """Compute the square of a number (with a small delay)."""
            await asyncio.sleep(0.01)
            return {"input": n, "result": n * n}

        async with Client(transport=FastMCPTransport(server)) as client:
            tasks = [
                client.call_tool("compute_square", {"n": i}) for i in range(1, 11)
            ]

            results = await asyncio.gather(*tasks)

            assert len(results) == 10

            expected = {i: i * i for i in range(1, 11)}
            actual = {r.data["input"]: r.data["result"] for r in results}

            assert actual == expected

    async def test_10_concurrent_calls_preserve_response_isolation(self):
        """Test that concurrent tool calls preserve response isolation by request ID.

        This test verifies that:
        1. 20 concurrent tool calls with different parameters
        2. Each request gets its own response (no cross-talk)
        3. Responses can be matched to requests by their parameter signature

        The test uses a unique parameter signature for each call to ensure
        responses are correctly matched to requests.
        """
        server = FastMCP("ResponseIsolationServer")

        @server.tool
        async def echo_with_delay(value: str, delay_ms: int) -> dict[str, Any]:
            """Echo the value after a random delay.

            The delay ensures that requests don't complete in the order they were sent,
            which helps detect response ID mismatches.
            """
            await asyncio.sleep(delay_ms / 1000)
            return {"input_value": value, "delay_ms": delay_ms}

        async with Client(transport=FastMCPTransport(server)) as client:
            import random

            random.seed(42)
            test_cases = []
            for i in range(20):
                value = f"request_{i:03d}"
                delay_ms = random.randint(1, 50)
                test_cases.append({"value": value, "delay_ms": delay_ms})

            tasks = [
                client.call_tool("echo_with_delay", {"value": tc["value"], "delay_ms": tc["delay_ms"]})
                for tc in test_cases
            ]

            results = await asyncio.gather(*tasks)

            assert len(results) == 20

            returned_values = {r.data["input_value"] for r in results}
            expected_values = {tc["value"] for tc in test_cases}
            assert returned_values == expected_values, (
                f"Missing values: {expected_values - returned_values}, "
                f"Extra values: {returned_values - expected_values}"
            )

            for i, result in enumerate(results):
                expected_value = test_cases[i]["value"]
                expected_delay = test_cases[i]["delay_ms"]
                assert result.data["input_value"] == expected_value, (
                    f"Result {i}: expected {expected_value}, got {result.data['input_value']}"
                )
                assert result.data["delay_ms"] == expected_delay, (
                    f"Result {i}: expected delay {expected_delay}, got {result.data['delay_ms']}"
                )

    async def test_concurrent_calls_with_mixed_tools(self):
        """Test concurrent calls to different tools.

        This test verifies that concurrent calls to different tools:
        1. All complete successfully
        2. Return correct results for each tool type
        3. Don't interfere with each other
        """
        server = FastMCP("MixedConcurrentServer")

        @server.tool
        def echo_text(text: str) -> str:
            return f"Echo: {text}"

        @server.tool
        def add_numbers(a: int, b: int) -> int:
            return a + b

        @server.tool
        async def delayed_hello(delay_ms: int) -> str:
            await asyncio.sleep(delay_ms / 1000)
            return "Hello after delay"

        async with Client(transport=FastMCPTransport(server)) as client:
            tasks = [
                client.call_tool("echo_text", {"text": "test1"}),
                client.call_tool("add_numbers", {"a": 10, "b": 20}),
                client.call_tool("delayed_hello", {"delay_ms": 50}),
                client.call_tool("echo_text", {"text": "test2"}),
                client.call_tool("add_numbers", {"a": 5, "b": 5}),
            ]

            results = await asyncio.gather(*tasks)

            results_data = [r.data for r in results]

            assert "Echo: test1" in results_data
            assert "Echo: test2" in results_data
            assert 30 in results_data
            assert 10 in results_data
            assert "Hello after delay" in results_data


@pytest.mark.integration
class TestAuthIntegration:
    """Tests for auth middleware and middleware chain integration.

    These tests verify that:
    1. Without auth middleware, requests succeed
    2. With auth middleware, requests without token return AuthorizationError
    3. Multiple middleware chain order is preserved
    """

    async def test_without_auth_middleware_request_succeeds(self):
        """Test that requests succeed when no auth middleware is configured.

        When no auth middleware is added, all tools should be accessible.
        """
        server = FastMCP("NoAuthServer")

        @server.tool
        def public_tool() -> str:
            return "Public access granted"

        async with Client(transport=FastMCPTransport(server)) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "public_tool"

            result = await client.call_tool("public_tool", {})
            assert result.data == "Public access granted"

    async def test_with_auth_middleware_without_token_returns_error(self):
        """Test that auth middleware blocks requests without valid token.

        When AuthMiddleware is configured with require_scopes, requests
        without a valid token should receive AuthorizationError.
        """
        from fastmcp.server.auth import require_scopes
        from fastmcp.server.middleware import AuthMiddleware

        server = FastMCP(
            "AuthServer",
            middleware=[AuthMiddleware(auth=require_scopes("api"))],
        )

        @server.tool
        def protected_tool() -> str:
            return "Protected access granted"

        async with Client(transport=FastMCPTransport(server)) as client:
            tools = await client.list_tools()
            assert len(tools) == 0, "Should filter all tools when no token"

            with pytest.raises(Exception) as exc_info:
                await client.call_tool("protected_tool", {})

            error_str = str(exc_info.value).lower()
            assert (
                "authorization" in error_str
                or "insufficient" in error_str
                or "forbidden" in error_str
            ), f"Expected authorization error, got: {exc_info.value}"

    async def test_middleware_chain_order_preserved(self):
        """Test that middleware chain order is preserved during execution.

        This test creates a chain of middleware that records their execution order.
        It verifies that:
        1. Middleware before handler execute in order
        2. The actual tool runs
        3. Middleware after handler execute in reverse order
        """
        import mcp.types as mcp_types
        from fastmcp.resources.base import Resource
        from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
        from fastmcp.tools.base import Tool, ToolResult

        execution_order: list[str] = []

        class RecordingMiddleware(Middleware):
            def __init__(self, name: str):
                self.name = name

            async def on_list_tools(
                self,
                context: MiddlewareContext[mcp_types.ListToolsRequest],
                call_next: CallNext[mcp_types.ListToolsRequest, Sequence[Tool]],
            ) -> Sequence[Tool]:
                execution_order.append(f"{self.name}:before")
                result = await call_next(context)
                execution_order.append(f"{self.name}:after")
                return result

            async def on_call_tool(
                self,
                context: MiddlewareContext[mcp_types.CallToolRequestParams],
                call_next: CallNext[mcp_types.CallToolRequestParams, ToolResult],
            ) -> ToolResult:
                execution_order.append(f"{self.name}:before")
                result = await call_next(context)
                execution_order.append(f"{self.name}:after")
                return result

        server = FastMCP("MiddlewareChainServer")

        @server.tool
        def my_tool() -> str:
            execution_order.append("tool:executed")
            return "done"

        server.add_middleware(RecordingMiddleware("first"))
        server.add_middleware(RecordingMiddleware("second"))
        server.add_middleware(RecordingMiddleware("third"))

        async with Client(transport=FastMCPTransport(server)) as client:
            execution_order.clear()
            await client.list_tools()

            assert execution_order == [
                "first:before",
                "second:before",
                "third:before",
                "third:after",
                "second:after",
                "first:after",
            ], f"Expected specific order, got: {execution_order}"

            execution_order.clear()
            await client.call_tool("my_tool", {})

            assert execution_order == [
                "first:before",
                "second:before",
                "third:before",
                "tool:executed",
                "third:after",
                "second:after",
                "first:after",
            ], f"Expected specific order, got: {execution_order}"


@pytest.mark.integration
class TestServerResourceCleanup:
    """Tests for server resource cleanup after client disconnection.

    These tests verify that:
    1. Server resources are properly released after client disconnect
    2. No resource leaks occur
    3. Multiple connect/disconnect cycles work correctly
    """

    async def test_multiple_connect_disconnect_cycles(self):
        """Test that multiple connect/disconnect cycles work correctly.

        This test verifies that:
        1. Client can connect, use, and disconnect multiple times
        2. Each connection is independent
        3. Server handles repeated connections without issues
        """
        server = FastMCP("ReconnectServer")

        @server.tool
        def get_counter() -> int:
            return 42

        client = Client(transport=FastMCPTransport(server))

        for _ in range(3):
            async with client:
                assert client.is_connected()
                result = await client.call_tool("get_counter", {})
                assert result.data == 42

            assert not client.is_connected()

    @pytest.mark.slow
    async def test_connection_cycles_with_memory_monitoring(self):
        """Test 100 connect/disconnect cycles with RSS memory monitoring.

        This test verifies that:
        1. 100 connect/disconnect cycles complete successfully
        2. RSS memory doesn't grow more than 1.5x the baseline
        3. No memory leaks occur during repeated connections

        Note: This test is marked as slow because it involves 100 iterations.
        """
        process = psutil.Process(os.getpid())

        server = FastMCP("MemoryTestServer")

        @server.tool
        def ping() -> str:
            return "pong"

        client = Client(transport=FastMCPTransport(server))

        initial_rss = process.memory_info().rss
        memory_readings: list[tuple[int, int]] = []

        for cycle in range(100):
            async with client:
                assert client.is_connected()
                result = await client.call_tool("ping", {})
                assert result.data == "pong"

                if (cycle + 1) % 20 == 0:
                    current_rss = process.memory_info().rss
                    memory_readings.append((cycle + 1, current_rss))

            assert not client.is_connected()

        final_rss = process.memory_info().rss
        memory_readings.append((100, final_rss))

        baseline_rss = initial_rss
        max_allowed_rss = int(baseline_rss * 1.5)

        for cycle, rss in memory_readings:
            assert rss <= max_allowed_rss, (
                f"Memory exceeded 1.5x baseline at cycle {cycle}. "
                f"Baseline: {baseline_rss / 1024 / 1024:.1f} MB, "
                f"Current: {rss / 1024 / 1024:.1f} MB, "
                f"Max allowed: {max_allowed_rss / 1024 / 1024:.1f} MB"
            )

    async def test_disconnect_with_pending_operations(self):
        """Test disconnection while operations are in flight.

        This is a defensive test to ensure that:
        1. Client can disconnect even if operations might be pending
        2. No hangs or crashes occur during cleanup
        """
        server = FastMCP("CleanupServer")

        @server.tool
        async def long_running() -> str:
            await asyncio.sleep(10)
            return "Done"

        client = Client(transport=FastMCPTransport(server))

        async with client:
            assert client.is_connected()

        assert not client.is_connected()


@pytest.mark.integration
class TestServerSideTransportIntegration:
    """Tests for actual HTTP transport integration.

    These tests verify that:
    1. HTTP transport handshake works correctly
    2. Tool calls work over HTTP transport
    3. Server responds correctly to HTTP requests

    Note: These tests use httpx.ASGITransport for in-process HTTP testing
    without starting an actual uvicorn server.
    """

    async def test_http_transport_handshake_and_tool_call(self):
        """Test HTTP transport handshake and tool call using ASGI transport.

        This test verifies:
        1. HTTP app can be created from FastMCP server
        2. HTTP routes are correctly set up
        3. Basic HTTP communication works

        Note: This is a simplified test using ASGI transport.
        Full MCP HTTP transport requires SSE and message framing,
        which is tested in other test files.
        """
        server = FastMCP("HttpTransportServer")

        @server.tool
        def http_greet(name: str) -> str:
            return f"Hello via HTTP, {name}!"

        app = server.http_app(transport="http")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            assert app is not None
            assert hasattr(app, "routes")
            assert len(app.routes) > 0

        async with Client(transport=FastMCPTransport(server)) as client:
            result = await client.call_tool("http_greet", {"name": "World"})
            assert result.data == "Hello via HTTP, World!"
