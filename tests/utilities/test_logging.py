import logging

import fastmcp
from fastmcp.utilities.logging import configure_logging, get_logger


def test_logging_doesnt_affect_other_loggers(caplog):
    # set FastMCP loggers to CRITICAL and ensure other loggers still emit messages
    original_level = logging.getLogger("fastmcp").getEffectiveLevel()

    try:
        logging.getLogger("fastmcp").setLevel(logging.CRITICAL)

        root_logger = logging.getLogger()
        app_logger = logging.getLogger("app")
        fastmcp_logger = logging.getLogger("fastmcp")
        fastmcp_server_logger = get_logger("server")

        with caplog.at_level(logging.INFO):
            root_logger.info("--ROOT--")
            app_logger.info("--APP--")
            fastmcp_logger.info("--FASTMCP--")
            fastmcp_server_logger.info("--FASTMCP SERVER--")

        assert "--ROOT--" in caplog.text
        assert "--APP--" in caplog.text
        assert "--FASTMCP--" not in caplog.text
        assert "--FASTMCP SERVER--" not in caplog.text

    finally:
        logging.getLogger("fastmcp").setLevel(original_level)


def test_configure_logging_with_traceback_kwargs():
    """Test that traceback-related kwargs can be passed without causing duplicate argument errors."""
    # This should not raise TypeError about duplicate keyword arguments
    configure_logging(enable_rich_tracebacks=True, tracebacks_max_frames=20)

    # Verify the logger was configured
    logger = logging.getLogger("fastmcp")
    assert logger.handlers
    assert len(logger.handlers) == 2  # One for normal logs, one for tracebacks


def test_configure_logging_traceback_defaults_can_be_overridden():
    """Test that default traceback settings can be overridden by kwargs."""
    configure_logging(
        enable_rich_tracebacks=True,
        tracebacks_max_frames=20,
        show_path=True,
        show_level=True,
    )

    logger = logging.getLogger("fastmcp")
    assert logger.handlers
    # The traceback handler should have been created with custom values
    # We can't directly inspect RichHandler internals easily, but we verified no error was raised


def test_configure_logging_with_rich_disabled():
    """Test that disabling rich logging uses standard StreamHandler."""
    original_enable_rich = fastmcp.settings.enable_rich_logging
    try:
        fastmcp.settings.enable_rich_logging = False
        configure_logging()

        logger = logging.getLogger("fastmcp")
        assert logger.handlers
        # Should only have one handler when rich logging is disabled
        assert len(logger.handlers) == 1
        # Should be a StreamHandler, not RichHandler
        assert isinstance(logger.handlers[0], logging.StreamHandler)
        assert not hasattr(
            logger.handlers[0], "console"
        )  # RichHandler has console attribute
    finally:
        fastmcp.settings.enable_rich_logging = original_enable_rich
        # Reconfigure to restore state
        configure_logging()


def test_configure_logging_with_rich_enabled():
    """Test that enabling rich logging uses RichHandler."""
    original_enable_rich = fastmcp.settings.enable_rich_logging
    try:
        fastmcp.settings.enable_rich_logging = True
        configure_logging()

        logger = logging.getLogger("fastmcp")
        assert logger.handlers
        # Should have two handlers when rich logging is enabled (normal + traceback)
        assert len(logger.handlers) == 2
        # Both should be RichHandler instances
        from rich.logging import RichHandler

        assert all(isinstance(h, RichHandler) for h in logger.handlers)
    finally:
        fastmcp.settings.enable_rich_logging = original_enable_rich
        # Reconfigure to restore state
        configure_logging()
