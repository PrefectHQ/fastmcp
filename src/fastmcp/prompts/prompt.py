"""Base classes for FastMCP prompts."""

from __future__ import annotations as _annotations

import inspect
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Literal, overload

import pydantic
import pydantic_core

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution
import mcp.types
from mcp import GetPromptResult
from mcp.types import (
    EmbeddedResource,
    Icon,
    PromptMessage,
    TextContent,
)
from mcp.types import Prompt as SDKPrompt
from mcp.types import PromptArgument as SDKPromptArgument
from pydantic import Field

from fastmcp.exceptions import PromptError
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    FastMCPBaseModel,
    get_cached_typeadapter,
)

logger = get_logger(__name__)


class Message(pydantic.BaseModel):
    """Wrapper for prompt message with auto-serialization.

    Accepts any content - strings pass through, other types
    (dict, list, BaseModel) are JSON-serialized to text.

    Example:
        ```python
        from fastmcp.prompts import Message

        # String content (user role by default)
        Message("Hello, world!")

        # Explicit role
        Message("I can help with that.", role="assistant")

        # Auto-serialized to JSON
        Message({"key": "value"})
        Message(["item1", "item2"])
        ```
    """

    role: Literal["user", "assistant"]
    content: TextContent | EmbeddedResource

    def __init__(
        self,
        content: Any,
        role: Literal["user", "assistant"] = "user",
    ):
        """Create Message with automatic serialization.

        Args:
            content: The message content. str passes through directly.
                     TextContent and EmbeddedResource pass through.
                     Other types (dict, list, BaseModel) are JSON-serialized.
            role: The message role, either "user" or "assistant".
        """
        # Handle already-wrapped content types
        if isinstance(content, (TextContent, EmbeddedResource)):
            normalized_content: TextContent | EmbeddedResource = content
        elif isinstance(content, str):
            normalized_content = TextContent(type="text", text=content)
        else:
            # dict, list, BaseModel → JSON string
            serialized = pydantic_core.to_json(content, fallback=str).decode()
            normalized_content = TextContent(type="text", text=serialized)

        super().__init__(role=role, content=normalized_content)

    def to_mcp_prompt_message(self) -> PromptMessage:
        """Convert to MCP PromptMessage."""
        return PromptMessage(role=self.role, content=self.content)


class PromptArgument(FastMCPBaseModel):
    """An argument that can be passed to a prompt."""

    name: str = Field(description="Name of the argument")
    description: str | None = Field(
        default=None, description="Description of what the argument does"
    )
    required: bool = Field(
        default=False, description="Whether the argument is required"
    )


class PromptResult(pydantic.BaseModel):
    """Canonical result type for prompt rendering.

    Provides explicit control over prompt responses: multiple messages,
    roles, and metadata at both the message and result level.

    Accepts:
        - str: Wrapped as single Message (user role)
        - list[Message]: Used directly for multiple messages or custom roles

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.prompts import PromptResult, Message

        mcp = FastMCP()

        # Simple string content
        @mcp.prompt
        def greet() -> PromptResult:
            return PromptResult("Hello!")

        # Multiple messages with roles
        @mcp.prompt
        def conversation() -> PromptResult:
            return PromptResult([
                Message("What's the weather?"),
                Message("It's sunny today.", role="assistant"),
            ])
        ```
    """

    messages: list[Message]
    description: str | None = None
    meta: dict[str, Any] | None = None

    def __init__(
        self,
        messages: str | list[Message],
        description: str | None = None,
        meta: dict[str, Any] | None = None,
    ):
        """Create PromptResult.

        Args:
            messages: String or list of Message objects.
            description: Optional description of the prompt result.
            meta: Optional metadata about the prompt result.
        """
        normalized = self._normalize_messages(messages)
        super().__init__(messages=normalized, description=description, meta=meta)

    @staticmethod
    def _normalize_messages(
        messages: str | list[Message],
    ) -> list[Message]:
        """Normalize input to list[Message]."""
        if isinstance(messages, str):
            return [Message(messages)]
        if isinstance(messages, list):
            # Validate all items are Message
            for i, item in enumerate(messages):
                if not isinstance(item, Message):
                    raise TypeError(
                        f"messages[{i}] must be Message, got {type(item).__name__}. "
                        f"Use Message({item!r}) to wrap the value."
                    )
            return messages
        raise TypeError(
            f"messages must be str or list[Message], got {type(messages).__name__}"
        )

    def to_mcp_prompt_result(self) -> GetPromptResult:
        """Convert to MCP GetPromptResult."""
        mcp_messages = [m.to_mcp_prompt_message() for m in self.messages]
        return GetPromptResult(
            description=self.description,
            messages=mcp_messages,
            _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
        )


