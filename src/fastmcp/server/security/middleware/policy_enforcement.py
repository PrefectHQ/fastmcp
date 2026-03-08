"""Policy enforcement middleware for SecureMCP.

Intercepts all MCP operations and evaluates them against the configured
policy engine before allowing execution. Follows the same patterns as
AuthMiddleware: fail-closed, STDIO bypass, list filtering.
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
from fastmcp.server.security.policy.engine import (
    PolicyDecision,
    PolicyEngine,
    PolicyViolationError,
)
from fastmcp.server.security.policy.provider import PolicyEvaluationContext
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class PolicyEnforcementMiddleware(Middleware):
    """Middleware that enforces policy decisions on all MCP operations.

    Evaluates every tool call, resource read, and prompt render against
    the configured PolicyEngine. Also filters list responses to only
    include policy-permitted components.

    Follows the same security patterns as AuthMiddleware:
    - Fail-closed when context is missing
    - STDIO transport bypass (configurable)
    - AND logic across all policy providers

    Args:
        policy_engine: The engine to evaluate requests against.
        bypass_stdio: If True (default), skip policy checks for STDIO transport.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        *,
        bypass_stdio: bool = True,
    ) -> None:
        self.policy_engine = policy_engine
        self.bypass_stdio = bypass_stdio

    def _should_bypass(self) -> bool:
        """Check if policy checks should be skipped for current transport."""
        if not self.bypass_stdio:
            return False
        from fastmcp.server.context import _current_transport

        return _current_transport.get() == "stdio"

    def _build_context(
        self,
        action: str,
        resource_id: str,
        middleware_context: MiddlewareContext[mt.CallToolRequestParams]
        | MiddlewareContext[mt.ReadResourceRequestParams]
        | MiddlewareContext[mt.GetPromptRequestParams],
        extra_metadata: dict | None = None,
    ) -> PolicyEvaluationContext:
        """Build a PolicyEvaluationContext from a MiddlewareContext."""
        actor_id: str | None = None
        tags: frozenset[str] = frozenset()

        # Try to extract actor from access token
        fastmcp_ctx = middleware_context.fastmcp_context
        if fastmcp_ctx is not None:
            try:
                token = fastmcp_ctx.access_token
                if token is not None:
                    actor_id = token.token[:8] + "..."  # Redacted token prefix
            except Exception:
                pass

        metadata: dict = extra_metadata or {}
        metadata["method"] = middleware_context.method
        metadata["source"] = middleware_context.source

        return PolicyEvaluationContext(
            actor_id=actor_id,
            action=action,
            resource_id=resource_id,
            metadata=metadata,
            timestamp=middleware_context.timestamp,
            tags=tags,
        )

    def _build_list_context(
        self,
        action: str,
        resource_id: str,
        tags: frozenset[str],
    ) -> PolicyEvaluationContext:
        """Build a lightweight context for list-level filtering."""
        return PolicyEvaluationContext(
            actor_id=None,
            action=action,
            resource_id=resource_id,
            tags=tags,
        )

    # ── Tool operations ──────────────────────────────────────────────

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Evaluate policy before tool execution."""
        if self._should_bypass():
            return await call_next(context)

        tool_name = context.message.name
        fastmcp_ctx = context.fastmcp_context

        if fastmcp_ctx is None:
            logger.warning(
                "PolicyEnforcement: context is None for tool '%s'. Denying access.",
                tool_name,
            )
            raise PolicyViolationError(
                _deny_result(
                    f"Policy check failed for tool '{tool_name}': missing context"
                )
            )

        eval_ctx = self._build_context(
            action="call_tool",
            resource_id=tool_name,
            middleware_context=context,
            extra_metadata={"arguments": context.message.arguments or {}},
        )

        # Get tool to access its tags
        tool = await fastmcp_ctx.fastmcp.get_tool(tool_name)
        if tool is not None:
            eval_ctx = PolicyEvaluationContext(
                actor_id=eval_ctx.actor_id,
                action=eval_ctx.action,
                resource_id=eval_ctx.resource_id,
                metadata=eval_ctx.metadata,
                timestamp=eval_ctx.timestamp,
                tags=frozenset(tool.tags),
            )

        result = await self.policy_engine.evaluate(eval_ctx)
        if result.decision == PolicyDecision.DENY:
            raise PolicyViolationError(result)

        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter tools list based on policy."""
        tools = await call_next(context)

        if self._should_bypass():
            return tools

        permitted: list[Tool] = []
        for tool in tools:
            eval_ctx = self._build_list_context(
                action="list_tools",
                resource_id=tool.name,
                tags=frozenset(tool.tags),
            )
            try:
                result = await self.policy_engine.evaluate(eval_ctx)
                if result.decision != PolicyDecision.DENY:
                    permitted.append(tool)
            except Exception:
                logger.debug(
                    "Policy evaluation failed for tool '%s' during listing; excluding",
                    tool.name,
                )

        return permitted

    # ── Resource operations ──────────────────────────────────────────

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Evaluate policy before resource read."""
        if self._should_bypass():
            return await call_next(context)

        uri = str(context.message.uri)
        fastmcp_ctx = context.fastmcp_context

        if fastmcp_ctx is None:
            logger.warning(
                "PolicyEnforcement: context is None for resource '%s'. Denying access.",
                uri,
            )
            raise PolicyViolationError(
                _deny_result(
                    f"Policy check failed for resource '{uri}': missing context"
                )
            )

        eval_ctx = self._build_context(
            action="read_resource",
            resource_id=uri,
            middleware_context=context,
        )

        result = await self.policy_engine.evaluate(eval_ctx)
        if result.decision == PolicyDecision.DENY:
            raise PolicyViolationError(result)

        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Filter resources list based on policy."""
        resources = await call_next(context)

        if self._should_bypass():
            return resources

        permitted: list[Resource] = []
        for resource in resources:
            eval_ctx = self._build_list_context(
                action="list_resources",
                resource_id=str(resource.uri),
                tags=frozenset(resource.tags),
            )
            try:
                result = await self.policy_engine.evaluate(eval_ctx)
                if result.decision != PolicyDecision.DENY:
                    permitted.append(resource)
            except Exception:
                logger.debug(
                    "Policy evaluation failed for resource '%s' during listing; excluding",
                    resource.uri,
                )

        return permitted

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        """Filter resource templates list based on policy."""
        templates = await call_next(context)

        if self._should_bypass():
            return templates

        permitted: list[ResourceTemplate] = []
        for template in templates:
            eval_ctx = self._build_list_context(
                action="list_resource_templates",
                resource_id=str(template.uri_template),
                tags=frozenset(template.tags),
            )
            try:
                result = await self.policy_engine.evaluate(eval_ctx)
                if result.decision != PolicyDecision.DENY:
                    permitted.append(template)
            except Exception:
                logger.debug(
                    "Policy evaluation failed for template '%s' during listing; excluding",
                    template.uri_template,
                )

        return permitted

    # ── Prompt operations ────────────────────────────────────────────

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        """Evaluate policy before prompt render."""
        if self._should_bypass():
            return await call_next(context)

        prompt_name = context.message.name
        fastmcp_ctx = context.fastmcp_context

        if fastmcp_ctx is None:
            logger.warning(
                "PolicyEnforcement: context is None for prompt '%s'. Denying access.",
                prompt_name,
            )
            raise PolicyViolationError(
                _deny_result(
                    f"Policy check failed for prompt '{prompt_name}': missing context"
                )
            )

        eval_ctx = self._build_context(
            action="get_prompt",
            resource_id=prompt_name,
            middleware_context=context,
        )

        result = await self.policy_engine.evaluate(eval_ctx)
        if result.decision == PolicyDecision.DENY:
            raise PolicyViolationError(result)

        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        """Filter prompts list based on policy."""
        prompts = await call_next(context)

        if self._should_bypass():
            return prompts

        permitted: list[Prompt] = []
        for prompt in prompts:
            eval_ctx = self._build_list_context(
                action="list_prompts",
                resource_id=prompt.name,
                tags=frozenset(prompt.tags),
            )
            try:
                result = await self.policy_engine.evaluate(eval_ctx)
                if result.decision != PolicyDecision.DENY:
                    permitted.append(prompt)
            except Exception:
                logger.debug(
                    "Policy evaluation failed for prompt '%s' during listing; excluding",
                    prompt.name,
                )

        return permitted


def _deny_result(reason: str) -> "PolicyResult":
    """Create a DENY PolicyResult for error paths."""
    from fastmcp.server.security.policy.provider import PolicyResult

    return PolicyResult(
        decision=PolicyDecision.DENY,
        reason=reason,
        policy_id="policy-enforcement-middleware",
    )
