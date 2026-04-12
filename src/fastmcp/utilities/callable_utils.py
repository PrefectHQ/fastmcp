"""Utilities for handling callables, including functools.partial objects."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeGuard


def is_callable_object(obj: Any) -> TypeGuard[Callable[..., Any]]:
    """Check if an object is a callable suitable for use as a tool, resource, or prompt.

    Returns True for functions, methods, builtins, and functools.partial objects.
    This is a broader check than ``inspect.isroutine`` which returns False for
    functools.partial.
    """
    return inspect.isroutine(obj) or isinstance(obj, functools.partial)


def get_callable_name(fn: Any) -> str:
    """Extract a human-readable name from a callable.

    Handles functions, callable classes, and functools.partial:

    - Regular functions: returns ``fn.__name__`` (e.g. ``"add"``)
    - Callable classes: returns the class name (e.g. ``"MyTool"``)
    - Partial with ``update_wrapper``: returns the wrapped name (e.g. ``"add"``)
    - Partial without ``update_wrapper``: returns the underlying function name
      (e.g. ``"add"`` instead of ``"partial"``)
    """
    name = getattr(fn, "__name__", None)
    if name is not None:
        return name
    # functools.partial without update_wrapper — use the underlying function's name
    if isinstance(fn, functools.partial):
        return getattr(fn.func, "__name__", None) or fn.__class__.__name__
    return fn.__class__.__name__


def prepare_callable(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Prepare a callable for introspection by ``inspect.signature()`` and Pydantic.

    This handles three cases that would otherwise require special-casing in every
    ``from_function`` method:

    1. **functools.partial with __wrapped__**: ``functools.update_wrapper`` sets
       ``__wrapped__`` which causes ``inspect.signature()`` and Pydantic to follow
       it back to the original function, ignoring the partial's bound arguments.
       We strip ``__wrapped__`` by reconstructing the partial.

    2. **Callable classes**: Non-routine callables (classes with ``__call__``) need
       to be unwrapped to their ``__call__`` method so ``inspect.signature()`` sees
       the right parameters.

    3. **staticmethod**: Needs unwrapping to the underlying function.

    Call this AFTER extracting name/doc from the original callable, since this
    may change what ``__name__`` and ``__doc__`` return.
    """
    # Strip __wrapped__ from partials so Pydantic sees the partial's own
    # signature with bound args removed, not the original function's signature.
    if isinstance(fn, functools.partial) and hasattr(fn, "__wrapped__"):
        fn = functools.partial(fn.func, *fn.args, **fn.keywords)

    # Callable classes (not routines, not partials) → unwrap to __call__
    if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
        fn = fn.__call__

    # staticmethod → unwrap to underlying function
    if isinstance(fn, staticmethod):
        fn = fn.__func__

    return fn
