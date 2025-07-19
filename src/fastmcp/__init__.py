"""FastMCP - An ergonomic MCP interface."""

import warnings
from importlib.metadata import version as _version
from fastmcp.settings import Settings
from fastmcp.utilities.logging import configure_logging as _configure_logging

settings = Settings()
_configure_logging(
    level=settings.log_level,
    enable_rich_tracebacks=settings.enable_rich_tracebacks,
)

from fastmcp.server.server import FastMCP
from fastmcp.server.context import Context
import fastmcp.server

from fastmcp.client import Client
from . import client

__version__ = _version("fastmcp")


# ensure deprecation warnings are displayed by default
if settings.deprecation_warnings:
    warnings.simplefilter("default", DeprecationWarning)


def __getattr__(name: str):
    """
    Used to deprecate the module-level Image class; can be removed once it is no longer imported to root.
    """
    if name == "Image":
        # Deprecated in 2.8.1
        if settings.deprecation_warnings:
            warnings.warn(
                "The top-level `fastmcp.Image` import is deprecated "
                "and will be removed in a future version. "
                "Please use `fastmcp.utilities.types.Image` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        from fastmcp.utilities.types import Image

        return Image
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "FastMCP",
    "Context",
    "client",
    "Client",
    "settings",
]
