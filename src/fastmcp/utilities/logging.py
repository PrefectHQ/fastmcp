"""Logging utilities for FastMCP."""

import contextlib
import logging
from typing import Any, Literal, cast

from rich.console import Console
from rich.logging import RichHandler

import fastmcp


def get_logger(name: str) -> logging.Logger:
    """Get a logger nested under FastMCP namespace.

    Args:
        name: the name of the logger, which will be prefixed with 'FastMCP.'

    Returns:
        a configured logger instance
    """
    return logging.getLogger(f"fastmcp.{name}")


def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | int = "INFO",
    logger: logging.Logger | None = None,
    enable_rich_tracebacks: bool | None = None,
    **rich_kwargs: Any,
) -> None:
    """
    Configure logging for FastMCP.

    Args:
        logger: the logger to configure
        level: the log level to use
        rich_kwargs: the parameters to use for creating RichHandler
    """
    # Check if logging is disabled in settings
    if not fastmcp.settings.log_enabled:
        return

    # Use settings default if not specified
    if enable_rich_tracebacks is None:
        enable_rich_tracebacks = fastmcp.settings.enable_rich_tracebacks

    if logger is None:
        logger = logging.getLogger("fastmcp")

    # Only configure the FastMCP logger namespace
    handler = RichHandler(
        console=Console(stderr=True),
        rich_tracebacks=enable_rich_tracebacks,
        **rich_kwargs,
    )
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.setLevel(level)

    # Remove any existing handlers to avoid duplicates on reconfiguration
    for hdlr in logger.handlers[:]:
        logger.removeHandler(hdlr)

    logger.addHandler(handler)

    # Don't propagate to the root logger
    logger.propagate = False


@contextlib.contextmanager
def temporary_log_level(
    level: str | None,
    logger: logging.Logger | None = None,
    enable_rich_tracebacks: bool | None = None,
    **rich_kwargs: Any,
):
    """Context manager to temporarily set log level and restore it afterwards.

    Args:
        level: The temporary log level to set (e.g., "DEBUG", "INFO")
        logger: Optional logger to configure (defaults to FastMCP logger)
        enable_rich_tracebacks: Whether to enable rich tracebacks
        **rich_kwargs: Additional parameters for RichHandler

    Usage:
        with temporary_log_level("DEBUG"):
            # Code that runs with DEBUG logging
            pass
        # Original log level is restored here
    """
    if level:
        # Get the original log level from settings
        original_level = fastmcp.settings.log_level

        # Configure with new level
        # Cast to proper type for type checker
        log_level_literal = cast(
            Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            level.upper(),
        )
        configure_logging(
            level=log_level_literal,
            logger=logger,
            enable_rich_tracebacks=enable_rich_tracebacks,
            **rich_kwargs,
        )
        try:
            yield
        finally:
            # Restore original configuration using configure_logging
            # This will respect the log_enabled setting
            configure_logging(
                level=original_level,
                logger=logger,
                enable_rich_tracebacks=enable_rich_tracebacks,
                **rich_kwargs,
            )
    else:
        yield
