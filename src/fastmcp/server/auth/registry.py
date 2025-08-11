"""Provider registry for FastMCP auth providers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider

# Type variable for auth providers
T = TypeVar("T", bound="AuthProvider")


# Provider Registry
_PROVIDER_REGISTRY: dict[str, type[AuthProvider]] = {}


def register_provider(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator to register an auth provider with a given name.

    Args:
        name: The name to register the provider under (e.g., 'AUTHKIT')

    Returns:
        The decorated class

    Example:
        @register_provider('AUTHKIT')
        class AuthKitProvider(AuthProvider):
            ...
    """

    def decorator(cls: type[T]) -> type[T]:
        _PROVIDER_REGISTRY[name.upper()] = cls
        return cls

    return decorator


def get_registered_provider(name: str) -> type[AuthProvider]:
    """Get a registered provider by name.

    Args:
        name: The provider name (case-insensitive)

    Returns:
        The provider class if found, None otherwise
    """
    if name.upper() in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[name.upper()]
    raise ValueError(f"Provider {name!r} has not been registered.")
