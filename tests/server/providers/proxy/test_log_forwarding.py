import asyncio
import sys
from pathlib import Path

import pytest

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import ClientTransport, StdioTransport
from fastmcp.server import create_proxy
from fastmcp.server.elicitation import AcceptedElicitation

pytestmark = [pytest.mark.client_process, pytest.mark.timeout(30)]

SERVER_SCRIPT = """
import sys
from fastmcp import Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy

server = FastMCP("logging-backend")

@server.tool
async def chatty(ctx: Context) -> str:
    for i in range(5):
        await ctx.log(f"message {i}", level="info")
    return "done"

if sys.argv[1] == "proxy":
    server = create_proxy(server)
elif sys.argv[1] == "stdio-backend-proxy":
    server = create_proxy(StdioTransport(sys.executable, [__file__, "backend"]))
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
async def test_proxy_with_keepalive_stdio_backend_forwards_every_log(
    server_script: Path,
):
    backend_transport = StdioTransport(sys.executable, [str(server_script), "backend"])
    try:
        proxy = create_proxy(backend_transport)
        assert await collect_logs(proxy) == EXPECTED
    finally:
        await backend_transport.close()


@pytest.mark.subprocess_heavy
async def test_stdio_proxy_with_keepalive_stdio_backend_forwards_every_log(
    server_script: Path,
):
    transport = StdioTransport(
        sys.executable, [str(server_script), "stdio-backend-proxy"]
    )
    assert await collect_logs(transport) == EXPECTED


async def test_concurrent_proxy_calls_do_not_misroute_elicitation():
    backend = FastMCP("elicit-backend")
    second_entered = asyncio.Event()
    elicit_handled = asyncio.Event()

    @backend.tool
    async def elicit_after_overlap(ctx: Context) -> str:
        await second_entered.wait()
        result = await ctx.elicit("Name?", response_type=str)
        assert isinstance(result, AcceptedElicitation)
        return str(result.data)

    @backend.tool
    async def overlap_call() -> str:
        second_entered.set()
        await elicit_handled.wait()
        return "second-done"

    proxy = create_proxy(backend)
    seen: dict[str, list[str]] = {"first": [], "second": []}

    async def elicit_first(message, response_type, params, ctx):
        seen["first"].append(message)
        elicit_handled.set()
        return ElicitResult(action="accept", content={"value": "caller-1"})

    async def elicit_second(message, response_type, params, ctx):
        seen["second"].append(message)
        return ElicitResult(action="accept", content={"value": "caller-2"})

    async with (
        Client(proxy, mode="legacy", elicitation_handler=elicit_first) as client_1,
        Client(proxy, mode="legacy", elicitation_handler=elicit_second) as client_2,
    ):
        r1, r2 = await asyncio.gather(
            client_1.call_tool("elicit_after_overlap"),
            client_2.call_tool("overlap_call"),
        )

    assert r1.data == "caller-1"
    assert r2.data == "second-done"
    assert seen == {"first": ["Name?"], "second": []}
