"""Backward-compatible re-exports from fastmcp.apps.

.. deprecated:: 3.2.0
    Import from ``fastmcp.apps`` instead.
"""

import warnings


def __getattr__(name: str) -> object:
    warnings.warn(
        f"Importing {name!r} from 'fastmcp.server.apps' is deprecated. "
        "Use 'from fastmcp.apps import ...' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from fastmcp.apps import config as _config

    return getattr(_config, name)
