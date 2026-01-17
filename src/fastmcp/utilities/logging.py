"""Logging utilities for FastMCP."""

import contextlib
import logging
from typing import Any, Literal, cast

from rich.console import Console
from rich.logging import RichHandler
from typing_extensions import override

import fastmcp


def get_logger(name: str) -> logging.Logger:
    """Get a logger nested under FastMCP namespace.

    Args:
        name: the name of the logger, which will be prefixed with 'FastMCP.'

    Returns:
        a configured logger instance
    """
    if name.startswith("fastmcp."):
        return logging.getLogger(name=name)

    return logging.getLogger(name=f"fastmcp.{name}")


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

    formatter = logging.Formatter("%(message)s")

    # Don't propagate to the root logger
    logger.propagate = False
    logger.setLevel(level)

    # Remove any existing handlers to avoid duplicates on reconfiguration
    for hdlr in logger.handlers[:]:
        logger.removeHandler(hdlr)

    # Use standard logging handlers if rich logging is disabled
    if not fastmcp.settings.enable_rich_logging:
        # Create a standard StreamHandler for stderr
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
        return

    # Configure the handler for normal logs
    handler = RichHandler(
        console=Console(stderr=True),
        **rich_kwargs,
    )
    handler.setFormatter(formatter)

    # filter to exclude tracebacks
    handler.addFilter(lambda record: record.exc_info is None)

    # Configure the handler for tracebacks, for tracebacks we use a compressed format:
    # no path or level name to maximize width available for the traceback
    # suppress framework frames and limit the number of frames to 3

    import mcp
    import pydantic

    # Build traceback kwargs with defaults that can be overridden
    traceback_kwargs = {
        "console": Console(stderr=True),
        "show_path": False,
        "show_level": False,
        "rich_tracebacks": enable_rich_tracebacks,
        "tracebacks_max_frames": 3,
        "tracebacks_suppress": [fastmcp, mcp, pydantic],
    }
    # Override defaults with user-provided values
    traceback_kwargs.update(rich_kwargs)

    traceback_handler = RichHandler(**traceback_kwargs)  # type: ignore[arg-type]
    traceback_handler.setFormatter(formatter)

    traceback_handler.addFilter(lambda record: record.exc_info is not None)

    logger.addHandler(handler)
    logger.addHandler(traceback_handler)


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


_level_to_no: dict[
    Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None, int | None
] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    None: None,
}


class _ClampedLogFilter(logging.Filter):
    min_level: tuple[int, str] | None
    max_level: tuple[int, str] | None

    def __init__(
        self,
        min_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        | None = None,
        max_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        | None = None,
    ):
        self.min_level = None
        self.max_level = None

        if min_level_no := _level_to_no.get(min_level):
            self.min_level = (min_level_no, str(min_level))
        if max_level_no := _level_to_no.get(max_level):
            self.max_level = (max_level_no, str(max_level))

        super().__init__()

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if self.max_level:
            max_level_no, max_level_name = self.max_level

            if record.levelno > max_level_no:
                record.levelno = max_level_no
                record.levelname = max_level_name
                return True

        if self.min_level:
            min_level_no, min_level_name = self.min_level
            if record.levelno < min_level_no:
                record.levelno = min_level_no
                record.levelname = min_level_name
                return True

        return True


def _clamp_logger(
    logger: logging.Logger,
    min_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
    max_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
) -> None:
    """Clamp the logger to a minimum and maximum level.

    If min_level is provided, messages logged at a lower level than `min_level` will have their level increased to `min_level`.
    If max_level is provided, messages logged at a higher level than `max_level` will have their level decreased to `max_level`.

    Args:
        min_level: The lower bound of the clamp
        max_level: The upper bound of the clamp
    """
    _unclamp_logger(logger=logger)

    logger.addFilter(filter=_ClampedLogFilter(min_level=min_level, max_level=max_level))


def _unclamp_logger(logger: logging.Logger) -> None:
    """Remove all clamped log filters from the logger."""
    for filter in logger.filters[:]:
        if isinstance(filter, _ClampedLogFilter):
            logger.removeFilter(filter)
