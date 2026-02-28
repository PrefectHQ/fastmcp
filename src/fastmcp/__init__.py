"""FastMCP - An ergonomic MCP interface."""

import importlib
import warnings
from importlib.metadata import version as _version
from typing import TYPE_CHECKING

from fastmcp.settings import Settings
from fastmcp.utilities.logging import configure_logging as _configure_logging

if TYPE_CHECKING:
    from fastmcp.client import Client as Client

settings = Settings()
if settings.log_enabled:
    _configure_logging(
        level=settings.log_level,
        enable_rich_tracebacks=settings.enable_rich_tracebacks,
    )

from fastmcp.server.server import FastMCP
from fastmcp.server.context import Context
import fastmcp.server

__version__ = _version("fastmcp")


# ensure deprecation warnings are displayed by default
if settings.deprecation_warnings:
    warnings.simplefilter("default", DeprecationWarning)


# --- Lazy imports for performance (see #3292) ---
# Client and the client submodule are deferred so that server-only users
# don't pay for the client import chain. Do not convert back to top-level.


def __getattr__(name: str) -> object:
    if name == "Client":
        from fastmcp.client import Client

        return Client
    if name == "client":
        return importlib.import_module("fastmcp.client")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Client",
    "Context",
    "FastMCP",
    "settings",
]
