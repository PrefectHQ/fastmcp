"""Decorators for marking functions in filesystem-based discovery.

These decorators mark functions with metadata so that FileSystemProvider
can discover and register them. Unlike LocalProvider's decorators, these
do NOT register components immediately - they just store metadata on the
function for later discovery.

Example:
    ```python
    # mcp/tools/greet.py
    from fastmcp.fs import tool

    @tool
    def greet(name: str) -> str:
        '''Greet someone by name.'''
        return f"Hello, {name}!"

    @tool(name="custom-greet", tags={"greeting"})
    def my_greet(name: str) -> str:
        return f"Hi, {name}!"
    ```
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, overload

from mcp.types import Annotations, AnyFunction, ToolAnnotations

if TYPE_CHECKING:
    import mcp.types

# Attribute name used to store metadata on decorated functions
FS_META_ATTR = "_fastmcp_fs_meta"


@dataclass
class ToolMeta:
    """Metadata stored on functions decorated with @tool."""

    type: Literal["tool"] = "tool"
    name: str | None = None
    title: str | None = None
    description: str | None = None
    icons: list[mcp.types.Icon] | None = None
    tags: set[str] | None = None
    output_schema: dict[str, Any] | None = None
    annotations: ToolAnnotations | None = None
    meta: dict[str, Any] | None = None


@dataclass
class ResourceMeta:
    """Metadata stored on functions decorated with @resource."""

    type: Literal["resource"] = "resource"
    uri: str = ""
    name: str | None = None
    title: str | None = None
    description: str | None = None
    icons: list[mcp.types.Icon] | None = None
    mime_type: str | None = None
    tags: set[str] | None = None
    annotations: Annotations | None = None
    meta: dict[str, Any] | None = None


@dataclass
class PromptMeta:
    """Metadata stored on functions decorated with @prompt."""

    type: Literal["prompt"] = "prompt"
    name: str | None = None
    title: str | None = None
    description: str | None = None
    icons: list[mcp.types.Icon] | None = None
    tags: set[str] | None = None
    meta: dict[str, Any] | None = None


FSMeta = ToolMeta | ResourceMeta | PromptMeta


def get_fs_meta(fn: Any) -> FSMeta | None:
    """Get filesystem metadata from a function if it has been decorated."""
    return getattr(fn, FS_META_ATTR, None)


def has_fs_meta(fn: Any) -> bool:
    """Check if a function has filesystem metadata."""
    return hasattr(fn, FS_META_ATTR)


# =============================================================================
# @tool decorator
# =============================================================================


@overload
def tool(fn: AnyFunction) -> AnyFunction: ...


@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any: ...


@overload
def tool(
    fn: str,
    *,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any: ...


def tool(
    fn: AnyFunction | str | None = None,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    annotations: ToolAnnotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """Mark a function as a tool for filesystem-based discovery.

    This decorator stores metadata on the function but does NOT register it.
    FileSystemProvider discovers marked functions when scanning directories.

    Supports multiple calling patterns:
    - @tool (without parentheses)
    - @tool() (with empty parentheses)
    - @tool("custom_name") (with name as first argument)
    - @tool(name="custom_name") (with keyword arguments)

    Args:
        fn: The function to decorate, or a name string, or None
        name: Optional name for the tool (defaults to function name)
        title: Optional title for display
        description: Optional description (defaults to docstring)
        icons: Optional icons for the tool
        tags: Optional tags for categorization
        output_schema: Optional JSON schema for output
        annotations: Optional tool annotations
        meta: Optional metadata dict

    Example:
        ```python
        @tool
        def greet(name: str) -> str:
            '''Greet someone.'''
            return f"Hello, {name}!"

        @tool(name="custom-greet", tags={"greeting"})
        def my_greet(name: str) -> str:
            return f"Hi, {name}!"
        ```
    """
    if isinstance(annotations, dict):
        annotations = ToolAnnotations(**annotations)

    def decorator(func: AnyFunction) -> AnyFunction:
        tool_meta = ToolMeta(
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            meta=meta,
        )
        setattr(func, FS_META_ATTR, tool_meta)
        return func

    if inspect.isroutine(fn):
        # @tool without parentheses
        return decorator(fn)
    elif isinstance(fn, str):
        # @tool("custom_name")
        return tool(
            name=fn,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            output_schema=output_schema,
            annotations=annotations,
            meta=meta,
        )
    else:
        # @tool() or @tool(name="...") - return decorator
        return decorator


# =============================================================================
# @resource decorator
# =============================================================================


def resource(
    uri: str,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    mime_type: str | None = None,
    tags: set[str] | None = None,
    annotations: Annotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """Mark a function as a resource for filesystem-based discovery.

    This decorator stores metadata on the function but does NOT register it.
    FileSystemProvider discovers marked functions when scanning directories.

    Unlike @tool and @prompt, @resource REQUIRES a URI argument.

    Args:
        uri: URI for the resource (e.g., "config://app" or "users://{user_id}")
        name: Optional name for the resource
        title: Optional title for display
        description: Optional description (defaults to docstring)
        icons: Optional icons for the resource
        mime_type: Optional MIME type
        tags: Optional tags for categorization
        annotations: Optional resource annotations
        meta: Optional metadata dict

    Example:
        ```python
        @resource("config://app")
        def get_config() -> dict:
            return {"setting": "value"}

        @resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> dict:
            return {"id": user_id, "name": "User"}
        ```
    """
    if inspect.isroutine(uri):
        raise TypeError(
            "The @resource decorator requires a URI. "
            "Use @resource('uri://...') instead of @resource"
        )

    if isinstance(annotations, dict):
        annotations = Annotations(**annotations)

    def decorator(func: AnyFunction) -> AnyFunction:
        resource_meta = ResourceMeta(
            uri=uri,
            name=name,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
        )
        setattr(func, FS_META_ATTR, resource_meta)
        return func

    return decorator


# =============================================================================
# @prompt decorator
# =============================================================================


@overload
def prompt(fn: AnyFunction) -> AnyFunction: ...


@overload
def prompt(
    fn: None = None,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any: ...


@overload
def prompt(
    fn: str,
    *,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any: ...


def prompt(
    fn: AnyFunction | str | None = None,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[mcp.types.Icon] | None = None,
    tags: set[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """Mark a function as a prompt for filesystem-based discovery.

    This decorator stores metadata on the function but does NOT register it.
    FileSystemProvider discovers marked functions when scanning directories.

    Supports multiple calling patterns:
    - @prompt (without parentheses)
    - @prompt() (with empty parentheses)
    - @prompt("custom_name") (with name as first argument)
    - @prompt(name="custom_name") (with keyword arguments)

    Args:
        fn: The function to decorate, or a name string, or None
        name: Optional name for the prompt (defaults to function name)
        title: Optional title for display
        description: Optional description (defaults to docstring)
        icons: Optional icons for the prompt
        tags: Optional tags for categorization
        meta: Optional metadata dict

    Example:
        ```python
        @prompt
        def analyze(topic: str) -> list:
            '''Analyze a topic.'''
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        @prompt(name="custom-analyze")
        def my_analyze(topic: str) -> list:
            return [{"role": "user", "content": topic}]
        ```
    """

    def decorator(func: AnyFunction) -> AnyFunction:
        prompt_meta = PromptMeta(
            name=name,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
        )
        setattr(func, FS_META_ATTR, prompt_meta)
        return func

    if inspect.isroutine(fn):
        # @prompt without parentheses
        return decorator(fn)
    elif isinstance(fn, str):
        # @prompt("custom_name")
        return prompt(
            name=fn,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
        )
    else:
        # @prompt() or @prompt(name="...") - return decorator
        return decorator
