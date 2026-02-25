from typing import TYPE_CHECKING

from .auth import (
    OAuthProvider,
    TokenVerifier,
    RemoteAuthProvider,
    AccessToken,
    AuthProvider,
)
from .authorization import (
    AuthCheck,
    AuthContext,
    require_scopes,
    restrict_tag,
    run_auth_checks,
)

if TYPE_CHECKING:
    from .oauth_proxy import OAuthProxy as OAuthProxy
    from .oidc_proxy import OIDCProxy as OIDCProxy
    from .providers.debug import DebugTokenVerifier as DebugTokenVerifier
    from .providers.jwt import JWTVerifier as JWTVerifier
    from .providers.jwt import StaticTokenVerifier as StaticTokenVerifier


# --- Lazy imports for performance (see #3292) ---
# These providers pull in heavy deps (authlib, cryptography, key_value.aio,
# beartype) that most users never need. Keeping them behind __getattr__
# avoids ~150ms+ of import overhead for the common server-only case.
# Do not convert these back to top-level imports.


def __getattr__(name: str) -> object:
    if name == "DebugTokenVerifier":
        from .providers.debug import DebugTokenVerifier

        return DebugTokenVerifier
    if name == "JWTVerifier":
        from .providers.jwt import JWTVerifier

        return JWTVerifier
    if name == "StaticTokenVerifier":
        from .providers.jwt import StaticTokenVerifier

        return StaticTokenVerifier
    if name == "OAuthProxy":
        from .oauth_proxy import OAuthProxy

        return OAuthProxy
    if name == "OIDCProxy":
        from .oidc_proxy import OIDCProxy

        return OIDCProxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AccessToken",
    "AuthCheck",
    "AuthContext",
    "AuthProvider",
    "DebugTokenVerifier",
    "JWTVerifier",
    "OAuthProvider",
    "OAuthProxy",
    "OIDCProxy",
    "RemoteAuthProvider",
    "StaticTokenVerifier",
    "TokenVerifier",
    "require_scopes",
    "restrict_tag",
    "run_auth_checks",
]
