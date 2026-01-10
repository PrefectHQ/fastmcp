"""Vendored dependency injection engine from Docket.

This is a minimal subset of docket.dependencies for FastMCP's DI system.
When docket is installed, FastMCP uses docket's classes directly for
isinstance compatibility. This vendored version is only used when docket
is not installed.

Original source: https://github.com/chrisguidry/docket
License: MIT
"""

from __future__ import annotations

import abc
import inspect
from contextlib import AsyncExitStack
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import (
    Any,
    Generic,
    TypeVar,
    cast,
)

R = TypeVar("R")

# Cached signature lookup (simplified from docket.execution.get_signature)
_signature_cache: dict[Callable[..., Any], inspect.Signature] = {}


def get_signature(function: Callable[..., Any]) -> inspect.Signature:
    """Get cached signature for a function."""
    if function in _signature_cache:
        return _signature_cache[function]

    signature_attr = getattr(function, "__signature__", None)
    if isinstance(signature_attr, inspect.Signature):
        _signature_cache[function] = signature_attr
        return signature_attr

    signature = inspect.signature(function)
    _signature_cache[function] = signature
    return signature


class Dependency(abc.ABC):
    """Base class for all dependencies.

    Subclasses must implement __aenter__ to provide the dependency value.
    The __aexit__ method is optional for cleanup.
    """

    single: bool = False

    @abc.abstractmethod
    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, *args: object) -> None:  # noqa: B027
        pass


DependencyFunction = Callable[..., Any]

_parameter_cache: dict[Callable[..., Any], dict[str, Dependency]] = {}


def get_dependency_parameters(
    function: Callable[..., Any],
) -> dict[str, Dependency]:
    """Find parameters with Dependency defaults."""
    if function in _parameter_cache:
        return _parameter_cache[function]

    dependencies: dict[str, Dependency] = {}
    signature = get_signature(function)

    for parameter, param in signature.parameters.items():
        if not isinstance(param.default, Dependency):
            continue
        dependencies[parameter] = param.default

    _parameter_cache[function] = dependencies
    return dependencies


class _Depends(Dependency, Generic[R]):
    """Wrapper for user-defined dependency functions."""

    dependency: DependencyFunction

    cache: ContextVar[dict[DependencyFunction, Any]] = ContextVar("cache")
    stack: ContextVar[AsyncExitStack] = ContextVar("stack")

    def __init__(self, dependency: DependencyFunction) -> None:
        self.dependency = dependency

    async def _resolve_parameters(self, function: DependencyFunction) -> dict[str, Any]:
        stack = self.stack.get()
        arguments: dict[str, Any] = {}
        parameters = get_dependency_parameters(function)

        for parameter, dependency in parameters.items():
            arguments[parameter] = await stack.enter_async_context(dependency)

        return arguments

    async def __aenter__(self) -> R:
        cache = self.cache.get()

        if self.dependency in cache:
            return cache[self.dependency]

        stack = self.stack.get()
        arguments = await self._resolve_parameters(self.dependency)

        raw_value = self.dependency(**arguments)

        # Handle different return types
        resolved_value: R
        if isinstance(raw_value, AbstractAsyncContextManager):
            resolved_value = await stack.enter_async_context(raw_value)
        elif isinstance(raw_value, AbstractContextManager):
            resolved_value = stack.enter_context(raw_value)
        elif inspect.iscoroutine(raw_value) or isinstance(raw_value, Awaitable):
            resolved_value = await cast(Awaitable[R], raw_value)
        else:
            resolved_value = cast(R, raw_value)

        cache[self.dependency] = resolved_value
        return resolved_value


def Depends(dependency: DependencyFunction) -> Any:
    """Include a user-defined function as a dependency.

    Dependencies may be:
    - Synchronous functions returning a value
    - Asynchronous functions returning a value (awaitable)
    - Synchronous context managers (using @contextmanager)
    - Asynchronous context managers (using @asynccontextmanager)

    Example:
        ```python
        def get_config() -> dict:
            return {"api_url": "https://api.example.com"}

        @mcp.tool
        def my_tool(config: dict = Depends(get_config)) -> str:
            return config["api_url"]
        ```
    """
    return cast(Any, _Depends(dependency))


__all__ = [
    "Dependency",
    "Depends",
    "_Depends",
    "get_dependency_parameters",
    "get_signature",
]
