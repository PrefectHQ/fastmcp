import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.extensions import ServerExtension
from fastmcp.server.middleware.caching import ResponseCachingMiddleware


@pytest.mark.parametrize("register_extension", [False, True])
async def test_extension_live_reads_do_not_disable_unrelated_caching(
    register_extension: bool,
):
    class LiveReads(ServerExtension):
        identifier = "example.com/live-reads"

        def requires_live_resource(self, uri: str) -> bool:
            return uri == "data://live"

    calls = {"live": 0, "cached": 0}
    server = FastMCP("Caching", middleware=[ResponseCachingMiddleware()])
    if register_extension:
        server.add_extension(LiveReads())

    @server.resource("data://{name}")
    def resource(name: str) -> str:
        calls[name] += 1
        return str(calls[name])

    async with Client(server) as client:
        for name in calls:
            await client.read_resource(f"data://{name}")
            contents = await client.read_resource(f"data://{name}")
            expected = 2 if name == "live" and register_extension else 1
            assert contents[0].text == str(expected)
    assert calls == {"live": 2 if register_extension else 1, "cached": 1}
