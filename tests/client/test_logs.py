import pytest
from mcp import LoggingLevel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.logging import LogMessage


class LogHandler:
    def __init__(self):
        self.logs: list[LogMessage] = []

    async def handle_log(self, message: LogMessage) -> None:
        self.logs.append(message)


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool()
    async def log(context: Context) -> None:
        await context.info(message="hello?")

    @mcp.tool()
    async def echo_log(
        message: str,
        context: Context,
        level: LoggingLevel | None = None,
        logger: str | None = None,
    ) -> None:
        await context.log(message=message, level=level)

    return mcp


class TestClientLogs:
    async def test_log(self, fastmcp_server: FastMCP):
        log_handler = LogHandler()
        async with Client(fastmcp_server, log_handler=log_handler.handle_log) as client:
            await client.call_tool("log", {})

        assert len(log_handler.logs) == 1
        assert log_handler.logs[0].data == "hello?"
        assert log_handler.logs[0].level == "info"

    async def test_echo_log(self, fastmcp_server: FastMCP):
        log_handler = LogHandler()
        async with Client(fastmcp_server, log_handler=log_handler.handle_log) as client:
            await client.call_tool("echo_log", {"message": "this is a log"})

            assert len(log_handler.logs) == 1
            await client.call_tool(
                "echo_log", {"message": "this is a warning log", "level": "warning"}
            )
            assert len(log_handler.logs) == 2

        assert log_handler.logs[0].data == "this is a log"
        assert log_handler.logs[0].level == "info"
        assert log_handler.logs[1].data == "this is a warning log"
        assert log_handler.logs[1].level == "warning"
