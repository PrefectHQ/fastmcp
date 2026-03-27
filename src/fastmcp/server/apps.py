"""Backward-compatible re-exports from fastmcp.apps.

.. deprecated:: 3.2.0
    Import from ``fastmcp.apps`` instead.
"""

import warnings

from fastmcp.apps.config import UI_EXTENSION_ID as UI_EXTENSION_ID
from fastmcp.apps.config import AppConfig as AppConfig
from fastmcp.apps.config import ResourceCSP as ResourceCSP
from fastmcp.apps.config import ResourcePermissions as ResourcePermissions
from fastmcp.apps.config import app_config_to_meta_dict as app_config_to_meta_dict
from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.utilities.mime import UI_MIME_TYPE as UI_MIME_TYPE
from fastmcp.utilities.mime import resolve_ui_mime_type as resolve_ui_mime_type

warnings.warn(
    "'fastmcp.server.apps' is deprecated. Use 'from fastmcp.apps import ...' instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)
