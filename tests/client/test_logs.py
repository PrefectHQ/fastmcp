import logging

import pytest
from mcp import LoggingLevel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.logging import LogMessage


class LogHandler:
    def __init__(self):
        self.logs: list[LogMessage] = []
        self.logger = logging.getLogger(__name__)
        # Backwards-compatible way to get the log level mapping
        if hasattr(logging, "getLevelNamesMapping"):
            # For Python 3.11+
            self.LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()  # pyright: ignore [reportAttributeAccessIssue]
        else:
            # For older Python versions
            self.LOGGING_LEVEL_MAP = logging._nameToLevel

    async def handle_log(self, message: LogMessage) -> None:
        self.logs.append(message)

        level = self.LOGGING_LEVEL_MAP[message.level.upper()]
        msg = message.data.get("msg")
        extra = message.data.get("extra")
        self.logger.log(level, msg, extra=extra)


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def log(context: Context) -> None:
        await context.info(message="hello?")

    @mcp.tool
    async def echo_log(
        message: str,
        context: Context,
        level: LoggingLevel | None = None,
        logger: str | None = None,
    ) -> None:
        await context.log(message=message, level=level)

    return mcp


class TestClientLogs:
    async def test_log(self, fastmcp_server: FastMCP, caplog):
        caplog.set_level(logging.INFO, logger=__name__)

        log_handler = LogHandler()
        async with Client(fastmcp_server, log_handler=log_handler.handle_log) as client:
            await client.call_tool("log", {})

        assert len(log_handler.logs) == 1
        assert log_handler.logs[0].data["msg"] == "hello?"
        assert log_handler.logs[0].level == "info"

        assert len(caplog.records) == 1
        assert caplog.records[0].msg == "hello?"
        assert caplog.records[0].levelname == "INFO"

    async def test_echo_log(self, fastmcp_server: FastMCP, caplog):
        caplog.set_level(logging.INFO, logger=__name__)

        log_handler = LogHandler()
        async with Client(fastmcp_server, log_handler=log_handler.handle_log) as client:
            await client.call_tool("echo_log", {"message": "this is a log"})

            assert len(log_handler.logs) == 1
            assert len(caplog.records) == 1
            await client.call_tool(
                "echo_log", {"message": "this is a warning log", "level": "warning"}
            )
            assert len(log_handler.logs) == 2
            assert len(caplog.records) == 2

        assert log_handler.logs[0].data["msg"] == "this is a log"
        assert log_handler.logs[0].level == "info"
        assert log_handler.logs[1].data["msg"] == "this is a warning log"
        assert log_handler.logs[1].level == "warning"

        assert caplog.records[0].msg == "this is a log"
        assert caplog.records[0].levelname == "INFO"
        assert caplog.records[1].msg == "this is a warning log"
        assert caplog.records[1].levelname == "WARNING"
