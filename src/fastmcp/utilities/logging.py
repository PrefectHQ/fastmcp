"""Logging utilities for FastMCP."""

import logging
from typing import Literal

from rich.console import Console
from rich.logging import RichHandler


def get_logger(name: str) -> logging.Logger:
    """Get a logger nested under FastMCP namespace.

    Args:
        name: the name of the logger, which will be prefixed with 'FastMCP.'

    Returns:
        a configured logger instance
    """
    return logging.getLogger(f"FastMCP.{name}")


def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | int = "INFO",
) -> None:
    """Configure logging for FastMCP.

    Args:
        level: the log level to use
    """
    # Only configure the FastMCP logger namespace
    handler = RichHandler(console=Console(stderr=True), rich_tracebacks=True)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    fastmcp_logger = logging.getLogger("FastMCP")
    fastmcp_logger.setLevel(level)

    # Remove any existing handlers to avoid duplicates on reconfiguration
    for hdlr in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(hdlr)

    fastmcp_logger.addHandler(handler)
