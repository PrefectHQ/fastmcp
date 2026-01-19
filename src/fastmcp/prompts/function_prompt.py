"""Standalone @prompt decorator for FastMCP."""

from __future__ import annotations

import inspect
import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeVar,
    overload,
    runtime_checkable,
)

import pydantic_core
from mcp.types import Icon

import fastmcp
from fastmcp.decorators import resolve_task_config
from fastmcp.exceptions import PromptError
from fastmcp.prompts.prompt import Prompt, PromptArgument, PromptResult
from fastmcp.server.dependencies import (
    transform_context_annotations,
    without_injected_parameters,
)
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import AuthCheckCallable
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

F = TypeVar("F", bound=Callable[..., Any])

logger = get_logger(__name__)


@runtime_checkable
class DecoratedPrompt(Protocol):
    """Protocol for functions decorated with @prompt."""

    __fastmcp__: PromptMeta

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class PromptMeta:
    """Metadata attached to functions by the @prompt decorator."""

    type: Literal["prompt"] = field(default="prompt", init=False)
    name: str | None = None
    version: str | int | None = None
    title: str | None = None
    description: str | None = None
    icons: list[Icon] | None = None
    tags: set[str] | None = None
    meta: dict[str, Any] | None = None
    task: bool | TaskConfig | None = None
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None
    enabled: bool = True


