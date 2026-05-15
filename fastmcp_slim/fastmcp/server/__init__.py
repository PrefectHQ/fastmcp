import importlib

from fastmcp import _install_hints


def __getattr__(name: str) -> object:
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
        return {"FastMCP": FastMCP, "create_proxy": create_proxy}[name]
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
