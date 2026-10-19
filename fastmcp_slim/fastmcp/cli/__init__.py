"""FastMCP CLI package."""

try:
    from .cli import app
except ImportError as exc:
    from fastmcp import _install_hints

    raise ImportError(_install_hints.CLI_SUPPORT) from exc
