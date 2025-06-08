"""FastMCP - An ergonomic MCP interface."""

from importlib.metadata import version
from fastmcp.settings import Settings

settings = Settings()

from fastmcp.server.server import FastMCP
from fastmcp.server.context import Context
import fastmcp.server

from fastmcp.client import Client
from fastmcp.utilities.types import Image
from . import client

__version__ = version("fastmcp")
__all__ = [
    "FastMCP",
    "Context",
    "client",
    "Client",
    "settings",
    "Image",
]
