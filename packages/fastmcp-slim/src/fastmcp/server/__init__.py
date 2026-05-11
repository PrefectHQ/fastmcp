import importlib

try:
    from .context import Context
    from .server import FastMCP, create_proxy
except ImportError as exc:
    raise ImportError(
        "FastMCP server support is not installed. Install "
        "`fastmcp-slim[server]` or `fastmcp`."
    ) from exc


def __getattr__(name: str) -> object:
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
