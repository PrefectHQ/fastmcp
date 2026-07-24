"""Dependency injection exports for FastMCP.

This module re-exports dependency injection symbols to provide a clean,
centralized import location for all dependency-related functionality.

DI features (Depends, CurrentContext, CurrentFastMCP) work without pydocket
using the uncalled-for DI engine. The docket-specific dependencies
(``CurrentDocket``, ``CurrentWorker``) live in the ``fastmcp-tasks`` package
(``fastmcp_tasks.dependencies``).
"""

from typing import Any

from uncalled_for import Dependency, Depends, Shared

from fastmcp.server.dependencies import (
    CurrentAccessToken,
    CurrentContext,
    CurrentFastMCP,
    CurrentHeaders,
    CurrentRequest,
    Progress,
    ProgressLike,
    TokenClaim,
)

__all__ = [
    "CurrentAccessToken",
    "CurrentContext",
    "CurrentFastMCP",
    "CurrentHeaders",
    "CurrentRequest",
    "Dependency",
    "Depends",
    "Progress",
    "ProgressLike",
    "Shared",
    "TokenClaim",
]

# Docket-specific dependencies moved to the fastmcp-tasks package. Point users
# there instead of raising a bare AttributeError.
_MOVED_TO_TASKS = {"CurrentDocket", "CurrentWorker"}


def __getattr__(name: str) -> Any:
    if name in _MOVED_TO_TASKS:
        raise ImportError(
            f"{name!r} moved to the fastmcp-tasks package. Install it with "
            f"`pip install 'fastmcp[tasks]'` and import from "
            f"`fastmcp_tasks.dependencies`."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
