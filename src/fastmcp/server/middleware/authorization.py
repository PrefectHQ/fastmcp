"""Authorization middleware for FastMCP.

This module provides middleware-based authorization using callable auth checks.
AuthMiddleware applies auth checks globally to all components on the server.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth import require_auth, require_scopes, restrict_tag
    from fastmcp.server.middleware import AuthMiddleware

    # Require auth for all components
    mcp = FastMCP(middleware=[
        AuthMiddleware(auth=require_auth)
    ])

    # Tag-based: components tagged "admin" require "admin" scope
    mcp = FastMCP(middleware=[
        AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"]))
    ])
    ```
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import mcp.types as mt

from fastmcp.exceptions import AuthorizationError
from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth.authorization import (
    AuthCheck,
    AuthContext,
    run_auth_checks,
)
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class AuthMiddleware(Middleware):
    """Global authorization middleware using callable checks.

    This middleware applies auth checks to all components (tools, resources,
    prompts) on the server. It uses the same callable API as component-level
    auth checks.

    The middleware:
    - Filters tools/resources/prompts from list responses based on auth checks
    - Checks auth before tool execution, resource read, and prompt render
    - Skips all auth checks for STDIO transport (no OAuth concept)

    Args:
        auth: A single auth check function or list of check functions.
            All checks must pass for authorization to succeed (AND logic).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth import require_auth, require_scopes

        # Require any authentication for all components
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_auth)])

        # Require specific scope for all components
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("api"))])

        # Combined checks (AND logic)
        mcp = FastMCP(middleware=[
            AuthMiddleware(auth=[require_auth, require_scopes("api")])
        ])
        ```
    """

    def __init__(self, auth: AuthCheck | list[AuthCheck]) -> None:
        self.auth = auth

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter tools/list response based on auth checks."""
        tools = await call_next(context)

        # STDIO has no auth concept, skip filtering
        # Late import to avoid circular import with context.py
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return tools

        token = get_access_token()

        authorized_tools: list[Tool] = []
        for tool in tools:
            ctx = AuthContext(token=token, component=tool)
            if run_auth_checks(self.auth, ctx):
                authorized_tools.append(tool)

        return authorized_tools

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Check auth before tool execution."""
        # STDIO has no auth concept, skip enforcement
        # Late import to avoid circular import with context.py
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return await call_next(context)

        # Get the tool being called
        tool_name = context.message.name
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            # Fail closed: deny access when context is missing
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for tool '{tool_name}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': missing context"
            )

        # Get tool (component auth is checked in get_tool, raises if unauthorized)
        tool = await fastmcp.fastmcp.get_tool(tool_name)
        if tool is None:
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': tool not found"
            )

        # Global auth check
        token = get_access_token()
        ctx = AuthContext(token=token, component=tool)
        if not run_auth_checks(self.auth, ctx):
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': insufficient permissions"
            )

        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Filter resources/list response based on auth checks."""
        resources = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return resources

        token = get_access_token()

        authorized_resources: list[Resource] = []
        for resource in resources:
            ctx = AuthContext(token=token, component=resource)
            if run_auth_checks(self.auth, ctx):
                authorized_resources.append(resource)

        return authorized_resources

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Check auth before resource read."""
        # STDIO has no auth concept, skip enforcement
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return await call_next(context)

        # Get the resource being read
        uri = context.message.uri
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for resource '{uri}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': missing context"
            )

        # Get resource/template (component auth is checked in get_*, raises if unauthorized)
        component = await fastmcp.fastmcp.get_resource(str(uri))
        if component is None:
            component = await fastmcp.fastmcp.get_resource_template(str(uri))
        if component is None:
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': resource not found"
            )

        # Global auth check
        token = get_access_token()
        ctx = AuthContext(token=token, component=component)
        if not run_auth_checks(self.auth, ctx):
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': insufficient permissions"
            )

        return await call_next(context)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        """Filter resource templates/list response based on auth checks."""
        templates = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return templates

        token = get_access_token()

        authorized_templates: list[ResourceTemplate] = []
        for template in templates:
            ctx = AuthContext(token=token, component=template)
            if run_auth_checks(self.auth, ctx):
                authorized_templates.append(template)

        return authorized_templates

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        """Filter prompts/list response based on auth checks."""
        prompts = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return prompts

        token = get_access_token()

        authorized_prompts: list[Prompt] = []
        for prompt in prompts:
            ctx = AuthContext(token=token, component=prompt)
            if run_auth_checks(self.auth, ctx):
                authorized_prompts.append(prompt)

        return authorized_prompts

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        """Check auth before prompt render."""
        # STDIO has no auth concept, skip enforcement
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return await call_next(context)

        # Get the prompt being rendered
        prompt_name = context.message.name
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for prompt '{prompt_name}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': missing context"
            )

        # Get prompt (component auth is checked in get_prompt, raises if unauthorized)
        prompt = await fastmcp.fastmcp.get_prompt(prompt_name)
        if prompt is None:
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': prompt not found"
            )

        # Global auth check
        token = get_access_token()
        ctx = AuthContext(token=token, component=prompt)
        if not run_auth_checks(self.auth, ctx):
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': insufficient permissions"
            )

        return await call_next(context)
