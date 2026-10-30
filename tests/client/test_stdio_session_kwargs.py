import asyncio
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import ProxyClient

pytestmark = pytest.mark.subprocess_heavy


@pytest.fixture
def sleepy_server(tmp_path: Path) -> Path:
    script = tmp_path / "sleepy_server.py"
    script.write_text(
        "import asyncio\n"
        "from fastmcp import FastMCP\n"
        "server = FastMCP('sleepy')\n"
        "@server.tool\n"
        "async def sleep(seconds: float) -> str:\n"
        "    await asyncio.sleep(seconds)\n"
        "    return 'done'\n"
        "if __name__ == '__main__':\n"
        "    server.run()\n",
        encoding="utf-8",
    )
    return script


@pytest.mark.timeout(20)
@pytest.mark.parametrize(
    "first_timeout, second_timeout",
    [(1, 3), (3, 1), (None, 1), (1, None), (0, 1), (1, 0)],
)
async def test_second_client_uses_its_own_timeout(
    sleepy_server: Path, first_timeout: float | None, second_timeout: float | None
) -> None:
    transport = PythonStdioTransport(sleepy_server)
    try:
        async with Client(transport, timeout=first_timeout) as first:
            await first.list_tools()
            first_session = first.session
        async with Client(transport, timeout=second_timeout) as second:
            if second_timeout == 1:
                with pytest.raises(MCPError, match="timed out"):
                    await second.call_tool("sleep", {"seconds": 2})
            else:
                result = await second.call_tool("sleep", {"seconds": 2})
                assert result.data == "done"
            assert second.session is not first_session
    finally:
        await transport.close()


@pytest.mark.timeout(20)
@pytest.mark.parametrize("second_timeout", [1, None])
async def test_different_client_settings_do_not_replace_an_active_session(
    sleepy_server: Path,
    second_timeout: float | None,
) -> None:
    transport = PythonStdioTransport(sleepy_server)
    try:
        async with Client(transport, timeout=3) as first:
            with pytest.raises(RuntimeError, match="still using it"):
                async with Client(transport, timeout=second_timeout):
                    pass
            result = await first.call_tool("sleep", {"seconds": 0})
            assert result.data == "done"
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_same_client_reuses_session_and_per_call_timeout_overrides_default(
    sleepy_server: Path,
) -> None:
    transport = PythonStdioTransport(sleepy_server)
    client = Client(transport, timeout=1)
    try:
        async with client:
            session = client.session
        async with client:
            assert client.session is session
            result = await client.call_tool("sleep", {"seconds": 2}, timeout=3)
            assert result.data == "done"
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_matching_timeouts_reuse_active_and_idle_connections() -> None:
    script = Path(__file__).parent / "minimal_stdio_server.py"
    transport = PythonStdioTransport(script)
    try:
        async with transport.connect_session(read_timeout_seconds=3) as first:
            await first.initialize()
            async with transport.connect_session(read_timeout_seconds=3) as second:
                assert second is first
        async with transport.connect_session(read_timeout_seconds=3) as third:
            assert third is first
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_proxy_client_clones_keep_the_stdio_session(sleepy_server: Path) -> None:
    transport = PythonStdioTransport(sleepy_server)
    proxy = ProxyClient(transport)
    try:
        async with proxy.new() as first:
            session = first.session
            async with proxy.new() as second:
                assert second.session is session
                result = await second.call_tool("sleep", {"seconds": 0})
                assert result.data == "done"
        async with proxy.new() as third:
            assert third.session is session
            result = await third.call_tool("sleep", {"seconds": 0})
            assert result.data == "done"
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_separate_clients_with_matching_timeouts_share_the_session(
    sleepy_server: Path,
) -> None:
    transport = PythonStdioTransport(sleepy_server)
    try:
        async with Client(transport, timeout=3) as first:
            async with Client(transport, timeout=3) as second:
                assert second.session is first.session
                result = await second.call_tool("sleep", {"seconds": 0})
                assert result.data == "done"
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_proxy_requests_preserve_backend_state(tmp_path: Path) -> None:
    script = tmp_path / "stateful_server.py"
    script.write_text(
        "import asyncio\n"
        "import os\n"
        "from fastmcp import FastMCP\n"
        "server = FastMCP('stateful')\n"
        "counter = 0\n"
        "@server.tool\n"
        "async def count() -> dict[str, int]:\n"
        "    global counter\n"
        "    counter += 1\n"
        "    captured = counter\n"
        "    await asyncio.sleep(0.05)\n"
        "    return {'pid': os.getpid(), 'count': captured}\n"
        "if __name__ == '__main__':\n"
        "    server.run(show_banner=False)\n",
        encoding="utf-8",
    )
    transport = PythonStdioTransport(script)
    try:
        async with Client(create_proxy(transport)) as client:
            first = (await client.call_tool("count")).data
            second = (await client.call_tool("count")).data
            assert second["pid"] == first["pid"]
            assert [first["count"], second["count"]] == [1, 2]
            results = await asyncio.gather(
                client.call_tool("count"), client.call_tool("count")
            )
            assert sorted(result.data["count"] for result in results) == [3, 4]
            assert {result.data["pid"] for result in results} == {first["pid"]}
    finally:
        await transport.close()


@pytest.mark.timeout(20)
async def test_omitted_and_explicit_none_reuse_default_timeout() -> None:
    transport = PythonStdioTransport(Path(__file__).parent / "minimal_stdio_server.py")
    try:
        async with transport.connect_session() as first:
            await first.initialize()
            async with transport.connect_session(read_timeout_seconds=None) as second:
                assert second is first
    finally:
        await transport.close()
