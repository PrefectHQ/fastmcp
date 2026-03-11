"""SecureMCP - trust-native server facade built on FastMCP."""

from importlib.metadata import version as _version

from securemcp.config import SecurityConfig
from securemcp.http import SecurityAPI, mount_security_routes
from securemcp.integration import (
    attach_security,
    attach_security_context,
    get_security_context,
    register_security_gateway_tools,
)
from securemcp.server import SecureMCP
from securemcp.settings import SecuritySettings, get_security_settings

__version__ = _version("fastmcp")

__all__ = [
    "SecureMCP",
    "SecurityAPI",
    "SecurityConfig",
    "SecuritySettings",
    "attach_security",
    "attach_security_context",
    "get_security_context",
    "get_security_settings",
    "mount_security_routes",
    "register_security_gateway_tools",
]
