from dataclasses import dataclass

import pytest
from anyio import create_task_group
from mcp.types import (
    ElicitRequestFormParams,
    LoggingLevel,
    ModelHint,
    ModelPreferences,
    TextContent,
)
from pydantic import BaseModel, Field

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitRequestParams, ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.providers.proxy import ProxyClient


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP("TestServer")

    @mcp.tool(tags={"echo"})
    def echo(message: str) -> str:
        return f"echo: {message}"

    @mcp.tool
    async def list_roots(context: Context) -> list[str]:
        roots = await context.list_roots()
        return [str(r.uri) for r in roots]

    @mcp.tool
    async def sampling(
        context: Context,
    ) -> str:
        result = await context.sample(
            "Hello, world!",
            system_prompt="You love FastMCP",
            temperature=0.5,
            max_tokens=100,
            model_preferences="gpt-4o",
        )
        return result.text or ""

    @dataclass
    class Person:
        name: str

    @mcp.tool
    async def elicit(context: Context) -> str:
        result = await context.elicit(
            message="What is your name?",
            response_type=Person,
        )

        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, Person)
            return f"Hello, {result.data.name}!"
        else:
            return "No name provided."

    @mcp.tool
    async def log(
        message: str, level: LoggingLevel, logger: str, context: Context
    ) -> None:
        await context.log(message=message, level=level, logger_name=logger)

    @mcp.tool
    async def report_progress(context: Context) -> int:
        for i in range(3):
            await context.report_progress(
                progress=i + 1,
                total=3,
                message=f"{(i + 1) / 3 * 100:.2f}% complete",
            )
        return 100

    return mcp


@pytest.fixture
async def proxy_server(fastmcp_server: FastMCP):
    """
    A proxy server that forwards interactions with the proxy client to the given fastmcp server.
    """
    return FastMCP.as_proxy(ProxyClient(fastmcp_server))


