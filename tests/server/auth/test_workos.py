from collections.abc import Generator

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.workos import AuthKitProvider
from fastmcp.utilities.tests import HeadlessOAuth, run_server_in_process


def run_mcp_server(host: str, port: int) -> None:
    mcp = FastMCP(
        auth=AuthKitProvider(
            authkit_domain="https://respectful-lullaby-34-staging.authkit.app",
            base_url="http://localhost:4321",
        )
    )

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    mcp.run(host=host, port=port, transport="http")


@pytest.fixture(scope="module")
def mcp_server_url() -> Generator[str]:
    with run_server_in_process(run_mcp_server) as url:
        yield f"{url}/mcp/"


@pytest.fixture()
def client_with_headless_oauth(
    mcp_server_url: str,
) -> Generator[Client, None, None]:
    """Client with headless OAuth that bypasses browser interaction."""
    client = Client(
        transport=StreamableHttpTransport(mcp_server_url),
        auth=HeadlessOAuth(mcp_url=mcp_server_url),
    )
    yield client


class TestAuthKitProvider:
    async def test_unauthorized_access(self, mcp_server_url: str):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            async with Client(mcp_server_url) as client:
                tools = await client.list_tools()  # noqa: F841
        assert exc_info.value.response.status_code == 401
        assert "tools" not in locals()

    # async def test_authorized_access(self, client_with_headless_oauth: Client):
    #     async with client_with_headless_oauth:
    #         tools = await client_with_headless_oauth.list_tools()
    #     assert tools is not None
    #     assert len(tools) > 0
    #     assert "add" in tools
