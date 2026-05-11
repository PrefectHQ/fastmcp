"""OAuth Proxy Provider for FastMCP.

This package provides OAuth proxy functionality split across multiple modules:
- models: Pydantic models and constants
- ui: HTML generation functions
- consent: Consent management mixin
- proxy: Main OAuthProxy class
"""

from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy

__all__ = [
    "OAuthProxy",
]
