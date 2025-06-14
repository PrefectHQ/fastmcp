"""FastMCP - An ergonomic MCP interface."""

import warnings
from importlib.metadata import version
from fastmcp.settings import Settings

settings = Settings()

from fastmcp.server.server import FastMCP
from fastmcp.server.context import Context
import fastmcp.server

from fastmcp.client import Client
from . import client

__version__ = version("fastmcp")


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