class TestProxyClient:
    async def test_forward_tool_meta(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `echo` tool meta.
        """
        async with Client(proxy_server) as client:
            tools = await client.list_tools()
            echo_tool = next(t for t in tools if t.name == "echo")
            assert echo_tool.meta == {"fastmcp": {"tags": ["echo"]}}

    async def test_forward_error_response(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards an error response.
        """
        async with Client(proxy_server) as client:
            with pytest.raises(ToolError, match="Elicitation not supported"):
                await client.call_tool("elicit", {})

    async def test_forward_list_roots_request(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `list_roots` request.
        """
        roots_handler_called = False

        async def roots_handler(ctx: RequestContext):
            nonlocal roots_handler_called
            roots_handler_called = True
            return []

        async with Client(proxy_server, roots=roots_handler) as client:
            await client.call_tool("list_roots", {})

        assert roots_handler_called

    async def test_forward_list_roots_response(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `list_roots` response.
        """
        async with Client(proxy_server, roots=["file://x/y/z"]) as client:
            result = await client.call_tool("list_roots", {})
            assert result.data == ["file://x/y/z"]

    async def test_forward_sampling_request(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `sampling` request.
        """
        sampling_handler_called = False

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> str:
            nonlocal sampling_handler_called
            sampling_handler_called = True
            assert messages == [
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text="Hello, world!"),
                )
            ]
            assert params.systemPrompt == "You love FastMCP"
            assert params.temperature == 0.5
            assert params.maxTokens == 100
            assert params.modelPreferences == ModelPreferences(
                hints=[ModelHint(name="gpt-4o")]
            )
            return ""

        async with Client(proxy_server, sampling_handler=sampling_handler) as client:
            await client.call_tool("sampling", {})

        assert sampling_handler_called

    async def test_forward_sampling_response(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `sampling` response.
        """
        async with Client(
            proxy_server, sampling_handler=lambda *args: "I love FastMCP"
        ) as client:
            result = await client.call_tool("sampling", {})
            assert result.data == "I love FastMCP"

    async def test_elicit_request(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `elicit` request.
        """
        elicitation_handler_called = False

        async def elicitation_handler(
            message, response_type, params: ElicitRequestParams, ctx
        ):
            nonlocal elicitation_handler_called
            elicitation_handler_called = True
            assert message == "What is your name?"
            assert "Person" in str(response_type)
            assert isinstance(params, ElicitRequestFormParams)
            assert params.requestedSchema == {
                "title": "Person",
                "type": "object",
                "properties": {"name": {"title": "Name", "type": "string"}},
                "required": ["name"],
            }
            return ElicitResult(action="accept", content=response_type(name="Alice"))

        async with Client(
            proxy_server, elicitation_handler=elicitation_handler
        ) as client:
            await client.call_tool("elicit", {})

        assert elicitation_handler_called

    async def test_elicit_accept_response(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `elicit` accept response.
        """

        async def elicitation_handler(
            message, response_type, params: ElicitRequestParams, ctx
        ):
            return ElicitResult(action="accept", content=response_type(name="Alice"))

        async with Client(
            proxy_server,
            elicitation_handler=elicitation_handler,
        ) as client:
            result = await client.call_tool("elicit", {})
            assert result.data == "Hello, Alice!"

    async def test_elicit_decline_response(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `elicit` decline response.
        """

        async def elicitation_handler(
            message, response_type, params: ElicitRequestParams, ctx
        ):
            return ElicitResult(action="decline")

        async with Client(
            proxy_server, elicitation_handler=elicitation_handler
        ) as client:
            result = await client.call_tool("elicit", {})
            assert result.data == "No name provided."

    async def test_log_request(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `log` request.
        """
        log_handler_called = False

        async def log_handler(message: LogMessage) -> None:
            nonlocal log_handler_called
            log_handler_called = True
            assert message.data == "Hello, world!"
            assert message.level == "info"
            assert message.logger == "test"

        async with Client(proxy_server, log_handler=log_handler) as client:
            await client.call_tool(
                "log", {"message": "Hello, world!", "level": "info", "logger": "test"}
            )

        assert log_handler_called

    async def test_report_progress_request(self, proxy_server: FastMCP):
        """
        Test that the proxy client correctly forwards the `report_progress` request.
        """

        EXPECTED_PROGRESS_MESSAGES = [
            dict(progress=1, total=3, message="33.33% complete"),
            dict(progress=2, total=3, message="66.67% complete"),
            dict(progress=3, total=3, message="100.00% complete"),
        ]
        PROGRESS_MESSAGES = []

        async def progress_handler(
            progress: float, total: float | None, message: str | None
        ) -> None:
            PROGRESS_MESSAGES.append(
                dict(progress=progress, total=total, message=message)
            )

        async with Client(proxy_server, progress_handler=progress_handler) as client:
            await client.call_tool("report_progress", {})

        assert PROGRESS_MESSAGES == EXPECTED_PROGRESS_MESSAGES

    async def test_concurrent_log_requests_no_mixing(self, proxy_server: FastMCP):
        """Test that concurrent log requests don't mix handlers (fixes #1068)."""
        results: dict[str, LogMessage] = {}

        async def log_handler_a(message: LogMessage) -> None:
            results["logger_a"] = message

        async def log_handler_b(message: LogMessage) -> None:
            results["logger_b"] = message

        async with (
            Client(proxy_server, log_handler=log_handler_a) as client_a,
            Client(proxy_server, log_handler=log_handler_b) as client_b,
        ):
            async with create_task_group() as tg:
                tg.start_soon(
                    client_a.call_tool,
                    "log",
                    {"message": "Hello, world!", "level": "info", "logger": "a"},
                )
                tg.start_soon(
                    client_b.call_tool,
                    "log",
                    {"message": "Hello, world!", "level": "info", "logger": "b"},
                )

        assert results["logger_a"].logger == "a"
        assert results["logger_b"].logger == "b"

    async def test_concurrent_elicitation_no_mixing(self, proxy_server: FastMCP):
        """Test that concurrent elicitation requests don't mix handlers (fixes #1068)."""
        results = {}

        async def elicitation_handler_a(
            message: str,
            response_type: type,
            params: ElicitRequestParams,
            ctx: RequestContext,
        ) -> ElicitResult:
            return ElicitResult(action="accept", content=response_type(name="Alice"))

        async def elicitation_handler_b(
            message: str,
            response_type: type,
            params: ElicitRequestParams,
            ctx: RequestContext,
        ) -> ElicitResult:
            return ElicitResult(action="accept", content=response_type(name="Bob"))

        async def get_and_store(name, coro):
            result = await coro
            results[name] = result.data

        async with (
            Client(proxy_server, elicitation_handler=elicitation_handler_a) as client_a,
            Client(proxy_server, elicitation_handler=elicitation_handler_b) as client_b,
        ):
            async with create_task_group() as tg:
                tg.start_soon(
                    get_and_store,
                    "elicitation_a",
                    client_a.call_tool("elicit", {}),
                )
                tg.start_soon(
                    get_and_store,
                    "elicitation_b",
                    client_b.call_tool("elicit", {}),
                )

        assert results["elicitation_a"] == "Hello, Alice!"
        assert results["elicitation_b"] == "Hello, Bob!"

    async def test_elicit_with_default_values(self, fastmcp_server: FastMCP):
        """
        Test that the proxy client correctly handles elicitation with default values (fixes #1167).
        """

        @fastmcp_server.tool
        async def elicit_with_defaults(context: Context) -> str:
            class TestModel(BaseModel):
                content: str = Field(description="Your reply content")
                acknowledge: bool = Field(
                    default=False, description="Send immediately or save as draft"
                )

            result = await context.elicit(
                "Please provide input:", response_type=TestModel
            )

            if result.action == "accept":
                assert isinstance(result, AcceptedElicitation)
                assert isinstance(result.data, TestModel)
                return f"Content: {result.data.content}, Acknowledge: {result.data.acknowledge}"
            else:
                return f"Elicitation {result.action}"

        proxy_server = FastMCP.as_proxy(ProxyClient(fastmcp_server))

        # Test that elicitation works correctly through the proxy
        async def elicitation_handler(
            message: str,
            response_type: type,
            params: ElicitRequestParams,
            ctx: RequestContext,
        ):
            # Verify the schema is correct - acknowledge should have default=False, not be nullable
            assert isinstance(params, ElicitRequestFormParams)
            schema = params.requestedSchema
            assert schema["properties"]["acknowledge"]["type"] == "boolean"
            assert schema["properties"]["acknowledge"]["default"] is False

            return {"content": "Test content", "acknowledge": True}

        async with Client(
            proxy_server, elicitation_handler=elicitation_handler
        ) as client:
            result = await client.call_tool("elicit_with_defaults", {})
            assert result.data == "Content: Test content, Acknowledge: True"

    async def test_client_factory_creates_fresh_sessions(self, fastmcp_server: FastMCP):
        """Test that the client factory pattern creates fresh sessions for each request."""
        from fastmcp.server.providers.proxy import FastMCPProxy

        # Create a disconnected client (should use fresh sessions per request)
        base_client = Client(fastmcp_server)

        # Test both as_proxy convenience method and direct client_factory usage
        proxy_via_as_proxy = FastMCP.as_proxy(base_client)
        proxy_via_factory = FastMCPProxy(client_factory=base_client.new)

        # Verify the proxies are created successfully - this tests the client factory pattern
        assert proxy_via_as_proxy is not None
        assert proxy_via_factory is not None

        # Verify they have the expected client factory behavior
        assert hasattr(proxy_via_as_proxy, "_local_provider")
        assert hasattr(proxy_via_factory, "_local_provider")

    async def test_connected_client_reuses_sessions(self, fastmcp_server: FastMCP):
        """Test that connected clients passed to as_proxy reuse sessions (preserves #959 behavior)."""
        # Create a connected client (should reuse sessions)
        async with Client(fastmcp_server) as connected_client:
            proxy = FastMCP.as_proxy(connected_client)

            # Verify the proxy is created successfully and uses session reuse
            assert proxy is not None
            assert hasattr(proxy, "_local_provider")
