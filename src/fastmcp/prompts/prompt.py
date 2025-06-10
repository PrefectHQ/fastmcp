"""Base classes for FastMCP prompts."""

from __future__ import annotations as _annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import pydantic_core
from mcp.types import EmbeddedResource, ImageContent, PromptMessage, Role, TextContent
from mcp.types import Prompt as MCPPrompt
from mcp.types import PromptArgument as MCPPromptArgument
from pydantic import Field, TypeAdapter, validate_call

from fastmcp.exceptions import PromptError
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    FastMCPBaseModel,
    find_kwarg_by_type,
    get_cached_typeadapter,
)

if TYPE_CHECKING:
    pass

CONTENT_TYPES = TextContent | ImageContent | EmbeddedResource

logger = get_logger(__name__)


def Message(
    content: str | CONTENT_TYPES, role: Role | None = None, **kwargs: Any
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


class Prompt(FastMCPComponent, ABC):
    """A prompt template that can be rendered with parameters."""

    arguments: list[PromptArgument] | None = Field(
        default=None, description="Arguments that can be passed to the prompt"
    )

    def to_mcp_prompt(self, **overrides: Any) -> MCPPrompt:
        """Convert the prompt to an MCP prompt."""
        arguments = [
            MCPPromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required,
            )
            for arg in self.arguments or []
        ]
        kwargs = {
            "name": self.name,
            "description": self.description,
            "arguments": arguments,
        }
        return MCPPrompt(**kwargs | overrides)

    @staticmethod
    def from_function(
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - A string (converted to a message)
        - A Message object
        - A dict (converted to a message)
        - A sequence of any of the above
        """
        return FunctionPrompt.from_function(
            fn=fn, name=name, description=description, tags=tags, enabled=enabled
        )

    @abstractmethod
    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> list[PromptMessage]:
        """Render the prompt with arguments."""
        raise NotImplementedError("Prompt.render() must be implemented by subclasses")


class FunctionPrompt(Prompt):
    """A prompt that is a function."""

    fn: Callable[..., PromptResult | Awaitable[PromptResult]]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - A string (converted to a message)
        - A Message object
        - A dict (converted to a message)
        - A sequence of any of the above
        """
        from fastmcp.server.context import Context

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

        description = description or fn.__doc__

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        type_adapter = get_cached_typeadapter(fn)
        parameters = type_adapter.json_schema()

        # Auto-detect context parameter if not provided

        context_kwarg = find_kwarg_by_type(fn, kwarg_type=Context)
        if context_kwarg:
            prune_params = [context_kwarg]
        else:
            prune_params = None

        parameters = compress_schema(parameters, prune_params=prune_params)

        # Convert parameters to PromptArguments
        arguments: list[PromptArgument] = []
        if "properties" in parameters:
            for param_name, param in parameters["properties"].items():
                arguments.append(
                    PromptArgument(
                        name=param_name,
                        description=param.get("description"),
                        required=param_name in parameters.get("required", []),
                    )
                )

        # ensure the arguments are properly cast
        fn = validate_call(fn)

        return cls(
            name=func_name,
            description=description,
            arguments=arguments,
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
            fn=fn,
        )

    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> list[PromptMessage]:
        """Render the prompt with arguments."""
        from fastmcp.server.context import Context

        # Validate required arguments
        if self.arguments:
            required = {arg.name for arg in self.arguments if arg.required}
            provided = set(arguments or {})
            missing = required - provided
            if missing:
                raise ValueError(f"Missing required arguments: {missing}")

        try:
            # Prepare arguments with context
            kwargs = arguments.copy() if arguments else {}
            context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
            if context_kwarg and context_kwarg not in kwargs:
                kwargs[context_kwarg] = get_context()

            # Call function and check if result is a coroutine
            result = self.fn(**kwargs)
            if inspect.iscoroutine(result):
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
                        content = pydantic_core.to_json(
                            msg, fallback=str, indent=2
                        ).decode()
                        messages.append(
                            PromptMessage(
                                role="user",
                                content=TextContent(type="text", text=content),
                            )
                        )
                except Exception:
                    raise PromptError("Could not convert prompt result to message.")

            return messages
        except Exception as e:
            logger.exception(f"Error rendering prompt {self.name}: {e}")
            raise PromptError(f"Error rendering prompt {self.name}.")
