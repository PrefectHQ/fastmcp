"""Docket-touching component logic relocated from core component classes.

During the SEP-1686 -> SEP-2663 migration the ``register_with_docket`` /
``add_to_docket`` / ``coerce_task_arguments`` methods were removed from the core
``FastMCPComponent`` classes (Tool, Resource, ResourceTemplate, Prompt). Their
bodies live here as type-dispatched functions that ``TasksExtension`` wires into
the Docket engine, preserving each type's calling convention.

The functions dispatch on the concrete component type because each type splats
its arguments differently into the Docket-registered callable:

- ``FunctionTool``/``FunctionResource``/``FunctionResourceTemplate``/``FunctionPrompt``
  register the raw ``fn`` so Docket resolves ALL dependencies (FastMCP's and
  Docket-native), and splat their arguments (``**kwargs``) into it.
- Base ``Tool``/``Resource``/``ResourceTemplate``/``Prompt`` register their
  ``run``/``read``/``render`` entry point and pass arguments positionally.

Only tools carry a task-capable ``task_config`` (SEP-2663 is tools-only); the
resource/prompt/template branches are retained for engine completeness, not
because core still declares them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from fastmcp.exceptions import ValidationError
from fastmcp.prompts.base import Prompt
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.resources.base import Resource
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.resources.template import FunctionResourceTemplate, ResourceTemplate
from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import FunctionTool, _resolve_param_hints
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.types import get_cached_typeadapter
from fastmcp_tasks.input_loop import reentrant_task_fn

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution


def register_component_with_docket(component: FastMCPComponent, docket: Docket) -> None:
    """Register a component's callable with Docket for background execution.

    No-ops if ``task_config.mode`` is ``forbidden``. Function-backed components
    register their raw ``fn`` (so Docket resolves all dependencies); base
    components register their ``run``/``read``/``render`` entry point.
    """
    if not component.task_config.supports_tasks():
        return

    if isinstance(component, FunctionTool):
        # Run the tool through the guard loop so a body that returns an
        # InputRequiredResult drives the reentrant in-task input cycle. The
        # wrapper is signature-preserving, so Docket's dependency injection is
        # unchanged for a body that never asks for input.
        docket.register(
            reentrant_task_fn(component.fn, component.name), names=[component.key]
        )
    elif isinstance(component, Tool):
        # Custom Tool subclasses route through the same wrapper so a raised
        # error becomes a masked, completed `is_error` result — matching the
        # synchronous `tools/call` path — rather than a Docket `FAILED` task
        # that leaks the raw exception text past the server's masking policy.
        docket.register(
            reentrant_task_fn(component.run, component.name), names=[component.key]
        )
    elif isinstance(component, FunctionResource):
        docket.register(component.fn, names=[component.key])
    elif isinstance(component, FunctionResourceTemplate):
        docket.register(component.fn, names=[component.key])
    elif isinstance(component, ResourceTemplate):
        docket.register(component.read, names=[component.key])
    elif isinstance(component, Resource):
        docket.register(component.read, names=[component.key])
    elif isinstance(component, FunctionPrompt):
        docket.register(component.fn, names=[component.key])
    elif isinstance(component, Prompt):
        docket.register(component.render, names=[component.key])
    else:
        raise NotImplementedError(
            f"{type(component).__name__} does not support Docket registration"
        )


async def add_component_to_docket(
    component: FastMCPComponent,
    docket: Docket,
    arguments: dict[str, Any] | None,
    *,
    fn_key: str | None = None,
    task_key: str | None = None,
    **kwargs: Any,
) -> Execution:
    """Schedule a component for background execution via Docket.

    Handles each component type's calling convention:

    - ``FunctionTool``: splats the arguments dict (``.fn`` expects ``**kwargs``).
    - base ``Tool``: passes the arguments dict positionally.
    - ``Resource`` (any): no arguments.
    - ``FunctionResourceTemplate``: splats the params dict.
    - base ``ResourceTemplate``: passes params positionally.
    - ``FunctionPrompt``: splats the arguments dict (or empty).
    - base ``Prompt``: passes arguments positionally.
    """
    if not component.task_config.supports_tasks():
        raise RuntimeError(
            f"Cannot add {type(component).__name__} '{component.name}' to docket: "
            f"task execution not supported"
        )

    lookup_key = fn_key or component.key
    if task_key:
        kwargs["key"] = task_key
    adder = docket.add(lookup_key, **kwargs)

    if isinstance(component, FunctionTool):
        return await adder(**(arguments or {}))
    elif isinstance(component, Tool):
        return await adder(arguments)
    elif isinstance(component, Resource):
        return await adder()
    elif isinstance(component, FunctionResourceTemplate):
        return await adder(**(arguments or {}))
    elif isinstance(component, ResourceTemplate):
        return await adder(arguments)
    elif isinstance(component, FunctionPrompt):
        return await adder(**(arguments or {}))
    elif isinstance(component, Prompt):
        return await adder(arguments)
    else:
        raise NotImplementedError(
            f"{type(component).__name__} does not implement add_to_docket()"
        )


def coerce_task_arguments(
    component: FastMCPComponent,
    arguments: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate and coerce task arguments before any task state is created.

    Called by ``submit_to_docket`` up front, so invalid inputs raise before the
    task's Redis metadata and initial status notification exist — otherwise a
    coercion failure during queueing would orphan a task the client has already
    observed. Only ``FunctionTool`` splats arguments into a typed Python callable
    and therefore mirrors the synchronous validation path; every other component
    type is a no-op passthrough.

    When ``strict`` is set (server-level ``strict_input_validation``), arguments
    are validated in strict mode so the task path rejects lax coercions (e.g. the
    string ``"1"`` into an ``int``) exactly as the synchronous call path does.
    """
    if not isinstance(component, FunctionTool):
        return arguments

    from fastmcp.server.dependencies import without_injected_parameters

    wrapper_fn = without_injected_parameters(
        component.fn, run_in_thread=component.run_in_thread
    )
    hints = _resolve_param_hints(wrapper_fn)

    coerced = dict(arguments)
    for name, value in arguments.items():
        annotation = hints.get(name)
        if annotation is None:
            continue
        adapter = get_cached_typeadapter(annotation)
        try:
            coerced[name] = adapter.validate_python(value, strict=strict)
        except PydanticValidationError as e:
            raise ValidationError(str(e), log_level=logging.WARNING) from e
    return coerced
