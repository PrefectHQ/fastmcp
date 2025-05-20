from typing import TypeAlias

from mcp.shared.session import ProgressFnT

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ProgressHandler: TypeAlias = ProgressFnT


async def default_progress_handler(
    progress: float, total: float | None, message: str | None
) -> None:
    """Default handler for progress notifications.

    Logs progress updates at debug level, properly handling missing total or message values.

    Args:
        progress: Current progress value
        total: Optional total expected value
        message: Optional status message
    """
    if total is not None:
        # We have both progress and total
        percent = (progress / total) * 100
        progress_str = f"{progress}/{total} ({percent:.1f}%)"
    else:
        # We only have progress
        progress_str = f"{progress}"

    # Include message if available
    if message:
        log_msg = f"Progress: {progress_str} - {message}"
    else:
        log_msg = f"Progress: {progress_str}"

    logger.debug(log_msg)
