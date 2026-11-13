import asyncio
import sys
from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import ClientTransport, StdioTransport
from fastmcp.server import create_proxy

pytestmark = [pytest.mark.client_process, pytest.mark.timeout(30)]

SERVER_SCRIPT = """
import sys
from fastmcp import Context, FastMCP
from fastmcp.server import create_proxy

server = FastMCP("logging-backend")

@server.tool
async def chatty(ctx: Context) -> str:
    for i in range(5):
        await ctx.log(f"message {i}", level="info")
    return "done"

if sys.argv[1] == "proxy":
    server = create_proxy(server)
server.run(show_banner=False)
"""

EXPECTED = [{"msg": f"message {i}", "extra": None} for i in range(5)]


@pytest.fixture
def server_script(tmp_path: Path) -> Path:
    script = tmp_path / "logging_server.py"
    script.write_text(SERVER_SCRIPT, encoding="utf-8")
    return script


async def collect_logs(target: ClientTransport | FastMCP) -> list:
    received = []
    complete = asyncio.Event()

    async def on_log(message: LogMessage) -> None:
        received.append(message.data)
        if len(received) == len(EXPECTED):
            complete.set()

    async with Client(target, log_handler=on_log) as client:
        assert (await client.call_tool("chatty")).data == "done"
        try:
            await asyncio.wait_for(complete.wait(), timeout=2)
        except TimeoutError:
            pass
    return received


@pytest.mark.subprocess_heavy
async def test_proxy_served_over_stdio_forwards_every_log(server_script: Path):
    transport = StdioTransport(sys.executable, [str(server_script), "proxy"])
    assert await collect_logs(transport) == EXPECTED


@pytest.mark.subprocess_heavy
async def test_proxy_of_stdio_backend_forwards_every_log(server_script: Path):
    backend = StdioTransport(sys.executable, [str(server_script), "direct"])
    try:
        assert await collect_logs(create_proxy(backend)) == EXPECTED
    finally:
        await backend.close()
