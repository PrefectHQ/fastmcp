import importlib

from fastmcp import _install_hints

try:
    from .context import Context
    from .server import FastMCP, create_proxy
except ImportError as exc:
    raise ImportError(_install_hints.SERVER_SUPPORT) from exc


def __getattr__(name: str) -> object:
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