class Prompt(FastMCPComponent):
    """A prompt template that can be rendered with parameters."""

    KEY_PREFIX: ClassVar[str] = "prompt"

    arguments: list[PromptArgument] | None = Field(
        default=None, description="Arguments that can be passed to the prompt"
    )

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
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - str: wrapped as single user Message
        - list[Message | str]: converted to list[Message]
        - PromptResult: used directly
        """
        return FunctionPrompt.from_function(
            fn=fn,
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            task=task,
        )

    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> str | list[Message | str] | PromptResult:
        """Render the prompt with arguments.

        Subclasses must implement this method. Return one of:
        - str: Wrapped as single user Message
        - list[Message | str]: Converted to list[Message]
        - PromptResult: Used directly
        """
        raise NotImplementedError("Subclasses must implement render()")

    def convert_result(self, raw_value: Any) -> PromptResult:
        """Convert a raw return value to PromptResult.

        Accepts:
            - PromptResult: passed through
            - str: wrapped as single Message
            - list[Message | str]: converted to list[Message]

        Raises:
            TypeError: for unsupported types
        """
        if isinstance(raw_value, PromptResult):
            return raw_value

        if isinstance(raw_value, str):
            return PromptResult(raw_value, description=self.description, meta=self.meta)

        if isinstance(raw_value, list | tuple):
            messages: list[Message] = []
            for i, item in enumerate(raw_value):
                if isinstance(item, Message):
                    messages.append(item)
                elif isinstance(item, str):
                    messages.append(Message(item))
                else:
                    raise TypeError(
                        f"messages[{i}] must be Message or str, got {type(item).__name__}. "
                        f"Use Message({item!r}) to wrap the value."
                    )
            return PromptResult(messages, description=self.description, meta=self.meta)

        raise TypeError(
            f"Prompt must return str, list[Message], or PromptResult, "
            f"got {type(raw_value).__name__}"
        )

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: TaskMeta | None = None,
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Prompt subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of render() directly.

        Args:
            arguments: Prompt arguments
            task_meta: If provided, execute as background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return PromptResult.

        Returns:
            PromptResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderPrompt overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self,
            task_type="prompt",
            arguments=arguments,
            task_meta=task_meta,
        )
        if task_result:
            return task_result

        # Synchronous execution
        result = await self.render(arguments)
        return self.convert_result(result)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this prompt with docket for background execution."""
        if not self.task_config.supports_tasks():
            return
        docket.register(self.render, names=[self.key])

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        arguments: dict[str, Any] | None,
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this prompt for background execution via docket.

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
        return await docket.add(lookup_key, **kwargs)(arguments)


class FunctionPrompt(Prompt):
    """A prompt that is a function."""

    fn: Callable[..., Any]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - str: wrapped as single user Message
        - list[Message | str]: converted to list[Message]
        - PromptResult: used directly
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
            result = self.fn(**kwargs)
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
        docket.register(self.fn, names=[self.key])  # type: ignore[arg-type]

    async def add_to_docket(  # type: ignore[override]
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
