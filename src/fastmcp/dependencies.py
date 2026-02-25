"""Dependency injection exports for FastMCP.

This module re-exports dependency injection symbols to provide a clean,
centralized import location for all dependency-related functionality.

DI features (Depends, CurrentContext, CurrentFastMCP) work without pydocket
using the uncalled-for DI engine. Only task-related dependencies (CurrentDocket,
CurrentWorker) and background task execution require fastmcp[tasks].
"""

from uncalled_for import Dependency, Depends

from fastmcp.server.dependencies import (
    CurrentAccessToken,
    CurrentContext,
    CurrentDocket,
    CurrentFastMCP,
    CurrentHeaders,
    CurrentRequest,
    CurrentWorker,
    Progress,
    ProgressLike,
    TokenClaim,
)

__all__ = [
    "CurrentAccessToken",
    "CurrentContext",
    "CurrentDocket",
    "CurrentFastMCP",
    "CurrentHeaders",
    "CurrentRequest",
    "CurrentWorker",
    "Dependency",
    "Depends",
    "Progress",
    "ProgressLike",
    "TokenClaim",
]
