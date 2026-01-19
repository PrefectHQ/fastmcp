import asyncio
import logging
import socket
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp.utilities.tests import temporary_settings

# Use SelectorEventLoop on Windows to avoid ProactorEventLoop crashes
# See: https://github.com/python/cpython/issues/116773
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_collection_modifyitems(items):
    """Automatically mark tests in integration_tests folder with 'integration' marker."""
    for item in items:
        # Check if the test is in the integration_tests folder
        if "integration_tests" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def import_rich_rule():
    # What a hack
    import rich.rule  # noqa: F401

    yield


@pytest.fixture(autouse=True)
def enable_fastmcp_logger_propagation(caplog):
    """Enable propagation on FastMCP root logger so caplog captures FastMCP log messages.

    FastMCP loggers have propagate=False by default, which prevents messages from
    reaching pytest's caplog handler (attached to root logger). This fixture
    temporarily enables propagation on the FastMCP root logger so FastMCP logs
    are captured in tests.
    """
    root_logger = logging.getLogger("fastmcp")
    original_propagate = root_logger.propagate
    root_logger.propagate = True

    yield

    root_logger.propagate = original_propagate


@pytest.fixture(autouse=True)
def isolate_settings_home(tmp_path: Path):
    """Ensure each test uses an isolated settings.home directory.

    This prevents SQLite database locking issues on Windows when multiple
    tests share the same DiskStore directory in settings.home / "oauth-proxy".
    """
    test_home = tmp_path / "fastmcp-test-home"
    test_home.mkdir(exist_ok=True)

    with temporary_settings(home=test_home):
        yield


def get_fn_name(fn: Callable[..., Any]) -> str:
    return fn.__name__  # ty: ignore[unresolved-attribute]


@pytest.fixture
def worker_id(request):
    """Get the xdist worker ID, or 'master' if not using xdist."""
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


@pytest.fixture
def free_port():
    """Get a free port for the test to use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture
def free_port_factory(worker_id):
    """Factory to get free ports that tracks used ports per test session."""
    used_ports = set()

    def get_port():
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                port = s.getsockname()[1]
                if port not in used_ports:
                    used_ports.add(port)
                    return port

    return get_port


@pytest.fixture(scope="session")
def otel_trace_provider() -> Generator[
    tuple[TracerProvider, InMemorySpanExporter], None, None
]:
    """Configure OTEL SDK with in-memory span exporter for testing.

    Session-scoped because TracerProvider can only be set once per process.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield provider, exporter


@pytest.fixture
def trace_exporter(
    otel_trace_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> Generator[InMemorySpanExporter, None, None]:
    """Get the span exporter and clear it between tests."""
    _, exporter = otel_trace_provider
    exporter.clear()
    yield exporter
    exporter.clear()


@pytest.fixture
def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    import asyncio
    import json

    from fastmcp import FastMCP

    server = FastMCP("TestServer")

    # Add a tool
    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    # Add a second tool
    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool
    async def sleep(seconds: float) -> str:
        """Sleep for a given number of seconds."""
        await asyncio.sleep(seconds)
        return f"Slept for {seconds} seconds"

    # Add a resource (return JSON string for proper typing)
    @server.resource(uri="data://users")
    async def get_users() -> str:
        return json.dumps(["Alice", "Bob", "Charlie"], separators=(",", ":"))

    # Add a resource template (return JSON string for proper typing)
    @server.resource(uri="data://user/{user_id}")
    async def get_user(user_id: str) -> str:
        return json.dumps(
            {"id": user_id, "name": f"User {user_id}", "active": True},
            separators=(",", ":"),
        )

    # Add a prompt
    @server.prompt
    def welcome(name: str) -> str:
        """Example greeting prompt."""
        return f"Welcome to FastMCP, {name}!"

    return server


@pytest.fixture
def tool_server():
    """Fixture that creates a FastMCP server with comprehensive tool set for provider tests."""
    import base64

    from mcp.types import (
        BlobResourceContents,
        EmbeddedResource,
        ImageContent,
        TextContent,
    )
    from pydantic import AnyUrl

    from fastmcp import FastMCP
    from fastmcp.utilities.types import Audio, File, Image

    mcp = FastMCP()

    @mcp.tool
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool
    def list_tool() -> list[str | int]:
        return ["x", 2]

    @mcp.tool
    def error_tool() -> None:
        raise ValueError("Test error")

    @mcp.tool
    def image_tool(path: str) -> Image:
        return Image(path)

    @mcp.tool
    def audio_tool(path: str) -> Audio:
        return Audio(path)

    @mcp.tool
    def file_tool(path: str) -> File:
        return File(path)

    @mcp.tool
    def mixed_content_tool() -> list[TextContent | ImageContent | EmbeddedResource]:
        return [
            TextContent(type="text", text="Hello"),
            ImageContent(type="image", data="abc", mimeType="application/octet-stream"),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    blob=base64.b64encode(b"abc").decode(),
                    mimeType="application/octet-stream",
                    uri=AnyUrl("file:///test.bin"),
                ),
            ),
        ]

    @mcp.tool(output_schema=None)
    def mixed_list_fn(image_path: str) -> list:
        return [
            "text message",
            Image(image_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_audio_list_fn(audio_path: str) -> list:
        return [
            "text message",
            Audio(audio_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_file_list_fn(file_path: str) -> list:
        return [
            "text message",
            File(file_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool
    def file_text_tool() -> File:
        return File(data=b"hello world", format="plain")

    return mcp


@pytest.fixture
def tagged_resources_server():
    """Fixture that creates a FastMCP server with tagged resources and templates."""
    import json

    from fastmcp import FastMCP

    server = FastMCP("TaggedResourcesServer")

    # Add a resource with tags
    @server.resource(
        uri="data://tagged", tags={"test", "metadata"}, description="A tagged resource"
    )
    async def get_tagged_data() -> str:
        return json.dumps({"type": "tagged_data"}, separators=(",", ":"))

    # Add a resource template with tags
    @server.resource(
        uri="template://{id}",
        tags={"template", "parameterized"},
        description="A tagged template",
    )
    async def get_template_data(id: str) -> str:
        return json.dumps({"id": id, "type": "template_data"}, separators=(",", ":"))

    return server
