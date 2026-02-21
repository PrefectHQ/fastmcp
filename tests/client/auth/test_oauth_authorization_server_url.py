"""Tests for OAuth authorization_server_url (skip 401 discovery)."""

from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestAuthorizationServerUrlInit:
    """authorization_server_url should be stored and accessible."""

    def test_stores_authorization_server_url(self):
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            authorization_server_url="https://auth.example.com",
        )
        assert oauth._authorization_server_url == "https://auth.example.com"

    def test_default_authorization_server_url_is_none(self):
        oauth = OAuth(mcp_url="https://example.com/mcp")
        assert oauth._authorization_server_url is None

    def test_combines_with_static_client(self):
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="my-client",
            client_secret="my-secret",
            authorization_server_url="https://auth.example.com",
        )
        assert oauth._authorization_server_url == "https://auth.example.com"
        assert oauth._static_client_info is not None
        assert oauth._static_client_info.client_id == "my-client"


class TestProactiveAuthE2E:
    """End-to-end tests: authorization_server_url skips the 401 round-trip."""

    async def test_proactive_auth_with_dcr(self):
        """When authorization_server_url is set, client skips 401 and uses DCR."""
        port = find_available_port()
        callback_port = find_available_port()
        issuer_url = f"http://127.0.0.1:{port}"

        provider = InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["read", "write"],
            ),
        )

        server = FastMCP("TestServer", auth=provider)

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with run_server_async(server, port=port, transport="http") as url:
            parsed = urlparse(url)
            auth_server_url = f"{parsed.scheme}://{parsed.netloc}"

            oauth = HeadlessOAuth(
                mcp_url=url,
                scopes=["read", "write"],
                callback_port=callback_port,
                authorization_server_url=auth_server_url,
            )

            async with Client(
                transport=StreamableHttpTransport(url),
                auth=oauth,
            ) as client:
                assert await client.ping()
                tools = await client.list_tools()
                assert any(t.name == "greet" for t in tools)

    async def test_proactive_auth_with_static_client(self):
        """authorization_server_url + static client_id skips 401 and DCR."""
        port = find_available_port()
        callback_port = find_available_port()
        issuer_url = f"http://127.0.0.1:{port}"

        provider = InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=False,
                valid_scopes=["read"],
            ),
        )

        server = FastMCP("TestServer", auth=provider)

        @server.tool
        def add(a: int, b: int) -> int:
            return a + b

        pre_registered = OAuthClientInformationFull(
            client_id="known-client",
            client_secret="known-secret",
            redirect_uris=[AnyUrl(f"http://localhost:{callback_port}/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
            scope="read",
        )
        await provider.register_client(pre_registered)

        async with run_server_async(server, port=port, transport="http") as url:
            parsed = urlparse(url)
            auth_server_url = f"{parsed.scheme}://{parsed.netloc}"

            oauth = HeadlessOAuth(
                mcp_url=url,
                client_id="known-client",
                client_secret="known-secret",
                scopes=["read"],
                callback_port=callback_port,
                authorization_server_url=auth_server_url,
            )

            async with Client(
                transport=StreamableHttpTransport(url),
                auth=oauth,
            ) as client:
                result = await client.call_tool("add", {"a": 5, "b": 7})
                assert result.data == 12

    async def test_without_authorization_server_url_still_works(self):
        """Normal 401-triggered flow remains unchanged when parameter is omitted."""
        port = find_available_port()
        callback_port = find_available_port()
        issuer_url = f"http://127.0.0.1:{port}"

        provider = InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["read"],
            ),
        )

        server = FastMCP("TestServer", auth=provider)

        @server.tool
        def echo(msg: str) -> str:
            return msg

        async with run_server_async(server, port=port, transport="http") as url:
            oauth = HeadlessOAuth(
                mcp_url=url,
                scopes=["read"],
                callback_port=callback_port,
                # authorization_server_url intentionally omitted
            )

            async with Client(
                transport=StreamableHttpTransport(url),
                auth=oauth,
            ) as client:
                assert await client.ping()
