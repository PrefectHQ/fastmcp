"""Standalone @tool decorator for FastMCP."""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from types import MethodType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    Protocol,
    TypeVar,
    cast,
    get_type_hints,
    overload,
    runtime_checkable,
)

import anyio
from mcp.shared.exceptions import MCPError
from mcp_types import Icon, ToolAnnotations
from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from pydantic.json_schema import SkipJsonSchema

from fastmcp.decorators import get_fastmcp_meta
from fastmcp.exceptions import ValidationError
from fastmcp.tools.base import (
    Tool,
    ToolResult,
)
from fastmcp.tools.function_parsing import ParsedFunction, _is_object_schema
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TaskConfig
from fastmcp.utilities.types import (
    NotSet,
    NotSetT,
    get_cached_typeadapter,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution


class _ToolBodyError(Exception):
    """Marks a ``pydantic.ValidationError`` raised while executing a tool's body.

    Pydantic validates a tool's arguments *before* invoking the body, so a bare
    ``pydantic.ValidationError`` surfacing from the call adapter is unambiguously
    an argument-validation failure (a bad call). Errors a tool raises from its
    own body — e.g. constructing a model from upstream data — are a different
    class of problem (a server-side bug) that must not be reclassified as a bad
    call. We wrap the body so those are tagged and can be told apart. See #4128.
    """


@lru_cache(maxsize=5000)
def _wrap_body_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` so a ``pydantic.ValidationError`` raised by its body is
    re-raised as ``_ToolBodyError``.

    The wrapper preserves ``fn``'s signature and annotations so the cached
    ``TypeAdapter`` validates arguments identically — only body execution is
    affected. Argument validation happens before the wrapper is called, so it
    keeps raising a bare ``pydantic.ValidationError``.
    """
    if is_coroutine_function(fn):

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except PydanticValidationError as e:
                raise _ToolBodyError from e
    else:

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except PydanticValidationError as e:
                raise _ToolBodyError from e

    # Mirror the original callable so TypeAdapter builds the identical schema and
    # binds arguments the same way. Annotations must cover every signature
    # parameter or pydantic's call-schema generation raises KeyError — so prefer
    # resolved type hints (handles string/forward refs) but fall back to the
    # signature's own annotations, which is the only source for callables like
    # functools.partial that carry no __annotations__.
    try:
        resolved_hints = get_type_hints(fn, include_extras=True)
    except Exception:
        resolved_hints = {}
    sig = inspect.signature(fn)
    annotations: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        if param_name in resolved_hints:
            annotations[param_name] = resolved_hints[param_name]
        elif param.annotation is not inspect.Parameter.empty:
            annotations[param_name] = param.annotation
    if "return" in resolved_hints:
        annotations["return"] = resolved_hints["return"]
    elif sig.return_annotation is not inspect.Signature.empty:
        annotations["return"] = sig.return_annotation

    wrapper.__signature__ = sig  # type: ignore[attr-defined]  # ty: ignore[invalid-assignment]
    wrapper.__annotations__ = annotations
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    wrapper.__doc__ = getattr(fn, "__doc__", None)
    wrapper.__module__ = getattr(fn, "__module__", wrapper.__module__)
    wrapper.__qualname__ = getattr(fn, "__qualname__", wrapper.__qualname__)
    return wrapper


def _strict_input_validation() -> bool:
    """Whether the running server enforces strict argument validation.

    Reads ``strict_input_validation`` off the active request's ``FastMCP``
    instance. Returns ``False`` outside a request context (e.g. a tool invoked
    directly in tests), preserving the default coercing behavior.
    """
    from fastmcp.server.context import _current_context

    context = _current_context.get(None)
    if context is None:
        return False
    return context.fastmcp.strict_input_validation


F = TypeVar("F", bound=Callable[..., Any])


@runtime_checkable
class DecoratedTool(Protocol):
    """Protocol for functions decorated with @tool."""

    __fastmcp__: ToolMeta

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class ToolMeta:
    """Metadata attached to functions by the @tool decorator."""

    type: Literal["tool"] = field(default="tool", init=False)
    name: str | None = None
    version: str | int | None = None
    title: str | None = None
    description: str | None = None
    icons: list[Icon] | None = None
    tags: set[str] | None = None
    output_schema: dict[str, Any] | NotSetT | None = NotSet
    annotations: ToolAnnotations | None = None
    meta: dict[str, Any] | None = None
    app: Any = None
    task: bool | TaskConfig | None = None
    timeout: float | None = None
    auth: AuthCheck | list[AuthCheck] | None = None
    enabled: bool = True
    run_in_thread: bool = True


def _resolve_param_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Resolve a callable's parameter type hints, tolerating partials.

    ``get_type_hints`` rejects ``functools.partial`` objects (and other
    non-function callables), which the synchronous TypeAdapter path handles
    natively. For those, resolve hints against the underlying function and keep
    only the parameters that remain in the partially-bound signature.
    """
    try:
        return get_type_hints(fn, include_extras=True)
    except TypeError:
        target = fn
        while isinstance(target, functools.partial):
            target = target.func
        try:
            resolved = get_type_hints(target, include_extras=True)
        except TypeError:
            return {}
        return {
            name: resolved[name]
            for name in inspect.signature(fn).parameters
            if name in resolved
        }


class FunctionTool(Tool):
    fn: SkipJsonSchema[Callable[..., Any]]
    return_type: Annotated[SkipJsonSchema[Any], Field(exclude=True)] = None
    run_in_thread: Annotated[
        bool,
        Field(
            description=(
                "Applies to sync tool functions only. When True (default), sync "
                "functions are dispatched to a worker thread so they don't block "
                "the event loop. Set to False to run the sync function inline on "
                "the event loop thread — useful for libraries with thread "
                "affinity (e.g. Windows COM, tkinter). Ignored for async functions, "
                "which always run on the event loop. Cannot be combined with "
                "`timeout` on a sync function: inline calls have no cancellation "
                "checkpoints, so the timeout would be a silent no-op."
            )
        ),
    ] = True

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        metadata: ToolMeta | None = None,
        # Keep individual params for backwards compat
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        run_in_thread: bool | None = None,
    ) -> FunctionTool:
        """Create a FunctionTool from a function.

        Args:
            fn: The function to wrap
            metadata: ToolMeta object with all configuration. If provided,
                individual parameters must not be passed.
            name, title, etc.: Individual parameters for backwards compatibility.
                Cannot be used together with metadata parameter.
        """
        # Check mutual exclusion
        individual_params_provided = (
            any(
                x is not None and x is not NotSet
                for x in [
                    name,
                    version,
                    title,
                    description,
                    icons,
                    tags,
                    annotations,
                    meta,
                    task,
                    timeout,
                    auth,
                    run_in_thread,
                ]
            )
            or output_schema is not NotSet
        )

        if metadata is not None and individual_params_provided:
            raise TypeError(
                "Cannot pass both 'metadata' and individual parameters to from_function(). "
                "Use metadata alone or individual parameters alone."
            )

        if metadata is None and not individual_params_provided:
            fmeta = get_fastmcp_meta(fn)
            if isinstance(fmeta, ToolMeta):
                metadata = fmeta

        # Build metadata from kwargs if not provided
        if metadata is None:
            metadata = ToolMeta(
                name=name,
                version=version,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                output_schema=output_schema,
                annotations=annotations,
                meta=meta,
                task=task,
                timeout=timeout,
                auth=auth,
                run_in_thread=True if run_in_thread is None else run_in_thread,
            )

        parsed_fn = ParsedFunction.from_function(fn)
        func_name = metadata.name or parsed_fn.name

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Inline sync execution has no cancellation checkpoints, so
        # anyio.fail_after cannot preempt the call — the timeout would be
        # silently ignored. Reject the combination so users make an
        # explicit choice. Async generators are async even though
        # is_coroutine_function returns False for them; the generator's
        # iteration has checkpoints, so timeout enforcement still works.
        if (
            metadata.timeout is not None
            and not metadata.run_in_thread
            and not is_coroutine_function(fn)
            and not inspect.isasyncgenfunction(fn)
        ):
            raise ValueError(
                f"Tool {func_name!r}: timeout cannot be enforced when "
                "run_in_thread=False on a sync function. Inline execution has "
                "no cancellation checkpoints, so the timeout would be a no-op. "
                "Either drop the timeout or remove run_in_thread=False and "
                "accept worker-thread dispatch."
            )

        # Normalize task to TaskConfig
        task_value = metadata.task
        if task_value is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task_value, bool):
            task_config = TaskConfig.from_bool(task_value)
        else:
            task_config = task_value
        task_config.validate_function(fn, func_name)

        # Handle output_schema
        if isinstance(metadata.output_schema, NotSetT):
            final_output_schema = parsed_fn.output_schema
        else:
            final_output_schema = metadata.output_schema

        if final_output_schema is not None and isinstance(final_output_schema, dict):
            if not _is_object_schema(final_output_schema):
                raise ValueError(
                    f"Output schemas must represent object types due to MCP spec limitations. "
                    f"Received: {final_output_schema!r}"
                )

        return cls(
            fn=parsed_fn.fn,
            return_type=parsed_fn.return_type,
            name=metadata.name or parsed_fn.name,
            version=str(metadata.version) if metadata.version is not None else None,
            title=metadata.title,
            description=metadata.description
            if metadata.description is not None
            else parsed_fn.description,
            icons=metadata.icons,
            parameters=parsed_fn.input_schema,
            output_schema=final_output_schema,
            annotations=metadata.annotations,
            tags=metadata.tags or set(),
            meta=metadata.meta,
            task_config=task_config,
            timeout=metadata.timeout,
            auth=metadata.auth,
            run_in_thread=metadata.run_in_thread,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool with arguments."""
        from fastmcp.server.dependencies import without_injected_parameters

        wrapper_fn = without_injected_parameters(
            self.fn, run_in_thread=self.run_in_thread
        )
        # Tag pydantic errors raised by the body so they can be distinguished
        # from argument-validation errors (which pydantic raises first). See #4128.
        exec_fn = _wrap_body_errors(wrapper_fn)
        type_adapter = get_cached_typeadapter(exec_fn)
        exec_is_async = is_coroutine_function(wrapper_fn)
        strict = _strict_input_validation()

        try:
            if self.timeout is not None:
                try:
                    with anyio.fail_after(self.timeout):
                        result = await self._execute(
                            type_adapter, exec_is_async, arguments, strict=strict
                        )
                except TimeoutError:
                    logger.warning(
                        f"Tool '{self.name}' timed out after {self.timeout}s. "
                        f"Consider using task=True for long-running operations. "
                        f"See https://gofastmcp.com/servers/tasks"
                    )
                    raise MCPError(
                        code=-32000,
                        message=f"Tool '{self.name}' execution timed out after {self.timeout}s",
                    ) from None
            else:
                result = await self._execute(
                    type_adapter, exec_is_async, arguments, strict=strict
                )
        except PydanticValidationError as e:
            # Body errors are re-raised as _ToolBodyError, so a bare pydantic
            # ValidationError here is an argument-validation failure (a bad call).
            # Convert it to fastmcp's ValidationError so the middleware chain and
            # downstream error taxonomy (e.g. Sentry filters) can treat it as a
            # client error rather than a server bug.
            raise ValidationError(str(e), log_level=logging.WARNING) from e
        except _ToolBodyError as e:
            # The tool's own body raised a pydantic ValidationError. Surface the
            # original so it is treated as a server-side error, hiding the
            # internal sentinel while preserving the error's own chained cause.
            original = e.__cause__
            assert original is not None
            raise original from original.__cause__

        return self.convert_result(result)

    async def _execute(
        self,
        type_adapter: TypeAdapter[Any],
        exec_is_async: bool,
        arguments: dict[str, Any],
        *,
        strict: bool = False,
    ) -> Any:
        """Validate arguments and execute the tool body.

        Argument validation runs first and raises a bare
        ``pydantic.ValidationError`` on bad input. Body execution (awaiting the
        result and materializing generators) is wrapped so any pydantic error it
        raises is tagged as ``_ToolBodyError``.

        When ``strict`` is set (server-level ``strict_input_validation``),
        pydantic validates in strict mode, so lax coercions such as the JSON
        string ``"10"`` into an ``int`` are rejected rather than coerced.
        """
        # Combining timeout with run_in_thread=False on a sync function is
        # rejected at registration (see FunctionTool.from_function), so this only
        # needs to handle async and threadpool-sync under a timeout.
        if exec_is_async:
            # Argument validation is synchronous; the body runs on await below.
            result = type_adapter.validate_python(arguments, strict=strict)
        elif self.run_in_thread:
            # Sync function: run in threadpool to avoid blocking the event loop.
            result = await call_sync_fn_in_threadpool(
                type_adapter.validate_python, arguments, strict=strict
            )
        else:
            result = type_adapter.validate_python(arguments, strict=strict)

        try:
            if inspect.isawaitable(result):
                result = await result
            # Materialize generators (here, so slow generators are still bound by
            # any configured timeout scope).
            return await self._materialize_generator(result)
        except PydanticValidationError as e:
            # A pydantic error from awaiting the result or materializing a
            # generator is body execution, not argument validation.
            raise _ToolBodyError from e

    @staticmethod
    async def _materialize_generator(result: Any) -> Any:
        """Consume generators/async generators into lists.

        Without this, async generators pass through as objects (repr string),
        and sync generators get consumed during text serialization but are
        exhausted by the time structured content is built.
        """
        if inspect.isasyncgen(result):
            return [item async for item in result]
        if inspect.isgenerator(result):
            return list(result)
        return result

    def register_with_docket(self, docket: Docket) -> None:
        """Register this tool with docket for background execution.

        Registers the raw function so Docket sees and resolves ALL
        dependencies — both FastMCP's (CurrentContext, Progress) and
        Docket-native ones (Retry, Timeout, ConcurrencyLimit).
        """
        if not self.task_config.supports_tasks():
            return
        docket.register(self.fn, names=[self.key])

    async def add_to_docket(
        self,
        docket: Docket,
        arguments: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this tool for background execution via docket.

        FunctionTool splats the arguments dict since .fn expects **kwargs.

        Args:
            docket: The Docket instance
            arguments: Tool arguments
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(**arguments)

    def coerce_task_arguments(
        self, arguments: dict[str, Any], *, strict: bool = False
    ) -> dict[str, Any]:
        """Validate client arguments against their declared parameter types.

        The synchronous ``run()`` path validates arguments through the
        function's Pydantic TypeAdapter, so a parameter typed as a model
        arrives as a model instance. The task path hands the raw arguments to
        Docket, which binds them to the function signature without coercion —
        so without this a model-typed parameter would reach the function as a
        raw dict (#4349). ``submit_to_docket`` calls this up front so coerced
        values are what get queued, and validation errors surface before any
        task state is created. Coerced values survive the trip to the worker
        because Docket serializes task arguments with cloudpickle.

        ``strict`` mirrors the synchronous path's ``strict_input_validation``
        handling: when set, arguments are validated in strict mode so lax
        coercions (e.g. the string ``"1"`` into an ``int``) are rejected at
        submission rather than silently coerced and queued.

        Injected dependency parameters (Context, Depends()) are excluded via
        the same wrapper used by the synchronous path, so only client-supplied
        arguments are coerced and Docket's dependency resolution is untouched.
        """
        from fastmcp.server.dependencies import without_injected_parameters

        wrapper_fn = without_injected_parameters(
            self.fn, run_in_thread=self.run_in_thread
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
                # Argument coercion failure on the task path is a bad call, just
                # like the synchronous path — surface it as fastmcp's
                # ValidationError so it is classified consistently (see #4128).
                raise ValidationError(str(e), log_level=logging.WARNING) from e
        return coerced


@overload
def tool(fn: F) -> F: ...
@overload
def tool(
    name_or_fn: str,
    *,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | NotSetT | None = NotSet,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    timeout: float | None = None,
    auth: AuthCheck | list[AuthCheck] | None = None,
    run_in_thread: bool = True,
) -> Callable[[F], F]: ...
@overload
def tool(
    name_or_fn: None = None,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | NotSetT | None = NotSet,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    timeout: float | None = None,
    auth: AuthCheck | list[AuthCheck] | None = None,
    run_in_thread: bool = True,
) -> Callable[[F], F]: ...


def tool(
    name_or_fn: str | Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | NotSetT | None = NotSet,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    timeout: float | None = None,
    auth: AuthCheck | list[AuthCheck] | None = None,
    run_in_thread: bool = True,
) -> Any:
    """Standalone decorator to mark a function as an MCP tool.

    Returns the original function with metadata attached. Register with a server
    using mcp.add_tool().

    Args:
        run_in_thread: Applies to sync tool functions only. When True (default),
            the sync function is dispatched to a worker thread so it does not
            block the event loop. Set to False to run the function inline on the
            event loop thread — useful for libraries with thread affinity
            (e.g. Windows COM via `uiautomation`/`comtypes`/`pywin32`, `tkinter`,
            some GPU/driver bindings). Ignored for async functions. Cannot be
            combined with `timeout` on a sync function: inline calls have no
            cancellation checkpoints, so the timeout would be a silent no-op.
    """
    if isinstance(annotations, dict):
        annotations = ToolAnnotations(**annotations)

    if isinstance(name_or_fn, classmethod):
        raise TypeError(
            "To decorate a classmethod, use @classmethod above @tool. "
            "See https://gofastmcp.com/servers/tools#using-with-methods"
        )

    def attach_metadata(fn: F, tool_name: str | None) -> F:
        metadata = ToolMeta(
            name=tool_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            meta=meta,
            task=task,
            timeout=timeout,
            auth=auth,
            run_in_thread=run_in_thread,
        )
        target = fn.__func__ if isinstance(fn, staticmethod | MethodType) else fn
        cast(Any, target).__fastmcp__ = metadata
        return fn

    def decorator(fn: F, tool_name: str | None) -> F:
        return attach_metadata(fn, tool_name)

    if inspect.isroutine(name_or_fn):
        return decorator(name_or_fn, name)
    elif isinstance(name_or_fn, str):
        if name is not None:
            raise TypeError("Cannot specify name both as first argument and keyword")
        tool_name = name_or_fn
    elif name_or_fn is None:
        tool_name = name
    else:
        raise TypeError(f"Invalid first argument: {type(name_or_fn)}")

    def wrapper(fn: F) -> F:
        return decorator(fn, tool_name)

    return wrapper
