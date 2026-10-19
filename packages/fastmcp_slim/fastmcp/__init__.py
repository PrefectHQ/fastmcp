"""FastMCP - An ergonomic MCP interface."""

import importlib
import warnings
from importlib.metadata import PackageNotFoundError, version as _version
from typing import TYPE_CHECKING

from fastmcp import _install_hints
from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.settings import Settings
from fastmcp.utilities.logging import configure_logging as _configure_logging

if TYPE_CHECKING:
    from fastmcp.client import Client as Client
    from fastmcp.client.group import ClientGroup as ClientGroup
    from fastmcp.apps.app import FastMCPApp as FastMCPApp
    from fastmcp.server.context import Context as Context
    from fastmcp.server.server import FastMCP as FastMCP

settings = Settings()
if settings.log_enabled:
    _configure_logging(
        level=settings.log_level,
        enable_rich_tracebacks=settings.enable_rich_tracebacks,
    )

# Install camelCase compatibility shims for MCP SDK v2's snake_case rename.
# Installed unconditionally; each shim's getter checks the live
# `mcp_camelcase_compat` setting at read time, so the bridge can be toggled at
# runtime. Patches only mcp_types model classes, no client chain.
from fastmcp import _compat

_compat.install()

try:
    __version__ = _version("fastmcp-slim")
except PackageNotFoundError:
    __version__ = _version("fastmcp")

if settings.deprecation_warnings:
    warnings.simplefilter("default", FastMCPDeprecationWarning)


# --- Lazy imports for performance (see #3292) ---
# Client exports and the client submodule are deferred so that server-only users
# don't pay for the client import chain. Do not make these eager module imports.


def __getattr__(name: str) -> object:
    if name == "Client":
        try:
            from fastmcp.client import Client
        except ImportError as exc:
            raise ImportError(_install_hints.CLIENT_SUPPORT) from exc

        return Client
    if name == "ClientGroup":
        try:
            from fastmcp.client.group import ClientGroup
        except ImportError as exc:
            raise ImportError(_install_hints.CLIENT_SUPPORT) from exc

        return ClientGroup
    if name == "Context":
        try:
            from fastmcp.server.context import Context
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc

        return Context
    if name == "FastMCP":
        try:
            from fastmcp.server.server import FastMCP
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc

        return FastMCP
    if name == "FastMCPApp":
        try:
            from fastmcp.apps.app import FastMCPApp
        except ImportError as exc:
            raise ImportError(_install_hints.APP_SUPPORT) from exc

        return FastMCPApp
    if name == "client":
        try:
            return importlib.import_module("fastmcp.client")
        except ImportError as exc:
            raise ImportError(_install_hints.CLIENT_SUPPORT) from exc
    if name == "server":
        try:
            return importlib.import_module("fastmcp.server")
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Client",
    "ClientGroup",
    "Context",
    "FastMCP",
    "FastMCPApp",
    "FastMCPDeprecationWarning",
    "settings",
]
