"""FastMCP client package."""

import warnings
from importlib.metadata import version as _version

from fastmcp_client.client import Client
from fastmcp_client.exceptions import FastMCPDeprecationWarning
from fastmcp_client.settings import Settings
from fastmcp_client.utilities.logging import configure_logging as _configure_logging

settings = Settings()
if settings.log_enabled:
    _configure_logging(
        level=settings.log_level,
        enable_rich_tracebacks=settings.enable_rich_tracebacks,
    )

__version__ = _version("fastmcp-client")

if settings.deprecation_warnings:
    warnings.simplefilter("default", FastMCPDeprecationWarning)

__all__ = ["Client", "FastMCPDeprecationWarning", "settings"]
