from .auth import (
    OAuthProvider,
    TokenVerifier,
    RemoteAuthProvider,
    AccessToken,
    AuthProvider,
)
from .providers.debug import DebugTokenVerifier
from .providers.jwt import JWTVerifier, StaticTokenVerifier
from .oauth_proxy import OAuthProxy
from .oidc_proxy import OIDCProxy


__all__ = [
    "AccessToken",
    "AuthProvider",
    "DebugTokenVerifier",
    "JWTVerifier",
    "OAuthProvider",
    "OAuthProxy",
    "OIDCProxy",
    "RemoteAuthProvider",
    "StaticTokenVerifier",
    "TokenVerifier",
]


def __getattr__(name: str):
    # Defer import because it raises a deprecation warning
    if name == "BearerAuthProvider":
        from .providers.bearer import BearerAuthProvider

        return BearerAuthProvider
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
