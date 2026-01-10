"""Dependency injection exports for FastMCP.

This module re-exports dependency injection symbols from Docket and FastMCP
to provide a clean, centralized import location for all dependency-related
functionality.

DI features (Depends, CurrentContext, CurrentFastMCP) work without pydocket
using a vendored DI engine. Only task-related dependencies (CurrentDocket,
CurrentWorker) and background task execution require fastmcp[tasks].
"""

# Try docket first for isinstance compatibility, fall back to vendored
try:
    from docket import Depends
except ImportError:
    from fastmcp._vendor.docket_di import Depends


from fastmcp.server.dependencies import (
    CurrentContext,
    CurrentDocket,
    CurrentFastMCP,
    CurrentWorker,
    Progress,
    ProgressLike,
)

__all__ = [
    "CurrentContext",
    "CurrentDocket",
    "CurrentFastMCP",
    "CurrentWorker",
    "Depends",
    "Progress",
    "ProgressLike",
]
