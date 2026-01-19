"""Prompt decorator mixin for LocalProvider.

This module provides the PromptDecoratorMixin class that adds prompt
registration functionality to LocalProvider.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, overload

import mcp.types
from mcp.types import AnyFunction

import fastmcp
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.prompts.prompt import Prompt
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import AuthCheckCallable

if TYPE_CHECKING:
    from fastmcp.server.providers.local_provider import LocalProvider


class PromptDecoratorMixin:
    """Mixin class providing prompt decorator functionality for LocalProvider.

    This mixin contains all methods related to:
    - Prompt registration via add_prompt()
    - Prompt decorator (@provider.prompt)
    """

    def add_prompt(self: LocalProvider, prompt: Prompt | Callable[..., Any]) -> Prompt:
        """Add a prompt to this provider's storage.

        Accepts either a Prompt object or a decorated function with __fastmcp__ metadata.
        """
        enabled = True
        if not isinstance(prompt, Prompt):
            from fastmcp.decorators import get_fastmcp_meta
            from fastmcp.prompts.function_prompt import PromptMeta

            meta = get_fastmcp_meta(prompt)
            if meta is not None and isinstance(meta, PromptMeta):
                resolved_task = meta.task if meta.task is not None else False
                enabled = meta.enabled
                prompt = Prompt.from_function(
                    prompt,
                    name=meta.name,
                    version=meta.version,
                    title=meta.title,
                    description=meta.description,
                    icons=meta.icons,
                    tags=meta.tags,
                    meta=meta.meta,
                    task=resolved_task,
                    auth=meta.auth,
                )
            else:
                raise TypeError(
                    f"Expected Prompt or @prompt-decorated function, got {type(prompt).__name__}. "
                    "Use @prompt decorator or pass a Prompt instance."
                )
        self._add_component(prompt)
        if not enabled:
            self.disable(keys={prompt.key})
        return prompt

    @overload
    def prompt(
        self: LocalProvider,
        name_or_fn: AnyFunction,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionPrompt: ...

    @overload
    def prompt(
        self: LocalProvider,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> Callable[[AnyFunction], FunctionPrompt]: ...

    def prompt(
        self: LocalProvider,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[mcp.types.Icon] | None = None,
        tags: set[str] | None = None,
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> (
        Callable[[AnyFunction], FunctionPrompt]
        | FunctionPrompt
        | partial[Callable[[AnyFunction], FunctionPrompt] | FunctionPrompt]
    ):
        """Decorator to register a prompt.

        This decorator supports multiple calling patterns:
        - @provider.prompt (without parentheses)
        - @provider.prompt() (with empty parentheses)
        - @provider.prompt("custom_name") (with name as first argument)
        - @provider.prompt(name="custom_name") (with name as keyword argument)
        - provider.prompt(function, name="custom_name") (direct function call)

        Args:
            name_or_fn: Either a function (when used as @prompt), a string name, or None
            name: Optional name for the prompt (keyword-only, alternative to name_or_fn)
            title: Optional title for the prompt
            description: Optional description of what the prompt does
            icons: Optional icons for the prompt
            tags: Optional set of tags for categorizing the prompt
            enabled: Whether the prompt is enabled (default True). If False, adds to blocklist.
            meta: Optional meta information about the prompt
            task: Optional task configuration for background execution
            auth: Optional authorization checks for the prompt

        Returns:
            The registered FunctionPrompt or a decorator function.

        Example:
            ```python
            provider = LocalProvider()

            @provider.prompt
            def analyze(topic: str) -> list:
                return [{"role": "user", "content": f"Analyze: {topic}"}]

            @provider.prompt("custom_name")
            def my_prompt(data: str) -> list:
                return [{"role": "user", "content": data}]
            ```
        """
        if isinstance(name_or_fn, classmethod):
            raise TypeError(
                "To decorate a classmethod, use @classmethod above @prompt. "
                "See https://gofastmcp.com/servers/prompts#using-with-methods"
            )

        def decorate_and_register(
            fn: AnyFunction, prompt_name: str | None
        ) -> FunctionPrompt | AnyFunction:
            # Check for unbound method
            try:
                params = list(inspect.signature(fn).parameters.keys())
            except (ValueError, TypeError):
                params = []
            if params and params[0] in ("self", "cls"):
                fn_name = getattr(fn, "__name__", "function")
                raise TypeError(
                    f"The function '{fn_name}' has '{params[0]}' as its first parameter. "
                    f"Use the standalone @prompt decorator and register the bound method:\n\n"
                    f"    from fastmcp.prompts import prompt\n\n"
                    f"    class MyClass:\n"
                    f"        @prompt\n"
                    f"        def {fn_name}(...):\n"
                    f"            ...\n\n"
                    f"    obj = MyClass()\n"
                    f"    mcp.add_prompt(obj.{fn_name})\n\n"
                    f"See https://gofastmcp.com/servers/prompts#using-with-methods"
                )

            resolved_task: bool | TaskConfig = task if task is not None else False

            if fastmcp.settings.decorator_mode == "object":
                prompt_obj = Prompt.from_function(
                    fn,
                    name=prompt_name,
                    version=version,
                    title=title,
                    description=description,
                    icons=icons,
                    tags=tags,
                    meta=meta,
                    task=resolved_task,
                    auth=auth,
                )
                self._add_component(prompt_obj)
                if not enabled:
                    self.disable(keys={prompt_obj.key})
                return prompt_obj
            else:
                from fastmcp.prompts.function_prompt import PromptMeta

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
                    enabled=enabled,
                )
                target = fn.__func__ if hasattr(fn, "__func__") else fn
                target.__fastmcp__ = metadata  # type: ignore[attr-defined]
                self.add_prompt(fn)
                return fn

        if inspect.isroutine(name_or_fn):
            return decorate_and_register(name_or_fn, name)

        elif isinstance(name_or_fn, str):
            if name is not None:
                raise TypeError(
                    f"Cannot specify both a name as first argument and as keyword argument. "
                    f"Use either @prompt('{name_or_fn}') or @prompt(name='{name}'), not both."
                )
            prompt_name = name_or_fn
        elif name_or_fn is None:
            prompt_name = name
        else:
            raise TypeError(f"Invalid first argument: {type(name_or_fn)}")

        return partial(
            self.prompt,
            name=prompt_name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            enabled=enabled,
            task=task,
            auth=auth,
        )
