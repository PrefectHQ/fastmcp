"""FastMCP Apps — interactive UIs for MCP tools.

This package contains the app-related components:

- ``FastMCPApp`` — composable provider for interactive apps with backend tools
- ``AppConfig`` — configuration for MCP App tools and resources
- ``ResourceCSP`` / ``ResourcePermissions`` — security configuration
"""

from typing import TYPE_CHECKING as _TYPE_CHECKING

from fastmcp.apps.config import AppConfig as AppConfig
from fastmcp.apps.config import PrefabAppConfig as PrefabAppConfig
from fastmcp.apps.config import ResourceCSP as ResourceCSP
from fastmcp.apps.config import ResourcePermissions as ResourcePermissions
from fastmcp.apps.config import UI_EXTENSION_ID as UI_EXTENSION_ID
from fastmcp.apps.config import app_config_to_meta_dict as app_config_to_meta_dict
from fastmcp.utilities.mime import UI_MIME_TYPE as UI_MIME_TYPE
from fastmcp.utilities.mime import resolve_ui_mime_type as resolve_ui_mime_type

__all__ = [
    "UI_EXTENSION_ID",
    "UI_MIME_TYPE",
    "AppConfig",
    "FastMCPApp",
    "PrefabAppConfig",
    "ResourceCSP",
    "ResourcePermissions",
    "app_config_to_meta_dict",
    "resolve_ui_mime_type",
]

if _TYPE_CHECKING:
    from fastmcp.apps.app import FastMCPApp as FastMCPApp


def __getattr__(name: str) -> object:
    if name == "FastMCPApp":
        from fastmcp.apps.app import FastMCPApp

        return FastMCPApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
