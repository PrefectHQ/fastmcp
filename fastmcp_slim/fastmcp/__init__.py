"""FastMCP - An ergonomic MCP interface."""

import importlib
import warnings
from importlib.metadata import PackageNotFoundError, version as _version
from typing import TYPE_CHECKING

from fastmcp.settings import Settings
from fastmcp.utilities.logging import configure_logging as _configure_logging

if TYPE_CHECKING:
    from fastmcp.client import Client as Client
    from fastmcp.apps.app import FastMCPApp as FastMCPApp
    from fastmcp.exceptions import (
        FastMCPDeprecationWarning as FastMCPDeprecationWarning,
    )
    from fastmcp.server.context import Context as Context
    from fastmcp.server.server import FastMCP as FastMCP

settings = Settings()
if settings.log_enabled:
    _configure_logging(
        level=settings.log_level,
        enable_rich_tracebacks=settings.enable_rich_tracebacks,
    )

try:
    __version__ = _version("fastmcp-slim")
except PackageNotFoundError:
    __version__ = _version("fastmcp")

if settings.deprecation_warnings:
    try:
        from fastmcp.exceptions import FastMCPDeprecationWarning
    except ImportError:
        pass
    else:
        warnings.simplefilter("default", FastMCPDeprecationWarning)


# --- Lazy imports for performance (see #3292) ---
# Client and the client submodule are deferred so that server-only users
# don't pay for the client import chain. Do not convert back to top-level.


def __getattr__(name: str) -> object:
    if name == "Client":
        try:
            from fastmcp.client import Client
        except ImportError as exc:
            raise ImportError(
                "FastMCP client support is not installed. Install "
                "`fastmcp-slim[client]` or `fastmcp`."
            ) from exc

        return Client
    if name == "Context":
        try:
            from fastmcp.server.context import Context
        except ImportError as exc:
            raise ImportError(
                "FastMCP server support is not installed. Install "
                "`fastmcp-slim[server]` or `fastmcp`."
            ) from exc

        return Context
    if name == "FastMCP":
        try:
            from fastmcp.server.server import FastMCP
        except ImportError as exc:
            raise ImportError(
                "FastMCP server support is not installed. Install "
                "`fastmcp-slim[server]` or `fastmcp`."
            ) from exc

        return FastMCP
    if name == "FastMCPApp":
        try:
            from fastmcp.apps.app import FastMCPApp
        except ImportError as exc:
            raise ImportError(
                "FastMCP app support is not installed. Install "
                "`fastmcp-slim[server,apps]` or `fastmcp[apps]`."
            ) from exc

        return FastMCPApp
    if name == "FastMCPDeprecationWarning":
        from fastmcp.exceptions import FastMCPDeprecationWarning

        return FastMCPDeprecationWarning
    if name == "client":
        try:
            return importlib.import_module("fastmcp.client")
        except ImportError as exc:
            raise ImportError(
                "FastMCP client support is not installed. Install "
                "`fastmcp-slim[client]` or `fastmcp`."
            ) from exc
    if name == "server":
        try:
            return importlib.import_module("fastmcp.server")
        except ImportError as exc:
            raise ImportError(
                "FastMCP server support is not installed. Install "
                "`fastmcp-slim[server]` or `fastmcp`."
            ) from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Client",
    "Context",
    "FastMCP",
    "FastMCPApp",
    "FastMCPDeprecationWarning",
    "settings",
]
