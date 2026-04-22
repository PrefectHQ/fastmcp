"""Sandbox providers for the CodeMode plugin.

A `SandboxProvider` is the component that actually executes LLM-generated
Python code. The default `MontySandboxProvider` delegates to
`pydantic-monty` for isolated execution; alternative providers can plug in
any other sandbox (remote process, WASM, a containerized worker, etc.)
by implementing the `SandboxProvider` protocol.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastmcp.utilities.async_utils import is_coroutine_function

if TYPE_CHECKING:
    from pydantic_monty import ResourceLimits


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    if is_coroutine_function(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


class SandboxProvider(Protocol):
    """Interface for executing LLM-generated Python code in a sandbox.

    WARNING: The `code` parameter passed to `run` contains untrusted,
    LLM-generated Python. Implementations MUST execute it in an isolated
    sandbox — never with plain `exec()`. Use `MontySandboxProvider`
    (backed by `pydantic-monty`) for production workloads.
    """

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any: ...


class MontySandboxProvider:
    """Sandbox provider backed by `pydantic-monty`.

    Args:
        limits: Resource limits for sandbox execution. Supported keys:
            `max_duration_secs` (float), `max_allocations` (int),
            `max_memory` (int), `max_recursion_depth` (int),
            `gc_interval` (int). All are optional; omit a key to leave
            that limit uncapped.
    """

    def __init__(
        self,
        *,
        limits: ResourceLimits | None = None,
    ) -> None:
        self.limits = limits

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any:
        try:
            pydantic_monty = importlib.import_module("pydantic_monty")
        except ModuleNotFoundError as exc:
            raise ImportError(
                "CodeMode requires pydantic-monty for the Monty sandbox provider. "
                "Install it with `fastmcp[code-mode]` or pass a custom SandboxProvider."
            ) from exc

        inputs = inputs or {}
        async_functions = {
            key: _ensure_async(value)
            for key, value in (external_functions or {}).items()
        }

        monty = pydantic_monty.Monty(code, inputs=list(inputs))
        return await monty.run_async(
            inputs=inputs or None,
            external_functions=async_functions or None,
            limits=self.limits,
        )
