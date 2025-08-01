from collections.abc import Generator
from urllib.parse import urlparse

import httpx
import pytest

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import HeadlessOAuth, run_server_in_process


def fastmcp_server(issuer_url: str):
    """Create a FastMCP server with OAuth authentication."""
    server = FastMCP(
        "TestServer",
        auth=InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
    )

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.resource("resource://test")
    def get_test_resource() -> str:
        """Get a test resource."""
        return "Hello from authenticated resource!"

    return server


def run_server(host: str, port: int, **kwargs) -> None:
    fastmcp_server(f"http://{host}:{port}").run(host=host, port=port, **kwargs)


@pytest.fixture(scope="module")
def streamable_http_server() -> Generator[str, None, None]:
    with run_server_in_process(run_server, transport="http") as url:
        yield f"{url}/mcp/"


@pytest.fixture()
def client_unauthorized(streamable_http_server: str) -> Client:
    return Client(transport=StreamableHttpTransport(streamable_http_server))


@pytest.fixture()
def client_with_headless_oauth(
    streamable_http_server: str,
) -> Generator[Client, None, None]:
    """Client with headless OAuth that bypasses browser interaction."""
    client = Client(
        transport=StreamableHttpTransport(streamable_http_server),
        auth=HeadlessOAuth(mcp_url=streamable_http_server),
    )
    yield client


async def test_unauthorized(client_unauthorized: Client):
    """Test that unauthenticated requests are rejected."""
    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        async with client_unauthorized:
            pass


async def test_ping(client_with_headless_oauth: Client):
    """Test that we can ping the server."""
    async with client_with_headless_oauth:
        assert await client_with_headless_oauth.ping()


async def test_list_tools(client_with_headless_oauth: Client):
    """Test that we can list tools."""
    async with client_with_headless_oauth:
        tools = await client_with_headless_oauth.list_tools()
        tool_names = [tool.name for tool in tools]
        assert "add" in tool_names


async def test_call_tool(client_with_headless_oauth: Client):
    """Test that we can call a tool."""
    async with client_with_headless_oauth:
        result = await client_with_headless_oauth.call_tool("add", {"a": 5, "b": 3})
        # The add tool returns int which gets wrapped as structured output
        # Client unwraps it and puts the actual int in the data field
        assert result.data == 8


async def test_list_resources(client_with_headless_oauth: Client):
    """Test that we can list resources."""
    async with client_with_headless_oauth:
        resources = await client_with_headless_oauth.list_resources()
        resource_uris = [str(resource.uri) for resource in resources]
        assert "resource://test" in resource_uris


async def test_read_resource(client_with_headless_oauth: Client):
    """Test that we can read a resource."""
    async with client_with_headless_oauth:
        resource = await client_with_headless_oauth.read_resource("resource://test")
        assert resource[0].text == "Hello from authenticated resource!"  # type: ignore[attr-defined]


async def test_oauth_server_metadata_discovery(streamable_http_server: str):
    """Test that we can discover OAuth metadata from the running server."""
    parsed_url = urlparse(streamable_http_server)
    server_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    async with httpx.AsyncClient() as client:
        # Test OAuth discovery endpoint
        metadata_url = f"{server_base_url}/.well-known/oauth-authorization-server"
        response = await client.get(metadata_url)
        assert response.status_code == 200

        metadata = response.json()
        assert "authorization_endpoint" in metadata
        assert "token_endpoint" in metadata
        assert "registration_endpoint" in metadata

        # The endpoints should be properly formed URLs
        assert metadata["authorization_endpoint"].startswith(server_base_url)
        assert metadata["token_endpoint"].startswith(server_base_url)
