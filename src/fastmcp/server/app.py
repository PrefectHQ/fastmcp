"""Backward-compatible re-exports from fastmcp.apps.app.

.. deprecated:: 3.2.0
    Import from ``fastmcp.apps.app`` or ``fastmcp`` instead.
"""

import warnings


def __getattr__(name: str) -> object:
    warnings.warn(
        f"Importing {name!r} from 'fastmcp.server.app' is deprecated. "
        "Use 'fastmcp.apps.app' or 'from fastmcp import FastMCPApp' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from fastmcp.apps import app as _app

    return getattr(_app, name)
