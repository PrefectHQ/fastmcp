"""Task configuration primitives for FastMCP components."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from fastmcp.utilities.async_utils import is_coroutine_function

TaskMode = Literal["forbidden", "optional", "required"]

DEFAULT_POLL_INTERVAL = timedelta(seconds=5)
DEFAULT_POLL_INTERVAL_MS = int(DEFAULT_POLL_INTERVAL.total_seconds() * 1000)
DEFAULT_TTL_MS = 60_000


@dataclass
class TaskMeta:
    """Metadata for task-augmented execution requests.

    Attributes:
        ttl: Client-requested TTL in milliseconds. If None, uses server default.
        fn_key: Docket routing key. Auto-derived from component name if None.
    """

    ttl: int | None = None
    fn_key: str | None = None


@dataclass
class TaskConfig:
    """Configuration for MCP background task execution.

    Controls how a component handles task-augmented requests:

    - ``forbidden``: Component does not support task execution.
    - ``optional``: Component supports both synchronous and task execution.
    - ``required``: Component requires task execution.
    """

    mode: TaskMode = "optional"
    poll_interval: timedelta = DEFAULT_POLL_INTERVAL

    @classmethod
    def from_bool(cls, value: bool) -> TaskConfig:
        """Convert a boolean task flag to a TaskConfig."""
        return cls(mode="optional" if value else "forbidden")

    def supports_tasks(self) -> bool:
        """Check if this component supports task execution."""
        return self.mode != "forbidden"

    def validate_function(self, fn: Callable[..., Any], name: str) -> None:
        """Validate that a function is compatible with this task config."""
        if not self.supports_tasks():
            return

        from fastmcp.server.dependencies import require_docket

        require_docket(f"`task=True` on function '{name}'")

        fn_to_check = fn
        if (
            not inspect.isroutine(fn)
            and not isinstance(fn, functools.partial)
            and callable(fn)
        ):
            fn_to_check = fn.__call__
        if isinstance(fn_to_check, staticmethod):
            fn_to_check = fn_to_check.__func__

        if not is_coroutine_function(fn_to_check):
            raise ValueError(
                f"'{name}' uses a sync function but has task execution enabled. "
                "Background tasks require async functions."
            )
