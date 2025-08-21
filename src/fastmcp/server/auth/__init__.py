from .auth import (
    OAuthProvider,
    TokenVerifier,
    RemoteAuthProvider,
    AccessToken,
    AuthProvider,
)
from .providers.jwt import JWTVerifier, StaticTokenVerifier
from .oauth_proxy import OAuthProxy


__all__ = [
    "AuthProvider",
    "OAuthProvider",
    "TokenVerifier",
    "JWTVerifier",
    "StaticTokenVerifier",
    "RemoteAuthProvider",
    "AccessToken",
    "OAuthProxy",
]


def __getattr__(name: str):
    # Defer import because it raises a deprecation warning
    if name == "BearerAuthProvider":
        from .providers.bearer import BearerAuthProvider

        return BearerAuthProvider
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
