from collections.abc import Generator
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import fastmcp.client.auth.oauth  # Import module, not the function directly
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import run_server_in_process


def fastmcp_server(issuer_url: str):
    """Create a FastMCP server with OAuth authentication."""
    server = FastMCP(
        "TestServer",
        auth=InMemoryOAuthProvider(
            issuer_url=issuer_url,
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


class HeadlessOAuthProvider(httpx.Auth):
    """
    OAuth provider that bypasses browser interaction for testing.

    This simulates the complete OAuth flow programmatically by:
    1. Discovering OAuth metadata from the server
    2. Registering a client
    3. Getting an authorization code (simulates user approval)
    4. Exchanging it for an access token
    5. Adding Bearer token to all requests

    This enables testing OAuth-protected FastMCP servers without
    requiring browser interaction or external OAuth providers.
    """

    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url
        parsed_url = urlparse(mcp_url)
        self.server_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        self._access_token = None

    async def async_auth_flow(self, request):
        """httpx.Auth interface - add Bearer token to requests."""
        if not self._access_token:
            await self._obtain_token()

        if self._access_token:
            request.headers["Authorization"] = f"Bearer {self._access_token}"

        yield request

    async def _obtain_token(self):
        """Get a valid access token by simulating the OAuth flow."""
        import base64
        import hashlib
        import secrets

        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        # Generate PKCE challenge/verifier
        code_verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        )
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        # Create HTTP client to talk to the server
        async with httpx.AsyncClient() as http_client:
            # 1. Discover OAuth metadata
            metadata_url = (
                f"{self.server_base_url}/.well-known/oauth-authorization-server"
            )
            response = await http_client.get(metadata_url)
            response.raise_for_status()
            metadata = response.json()

            # 2. Register a client
            client_info = OAuthClientInformationFull(
                client_id="test_client_headless",
                client_secret="test_secret_headless",
                redirect_uris=[AnyHttpUrl("http://localhost:8080/callback")],
            )

            register_response = await http_client.post(
                metadata["registration_endpoint"],
                json=client_info.model_dump(mode="json"),
            )
            register_response.raise_for_status()
            registered_client = register_response.json()

            # 3. Get authorization code (simulate user approval)
            auth_params = {
                "response_type": "code",
                "client_id": registered_client["client_id"],
                "redirect_uri": "http://localhost:8080/callback",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": "test_state_headless",
            }

            auth_response = await http_client.get(
                metadata["authorization_endpoint"],
                params=auth_params,
                follow_redirects=False,
            )

            # Extract auth code from redirect
            if auth_response.status_code == 302:
                redirect_url = auth_response.headers["location"]
                parsed = urlparse(redirect_url)
                query_params = parse_qs(parsed.query)

                if "error" in query_params:
                    error = query_params["error"][0]
                    error_desc = query_params.get(
                        "error_description", ["Unknown error"]
                    )[0]
                    raise RuntimeError(
                        f"OAuth authorization failed: {error} - {error_desc}"
                    )

                auth_code = query_params["code"][0]

                # 4. Exchange auth code for access token
                token_data = {
                    "grant_type": "authorization_code",
                    "client_id": registered_client["client_id"],
                    "client_secret": registered_client["client_secret"],
                    "code": auth_code,
                    "redirect_uri": "http://localhost:8080/callback",
                    "code_verifier": code_verifier,
                }

                token_response = await http_client.post(
                    metadata["token_endpoint"], data=token_data
                )
                token_response.raise_for_status()
                token_info = token_response.json()

                self._access_token = token_info["access_token"]
            else:
                raise RuntimeError(f"Authorization failed: {auth_response.status_code}")


@pytest.fixture()
def client_with_headless_oauth(
    streamable_http_server: str,
) -> Generator[Client, None, None]:
    """Client with headless OAuth that bypasses browser interaction."""

    # Patch the OAuth function to return our headless provider
    def headless_oauth(*args, **kwargs):
        mcp_url = args[0] if args else kwargs.get("mcp_url", "")
        if not mcp_url:
            raise ValueError("mcp_url is required")
        return HeadlessOAuthProvider(mcp_url)

    with patch("fastmcp.client.auth.oauth.OAuth", side_effect=headless_oauth):
        client = Client(
            transport=StreamableHttpTransport(streamable_http_server),
            auth=fastmcp.client.auth.oauth.OAuth(mcp_url=streamable_http_server),
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
        assert result[0].text == "8"  # type: ignore[attr-defined]


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
