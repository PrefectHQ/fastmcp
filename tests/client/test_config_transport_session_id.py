from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import run_server_async


async def test_mcp_config_transport_get_session_id_single_server():
    server = FastMCP("s")

    @server.tool
    def ping() -> str:
        return "pong"

    async with run_server_async(server) as url:
        async with Client(
            {"mcpServers": {"only": {"url": url}}}, mode="legacy"
        ) as client:
            session_id = client.transport.get_session_id()
            assert session_id is not None
            assert session_id == client.transport.transport.get_session_id()  # type: ignore[attr-defined]


async def test_mcp_config_transport_get_session_id_multi_server_returns_none():
    s1 = FastMCP("s1")
    s2 = FastMCP("s2")

    async with run_server_async(s1) as url1, run_server_async(s2) as url2:
        async with Client(
            {"mcpServers": {"one": {"url": url1}, "two": {"url": url2}}},
            mode="legacy",
        ) as client:
            assert client.transport.get_session_id() is None