class FunctionPrompt(Prompt):
    """A prompt that is a function."""

    fn: Callable[..., Any]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        metadata: PromptMeta | None = None,
        # Keep individual params for backwards compat
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        Args:
            fn: The function to wrap
            metadata: PromptMeta object with all configuration. If provided,
                individual parameters must not be passed.
            name, title, etc.: Individual parameters for backwards compatibility.
                Cannot be used together with metadata parameter.

        The function can return:
        - str: wrapped as single user Message
        - list[Message | str]: converted to list[Message]
        - PromptResult: used directly
        """
        # Check mutual exclusion
        individual_params_provided = any(
            x is not None
            for x in [name, version, title, description, icons, tags, meta, task, auth]
        )

        if metadata is not None and individual_params_provided:
            raise TypeError(
                "Cannot pass both 'metadata' and individual parameters to from_function(). "
                "Use metadata alone or individual parameters alone."
            )

        # Build metadata from kwargs if not provided
        if metadata is None:
            metadata = PromptMeta(
                name=name,
                version=version,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                meta=meta,
                task=task,
                auth=auth,
            )

        func_name = (
            metadata.name or getattr(fn, "__name__", None) or fn.__class__.__name__
        )

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Reject functions with *args or **kwargs
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError("Functions with *args are not supported as prompts")
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                raise ValueError("Functions with **kwargs are not supported as prompts")

        description = metadata.description or inspect.getdoc(fn)

        # Normalize task to TaskConfig and validate
        task_value = metadata.task
        if task_value is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task_value, bool):
            task_config = TaskConfig.from_bool(task_value)
        else:
            task_config = task_value
        task_config.validate_function(fn, func_name)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        # Wrap fn to handle dependency resolution internally
        wrapped_fn = without_injected_parameters(fn)
        type_adapter = get_cached_typeadapter(wrapped_fn)
        parameters = type_adapter.json_schema()
        parameters = compress_schema(parameters, prune_titles=True)

        # Convert parameters to PromptArguments
        arguments: list[PromptArgument] = []
        if "properties" in parameters:
            for param_name, param in parameters["properties"].items():
                arg_description = param.get("description")

                # For non-string parameters, append JSON schema info to help users
                # understand the expected format when passing as strings (MCP requirement)
                if param_name in sig.parameters:
                    sig_param = sig.parameters[param_name]
                    if (
                        sig_param.annotation != inspect.Parameter.empty
                        and sig_param.annotation is not str
                    ):
                        # Get the JSON schema for this specific parameter type
                        try:
                            param_adapter = get_cached_typeadapter(sig_param.annotation)
                            param_schema = param_adapter.json_schema()

                            # Create compact schema representation
                            schema_str = json.dumps(param_schema, separators=(",", ":"))

                            # Append schema info to description
                            schema_note = f"Provide as a JSON string matching the following schema: {schema_str}"
                            if arg_description:
                                arg_description = f"{arg_description}\n\n{schema_note}"
                            else:
                                arg_description = schema_note
                        except Exception as e:
                            # If schema generation fails, skip enhancement
                            logger.debug(
                                "Failed to generate schema for prompt argument %s: %s",
                                param_name,
                                e,
                            )

                arguments.append(
                    PromptArgument(
                        name=param_name,
                        description=arg_description,
                        required=param_name in parameters.get("required", []),
                    )
                )

        return cls(
            name=func_name,
            version=str(metadata.version) if metadata.version is not None else None,
            title=metadata.title,
            description=description,
            icons=metadata.icons,
            arguments=arguments,
            tags=metadata.tags or set(),
            fn=wrapped_fn,
            meta=metadata.meta,
            task_config=task_config,
            auth=metadata.auth,
        )

    def _convert_string_arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Convert string arguments to expected types based on function signature."""
        from fastmcp.server.dependencies import without_injected_parameters

        wrapper_fn = without_injected_parameters(self.fn)
        sig = inspect.signature(wrapper_fn)
        converted_kwargs = {}

        for param_name, param_value in kwargs.items():
            if param_name in sig.parameters:
                param = sig.parameters[param_name]

                # If parameter has no annotation or annotation is str, pass as-is
                if (
                    param.annotation == inspect.Parameter.empty
                    or param.annotation is str
                ) or not isinstance(param_value, str):
                    converted_kwargs[param_name] = param_value
                else:
                    # Try to convert string argument using type adapter
                    try:
                        adapter = get_cached_typeadapter(param.annotation)
                        # Try JSON parsing first for complex types
                        try:
                            converted_kwargs[param_name] = adapter.validate_json(
                                param_value
                            )
                        except (ValueError, TypeError, pydantic_core.ValidationError):
                            # Fallback to direct validation
                            converted_kwargs[param_name] = adapter.validate_python(
                                param_value
                            )
                    except (ValueError, TypeError, pydantic_core.ValidationError) as e:
                        # If conversion fails, provide informative error
                        raise PromptError(
                            f"Could not convert argument '{param_name}' with value '{param_value}' "
                            f"to expected type {param.annotation}. Error: {e}"
                        ) from e
            else:
                # Parameter not in function signature, pass as-is
                converted_kwargs[param_name] = param_value

        return converted_kwargs

    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> PromptResult:
        """Render the prompt with arguments."""
        # Validate required arguments
        if self.arguments:
            required = {arg.name for arg in self.arguments if arg.required}
            provided = set(arguments or {})
            missing = required - provided
            if missing:
                raise ValueError(f"Missing required arguments: {missing}")

        try:
            # Prepare arguments
            kwargs = arguments.copy() if arguments else {}

            # Convert string arguments to expected types BEFORE validation
            kwargs = self._convert_string_arguments(kwargs)

            # self.fn is wrapped by without_injected_parameters which handles
            # dependency resolution internally
            if inspect.iscoroutinefunction(self.fn):
                result = await self.fn(**kwargs)
            else:
                # Run sync functions in threadpool to avoid blocking the event loop
                result = await call_sync_fn_in_threadpool(self.fn, **kwargs)
                # Handle sync wrappers that return awaitables (e.g., partial(async_fn))
                if inspect.isawaitable(result):
                    result = await result

            return self.convert_result(result)
        except Exception as e:
            logger.exception(f"Error rendering prompt {self.name}")
            raise PromptError(f"Error rendering prompt {self.name}.") from e

    def register_with_docket(self, docket: Docket) -> None:
        """Register this prompt with docket for background execution.

        FunctionPrompt registers the underlying function, which has the user's
        Depends parameters for docket to resolve.
        """
        if not self.task_config.supports_tasks():
            return
        docket.register(self.fn, names=[self.key])

    async def add_to_docket(
        self,
        docket: Docket,
        arguments: dict[str, Any] | None,
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this prompt for background execution via docket.

        FunctionPrompt splats the arguments dict since .fn expects **kwargs.

        Args:
            docket: The Docket instance
            arguments: Prompt arguments
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(**(arguments or {}))


@overload
def prompt(fn: F) -> F: ...
@overload
def prompt(
    name_or_fn: str,
    *,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...
@overload
def prompt(
    name_or_fn: None = None,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Callable[[F], F]: ...


def prompt(
    name_or_fn: str | Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
    task: bool | TaskConfig | None = None,
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
) -> Any:
    """Standalone decorator to mark a function as an MCP prompt.

    Returns the original function with metadata attached. Register with a server
    using mcp.add_prompt().
    """
    if isinstance(name_or_fn, classmethod):
        raise TypeError(
            "To decorate a classmethod, use @classmethod above @prompt. "
            "See https://gofastmcp.com/servers/prompts#using-with-methods"
        )

    def create_prompt(
        fn: Callable[..., Any], prompt_name: str | None
    ) -> FunctionPrompt:
        # Create metadata first, then pass it
        prompt_meta = PromptMeta(
            name=prompt_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            task=resolve_task_config(task),
            auth=auth,
        )
        return FunctionPrompt.from_function(fn, metadata=prompt_meta)

    def attach_metadata(fn: F, prompt_name: str | None) -> F:
        metadata = PromptMeta(
            name=prompt_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            task=task,
            auth=auth,
        )
        target = fn.__func__ if hasattr(fn, "__func__") else fn
        target.__fastmcp__ = metadata
        return fn

    def decorator(fn: F, prompt_name: str | None) -> F:
        if fastmcp.settings.decorator_mode == "object":
            warnings.warn(
                "decorator_mode='object' is deprecated and will be removed in a future version. "
                "Decorators now return the original function with metadata attached.",
                DeprecationWarning,
                stacklevel=4,
            )
            return create_prompt(fn, prompt_name)  # type: ignore[return-value]
        return attach_metadata(fn, prompt_name)

    if inspect.isroutine(name_or_fn):
        return decorator(name_or_fn, name)
    elif isinstance(name_or_fn, str):
        if name is not None:
            raise TypeError("Cannot specify name both as first argument and keyword")
        prompt_name = name_or_fn
    elif name_or_fn is None:
        prompt_name = name
    else:
        raise TypeError(f"Invalid first argument: {type(name_or_fn)}")

    def wrapper(fn: F) -> F:
        return decorator(fn, prompt_name)

    return wrapper
