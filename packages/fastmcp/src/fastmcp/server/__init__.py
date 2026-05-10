import importlib

from .context import Context
from .server import FastMCP, create_proxy


def __getattr__(name: str) -> object:
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
