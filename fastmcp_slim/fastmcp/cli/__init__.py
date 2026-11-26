"""FastMCP CLI package."""

import contextlib
import sys


def _configure_output_streams() -> None:
    """Make CLI output resilient to characters the stream cannot encode.

    On Windows, redirected stdout/stderr default to the legacy ANSI code page
    (for example cp1252).  Printing characters such as the checkmark used in
    success messages then raises ``UnicodeEncodeError`` and turns a successful
    command into a traceback.  Replacing unencodable characters matches what a
    terminal does and keeps the command usable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Detached or otherwise non-reconfigurable streams (for example test
        # capture objects) are left untouched.
        with contextlib.suppress(OSError, ValueError):
            reconfigure(errors="replace")


_configure_output_streams()

try:
    from .cli import app
except ImportError as exc:
    from fastmcp import _install_hints

    raise ImportError(_install_hints.CLI_SUPPORT) from exc
