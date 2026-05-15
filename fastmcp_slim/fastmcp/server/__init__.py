import importlib
from typing import TYPE_CHECKING

from fastmcp import _install_hints

if TYPE_CHECKING:
    from .context import Context as Context
    from .server import FastMCP as FastMCP
    from .server import create_proxy as create_proxy


# --- Lazy imports to avoid circular imports (see #4147) ---
# Eagerly importing Context/FastMCP at package init time pulls in the full
# server import chain, which can re-enter partially-initialized modules
# (e.g. fastmcp.tools.function_tool) and raise a misleading ImportError.
# Defer these to __getattr__ so submodule imports like
# `fastmcp.server.tasks.config` don't trigger the heavy import chain.
def __getattr__(name: str) -> object:
    if name == "Context":
        try:
            from .context import Context
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return Context
    if name == "FastMCP":
        try:
            from .server import FastMCP
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return FastMCP
    if name == "create_proxy":
        try:
            from .server import create_proxy
        except ImportError as exc:
            raise ImportError(_install_hints.SERVER_SUPPORT) from exc
        return create_proxy
    if name == "dependencies":
        return importlib.import_module("fastmcp.server.dependencies")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Context", "FastMCP", "create_proxy"]
