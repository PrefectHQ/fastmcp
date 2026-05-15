import importlib
from typing import TYPE_CHECKING

from fastmcp import _install_hints

if TYPE_CHECKING:
    from .context import Context as Context
    from .server import FastMCP as FastMCP
    from .server import create_proxy as create_proxy


def __getattr__(name: str) -> object:
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    if name == "Context":
        try:
            module = importlib.import_module("fastmcp.server.context")
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return module.Context
    if name == "FastMCP":
        try:
            module = importlib.import_module("fastmcp.server.server")
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return module.FastMCP
    if name == "create_proxy":
        try:
            module = importlib.import_module("fastmcp.server.server")
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return module.create_proxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
