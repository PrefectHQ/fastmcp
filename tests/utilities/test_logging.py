import logging

from fastmcp.utilities.logging import get_logger


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
