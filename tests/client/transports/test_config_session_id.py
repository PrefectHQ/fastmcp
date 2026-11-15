"""MCPConfigTransport reports the session ID of a single-server config."""

import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import MCPConfigTransport
from fastmcp.utilities.tests import run_server_async


@pytest.fixture
def server() -> FastMCP:
    mcp = FastMCP("Session ID server")

    @mcp.tool
    def ping() -> str:
        return "pong"

    return mcp


async def test_single_server_config_reports_inner_session_id(server: FastMCP):
    async with run_server_async(server) as url:
        config = {"mcpServers": {"only": {"url": url}}}
        async with Client(config, mode="legacy") as client:
            transport = client.transport
            assert isinstance(transport, MCPConfigTransport)

            session_id = transport.get_session_id()

            assert session_id is not None
            assert session_id == transport.transport.get_session_id()


async def test_single_server_config_matches_url_client_behaviour(server: FastMCP):
    async with run_server_async(server) as url:
        async with Client(url, mode="legacy") as client:
            assert client.transport.get_session_id() is not None

        config = {"mcpServers": {"only": {"url": url}}}
        async with Client(config, mode="legacy") as client:
            assert client.transport.get_session_id() is not None


def test_multi_server_config_has_no_session_id():
    transport = MCPConfigTransport(
        {
            "mcpServers": {
                "a": {"url": "http://127.0.0.1:1/mcp"},
                "b": {"url": "http://127.0.0.1:2/mcp"},
            }
        }
    )

    assert transport.get_session_id() is None
