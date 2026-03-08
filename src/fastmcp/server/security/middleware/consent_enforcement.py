"""Consent enforcement middleware for SecureMCP.

Checks the consent graph before allowing MCP operations,
ensuring agents have proper consent grants for the requested
resources and scopes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import mcp.types as mt

from fastmcp.prompts.prompt import Prompt, PromptResult
from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.consent.models import ConsentQuery, ConsentScope
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class ConsentRequiredError(Exception):
    """Raised when an operation lacks required consent."""

    def __init__(self, message: str, source_id: str = "", target_id: str = "", scope: str = ""):
        super().__init__(message)
        self.source_id = source_id
        self.target_id = target_id
        self.scope = scope


class ConsentEnforcementMiddleware(Middleware):
    """Middleware that enforces consent graph access rules.

    Before allowing tool calls, resource reads, or prompt renders,
    this middleware checks that the requesting agent has consent
    from the resource owner.

    The ``resource_owner`` parameter defines a default owner for all
    resources. In practice, tools/resources can declare owners via
    their tags or metadata.

    Args:
        graph: The consent graph to evaluate against.
        resource_owner: Default owner ID for resource consent checks.
        bypass_stdio: Skip consent checks for STDIO transport.
        require_for_list: If True, check consent for list operations.
    """

    def __init__(
        self,
        graph: ConsentGraph,
        *,
        resource_owner: str = "server",
        bypass_stdio: bool = True,
        require_for_list: bool = False,
    ) -> None:
        self.graph = graph
        self.resource_owner = resource_owner
        self.bypass_stdio = bypass_stdio
        self.require_for_list = require_for_list

    def _should_bypass(self) -> bool:
        if not self.bypass_stdio:
            return False
        from fastmcp.server.context import _current_transport

        return _current_transport.get() == "stdio"

    def _get_actor_id(self, context: MiddlewareContext) -> str:
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return "unknown"
        try:
            token = fastmcp_ctx.access_token
            if token is not None:
                return token.token[:8] + "..."
        except Exception:
            pass
        return "anonymous"

    def _check_consent(
        self,
        actor_id: str,
        resource_id: str,
        scope: str,
        context: dict | None = None,
    ) -> None:
        """Check consent and raise if not granted."""
        query = ConsentQuery(
            source_id=self.resource_owner,
            target_id=actor_id,
            scope=scope,
            context=context or {},
        )
        decision = self.graph.evaluate(query)
        if not decision.granted:
            raise ConsentRequiredError(
                f"Consent required: {actor_id} needs '{scope}' consent "
                f"for '{resource_id}' from '{self.resource_owner}'. "
                f"Reason: {decision.reason}",
                source_id=self.resource_owner,
                target_id=actor_id,
                scope=scope,
            )

    # ── Tool operations ──────────────────────────────────────────────

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if self._should_bypass():
            return await call_next(context)

        actor_id = self._get_actor_id(context)
        tool_name = context.message.name if context.message else "unknown"
        self._check_consent(actor_id, tool_name, ConsentScope.EXECUTE.value)

        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        if self.require_for_list and not self._should_bypass():
            actor_id = self._get_actor_id(context)
            self._check_consent(actor_id, "tools", ConsentScope.LIST.value)
        return await call_next(context)

    # ── Resource operations ──────────────────────────────────────────

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        if self._should_bypass():
            return await call_next(context)

        actor_id = self._get_actor_id(context)
        resource_uri = str(context.message.uri) if context.message else "unknown"
        self._check_consent(actor_id, resource_uri, ConsentScope.READ.value)

        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        if self.require_for_list and not self._should_bypass():
            actor_id = self._get_actor_id(context)
            self._check_consent(actor_id, "resources", ConsentScope.LIST.value)
        return await call_next(context)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        return await call_next(context)

    # ── Prompt operations ────────────────────────────────────────────

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        if self._should_bypass():
            return await call_next(context)

        actor_id = self._get_actor_id(context)
        prompt_name = context.message.name if context.message else "unknown"
        self._check_consent(actor_id, prompt_name, ConsentScope.READ.value)

        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        if self.require_for_list and not self._should_bypass():
            actor_id = self._get_actor_id(context)
            self._check_consent(actor_id, "prompts", ConsentScope.LIST.value)
        return await call_next(context)
