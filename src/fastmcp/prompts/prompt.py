"""Base classes for FastMCP prompts."""

from __future__ import annotations as _annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any

import pydantic_core
from mcp.types import ContentBlock, Icon, PromptMessage, Role, TextContent
from mcp.types import Prompt as SDKPrompt
from mcp.types import PromptArgument as SDKPromptArgument
from pydantic import Field, TypeAdapter

from fastmcp.exceptions import PromptError
from fastmcp.server.dependencies import get_context, without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    FastMCPBaseModel,
    get_cached_typeadapter,
)

logger = get_logger(__name__)


def Message(
    content: str | ContentBlock, role: Role | None = None, **kwargs: Any
) -> PromptMessage:
    """A user-friendly constructor for PromptMessage."""
    if isinstance(content, str):
        content = TextContent(type="text", text=content)
    if role is None:
        role = "user"
    return PromptMessage(content=content, role=role, **kwargs)


message_validator = TypeAdapter[PromptMessage](PromptMessage)

SyncPromptResult = (
    str
    | PromptMessage
    | dict[str, Any]
    | Sequence[str | PromptMessage | dict[str, Any]]
)
PromptResult = SyncPromptResult | Awaitable[SyncPromptResult]


class PromptArgument(FastMCPBaseModel):
    """An argument that can be passed to a prompt."""

    name: str = Field(description="Name of the argument")
    description: str | None = Field(
        default=None, description="Description of what the argument does"
    )
    required: bool = Field(
        default=False, description="Whether the argument is required"
    )


class Prompt(FastMCPComponent):
    """A prompt template that can be rendered with parameters."""

    arguments: list[PromptArgument] | None = Field(
        default=None, description="Arguments that can be passed to the prompt"
    )

    def enable(self) -> None:
        super().enable()
        try:
            context = get_context()
            context._queue_prompt_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def disable(self) -> None:
        super().disable()
        try:
            context = get_context()
            context._queue_prompt_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def to_mcp_prompt(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> SDKPrompt:
        """Convert the prompt to an MCP prompt."""
        arguments = [
            SDKPromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required,
            )
            for arg in self.arguments or []
        ]

        return SDKPrompt(
            name=overrides.get("name", self.name),
            description=overrides.get("description", self.description),
            arguments=arguments,
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
            _meta=overrides.get(
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    @staticmethod
    def from_function(
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - A string (converted to a message)
        - A Message object
        - A dict (converted to a message)
        - A sequence of any of the above
        """
        return FunctionPrompt.from_function(
            fn=fn,
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            enabled=enabled,
            meta=meta,
            task=task,
        )

    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> list[PromptMessage]:
        """Render the prompt with arguments.

        This method is not implemented in the base Prompt class and must be
        implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement render()")


class FunctionPrompt(Prompt):
    """A prompt that is a function."""

    fn: Callable[..., PromptResult | Awaitable[PromptResult]]
    task_config: Annotated[
        TaskConfig,
        Field(description="Background task execution configuration (SEP-1686)."),
    ] = Field(default_factory=lambda: TaskConfig(mode="forbidden"))

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - A string (converted to a message)
        - A Message object
        - A dict (converted to a message)
        - A sequence of any of the above
        """

        func_name = name or getattr(fn, "__name__", None) or fn.__class__.__name__

        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")
            # Reject functions with *args or **kwargs
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError("Functions with *args are not supported as prompts")
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                raise ValueError("Functions with **kwargs are not supported as prompts")

        description = description or inspect.getdoc(fn)

        # Normalize task to TaskConfig and validate
        if task is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task, bool):
            task_config = TaskConfig.from_bool(task)
        else:
            task_config = task
        task_config.validate_function(fn, func_name)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__  # type: ignore[assignment]

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
                        except Exception:
                            # If schema generation fails, skip enhancement
                            pass

                arguments.append(
                    PromptArgument(
                        name=param_name,
                        description=arg_description,
                        required=param_name in parameters.get("required", []),
                    )
                )

        return cls(
            name=func_name,
            title=title,
            description=description,
            icons=icons,
            arguments=arguments,
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
            fn=wrapped_fn,
            meta=meta,
            task_config=task_config,
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
    ) -> list[PromptMessage]:
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
            result = self.fn(**kwargs)
            if inspect.isawaitable(result):
                result = await result

            # Validate messages
            if not isinstance(result, list | tuple):
                result = [result]

            # Convert result to messages
            messages: list[PromptMessage] = []
            for msg in result:
                try:
                    if isinstance(msg, PromptMessage):
                        messages.append(msg)
                    elif isinstance(msg, str):
                        messages.append(
                            PromptMessage(
                                role="user",
                                content=TextContent(type="text", text=msg),
                            )
                        )
                    else:
                        content = pydantic_core.to_json(msg, fallback=str).decode()
                        messages.append(
                            PromptMessage(
                                role="user",
                                content=TextContent(type="text", text=content),
                            )
                        )
                except Exception as e:
                    raise PromptError(
                        "Could not convert prompt result to message."
                    ) from e

            return messages
        except Exception as e:
            logger.exception(f"Error rendering prompt {self.name}")
            raise PromptError(f"Error rendering prompt {self.name}.") from e
