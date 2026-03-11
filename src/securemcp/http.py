"""SecureMCP HTTP facade."""

from fastmcp.server.security.http import SecurityAPI, mount_security_routes

__all__ = [
    "SecurityAPI",
    "mount_security_routes",
]
