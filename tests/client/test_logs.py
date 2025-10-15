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


class TestDefaultLogHandler:
    """Tests for default_log_handler with data as any JSON-serializable type."""

    async def test_default_handler_routes_to_correct_levels(self):
        """Test that default_log_handler routes server logs to appropriate Python log levels."""
        from unittest.mock import MagicMock, patch

        from mcp.types import LoggingMessageNotificationParams

        from fastmcp.client.logging import default_log_handler

        with patch("fastmcp.client.logging.from_server_logger") as mock_logger:
            # Set up mock methods
            mock_logger.debug = MagicMock()
            mock_logger.info = MagicMock()
            mock_logger.warning = MagicMock()
            mock_logger.error = MagicMock()
            mock_logger.critical = MagicMock()

            # Test each log level
            test_cases = [
                ("debug", mock_logger.debug, "Debug message"),
                ("info", mock_logger.info, "Info message"),
                ("notice", mock_logger.info, "Notice message"),  # notice -> info
                ("warning", mock_logger.warning, "Warning message"),
                ("error", mock_logger.error, "Error message"),
                ("critical", mock_logger.critical, "Critical message"),
                ("alert", mock_logger.critical, "Alert message"),  # alert -> critical
                (
                    "emergency",
                    mock_logger.critical,
                    "Emergency message",
                ),  # emergency -> critical
            ]

            for level, expected_method, msg in test_cases:
                # Reset mocks
                mock_logger.reset_mock()

                # Create log message with data as a string
                log_msg = LoggingMessageNotificationParams(
                    level=level,  # type: ignore[arg-type]
                    logger="test.logger",
                    data=msg,
                )

                # Call handler
                await default_log_handler(log_msg)

                # Verify correct method was called
                expected_method.assert_called_once_with(
                    msg=f"Received {level.upper()} from server (test.logger): {msg}"
                )

    async def test_default_handler_without_logger_name(self):
        """Test that default_log_handler works when logger name is None."""
        from unittest.mock import MagicMock, patch

        from mcp.types import LoggingMessageNotificationParams

        from fastmcp.client.logging import default_log_handler

        with patch("fastmcp.client.logging.from_server_logger") as mock_logger:
            mock_logger.info = MagicMock()

            log_msg = LoggingMessageNotificationParams(
                level="info",
                logger=None,
                data="Message without logger",
            )

            await default_log_handler(log_msg)

            mock_logger.info.assert_called_once_with(
                msg="Received INFO from server: Message without logger"
            )

    async def test_default_handler_with_dict_data(self):
        """Test that default_log_handler handles dict data correctly."""
        from unittest.mock import MagicMock, patch

        from mcp.types import LoggingMessageNotificationParams

        from fastmcp.client.logging import default_log_handler

        with patch("fastmcp.client.logging.from_server_logger") as mock_logger:
            mock_logger.info = MagicMock()

            log_msg = LoggingMessageNotificationParams(
                level="info",
                logger="test.logger",
                data={"key": "value", "count": 42},
            )

            await default_log_handler(log_msg)

            # Should log the entire dict as a string
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert "Received INFO from server (test.logger):" in call_args[1]["msg"]
            assert "key" in call_args[1]["msg"]
            assert "value" in call_args[1]["msg"]

    async def test_default_handler_with_list_data(self):
        """Test that default_log_handler handles list data correctly."""
        from unittest.mock import MagicMock, patch

        from mcp.types import LoggingMessageNotificationParams

        from fastmcp.client.logging import default_log_handler

        with patch("fastmcp.client.logging.from_server_logger") as mock_logger:
            mock_logger.warning = MagicMock()

            log_msg = LoggingMessageNotificationParams(
                level="warning",
                logger="test.logger",
                data=["item1", "item2", "item3"],
            )

            await default_log_handler(log_msg)

            # Should log the entire list as a string
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "Received WARNING from server (test.logger):" in call_args[1]["msg"]
            assert "item1" in call_args[1]["msg"]

    async def test_default_handler_with_number_data(self):
        """Test that default_log_handler handles numeric data correctly."""
        from unittest.mock import MagicMock, patch

        from mcp.types import LoggingMessageNotificationParams

        from fastmcp.client.logging import default_log_handler

        with patch("fastmcp.client.logging.from_server_logger") as mock_logger:
            mock_logger.error = MagicMock()

            log_msg = LoggingMessageNotificationParams(
                level="error",
                logger=None,
                data=404,
            )

            await default_log_handler(log_msg)

            mock_logger.error.assert_called_once_with(
                msg="Received ERROR from server: 404"
            )
