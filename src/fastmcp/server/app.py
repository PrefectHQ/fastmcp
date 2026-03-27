"""Backward-compatible re-exports from fastmcp.apps.app.

.. deprecated:: 3.2.0
    Import from ``fastmcp.apps.app`` or ``fastmcp`` instead.
"""

import warnings

from fastmcp.apps.app import FastMCPApp as FastMCPApp
from fastmcp.apps.app import _dispatch_decorator as _dispatch_decorator
from fastmcp.apps.app import _resolve_tool_ref as _resolve_tool_ref
from fastmcp.exceptions import FastMCPDeprecationWarning

warnings.warn(
    "'fastmcp.server.app' is deprecated. "
    "Use 'fastmcp.apps.app' or 'from fastmcp import FastMCPApp' instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)
