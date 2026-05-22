"""Python stdio bridge for remote MCP servers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fastmcp-remote")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
