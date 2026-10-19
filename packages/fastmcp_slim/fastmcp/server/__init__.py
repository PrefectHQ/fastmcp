import importlib
from typing import TYPE_CHECKING

from fastmcp import _install_hints

if TYPE_CHECKING:
    from .context import Context as Context
    from .server import FastMCP as FastMCP
    from .server import create_proxy as create_proxy


def __getattr__(name: str) -> object:
    if name in {"context", "dependencies"}:
        return importlib.import_module(f"fastmcp.server.{name}")
    if name == "Context":
        try:
            from .context import Context
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc

        return Context
    if name in {"FastMCP", "create_proxy"}:
        try:
            from .server import FastMCP, create_proxy
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc

        return FastMCP if name == "FastMCP" else create_proxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
