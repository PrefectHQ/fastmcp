"""Memoize callable metadata without making the callable process-owned."""

import inspect
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from threading import RLock
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")
_CACHE_ATTRIBUTE = "__fastmcp_callable_cache__"


@dataclass
class _CallableCache:
    owner: weakref.ReferenceType[Any] | None
    values: dict[tuple[Any, ...], Any] = field(default_factory=dict)

    def __reduce__(self) -> tuple[type, tuple[None]]:
        # Tasks can pickle user functions. Metadata is process-local and may
        # contain weakrefs or adapters; a restored callable builds it afresh.
        return type(self), (None,)


def cache_on_callable(build: Callable[P, R]) -> Callable[P, R]:
    """Cache on the first argument, or its instance for a bound method.

    Wrapper and TypeAdapter values can refer back to their callable, so a weak
    key alone cannot prevent retention. Owning the values on the callable makes
    the whole cycle collectable. Callables without a writable namespace or weak
    reference support are evaluated uncached instead of retained globally.
    """
    first_parameter = next(iter(inspect.signature(build).parameters))
    lock = RLock()

    @wraps(build)
    def cached(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            target = args[0]
        except IndexError:
            if first_parameter not in kwargs:
                return build(*args, **kwargs)
            target = kwargs[first_parameter]
        if inspect.ismethod(target):
            method = target.__func__
            owner = target.__self__
        else:
            method = None
            owner = target
        options = tuple((k, v) for k, v in kwargs.items() if k != first_parameter)
        key = (build, method, args[1:], options)
        try:
            namespace = vars(owner)
            owner_ref = weakref.ref(owner)
        except TypeError:
            return build(*args, **kwargs)

        with lock:
            cache = namespace.get(_CACHE_ATTRIBUTE)
            # Function wrappers often copy __dict__. Never reuse the source
            # function's metadata on a distinct wrapper with a different schema.
            if (
                not isinstance(cache, _CallableCache)
                or cache.owner is None
                or cache.owner() is not owner
            ):
                cache = _CallableCache(owner_ref)
                try:
                    namespace[_CACHE_ATTRIBUTE] = cache
                except TypeError:
                    return build(*args, **kwargs)
            if key in cache.values:
                return cache.values[key]

        value = build(*args, **kwargs)
        with lock:
            cache.values[key] = value
        return value

    return cached
