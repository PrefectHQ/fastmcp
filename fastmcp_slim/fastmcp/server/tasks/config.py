"""Backward-compatible exports for task configuration primitives."""

from fastmcp.utilities.tasks import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_TTL_MS,
    TaskConfig,
    TaskMeta,
    TaskMode,
)

__all__ = [
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_POLL_INTERVAL_MS",
    "DEFAULT_TTL_MS",
    "TaskConfig",
    "TaskMeta",
    "TaskMode",
]
