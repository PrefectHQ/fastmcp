from .auth import OAuthProvider, TokenVerifier
from .verifiers import IntrospectionTokenVerifier, JWTVerifier, StaticTokenVerifier


__all__ = [
    "OAuthProvider",
    "TokenVerifier",
    "IntrospectionTokenVerifier",
    "JWTVerifier",
    "StaticTokenVerifier",
]


def __getattr__(name: str):
    # Defer import because it raises a deprecation warning
    if name == "BearerAuthProvider":
        from .providers.bearer import BearerAuthProvider

        return BearerAuthProvider
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
