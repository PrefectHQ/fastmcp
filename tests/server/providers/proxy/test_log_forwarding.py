import asyncio
import sys
from pathlib import Path

import pytest

from fastmcp import Client
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import StdioTransport


@pytest.mark.timeout(30)
@pytest.mark.parametrize("proxy", [False, True])
async def test_stdio_proxy_forwards_all_logs_before_backend_disconnect(
    tmp_path: Path, proxy: bool
):
    script = tmp_path / "logging_server.py"
    script.write_text(
        """
import sys
from fastmcp import Context, FastMCP
from fastmcp.server import create_proxy
server = FastMCP('logging-backend')
@server.tool
async def chatty(ctx: Context) -> str:
    for i in range(5):
        await ctx.log(f'message {i}', level='info')
    return 'done'
if sys.argv[1] == 'proxy':
    server = create_proxy(server)
server.run(show_banner=False)
""",
        encoding="utf-8",
    )
    received = []
    complete = asyncio.Event()

    async def on_log(message: LogMessage) -> None:
        received.append(message.data)
        if len(received) == 5:
            complete.set()

    transport = StdioTransport(
        sys.executable, [str(script), "proxy" if proxy else "direct"]
    )
    async with Client(transport, log_handler=on_log) as client:
        assert (await client.call_tool("chatty")).data == "done"
        try:
            await asyncio.wait_for(complete.wait(), timeout=2)
        except TimeoutError:
            pass
    assert received == [{"msg": f"message {i}", "extra": None} for i in range(5)]
