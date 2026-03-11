"""Contract validation middleware for SecureMCP.

Intercepts MCP operations and verifies that an active, valid contract
exists before allowing execution. Follows the same patterns as
PolicyEnforcementMiddleware: fail-closed, STDIO bypass.
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
from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.contracts.schema import Contract
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class ContractViolationError(Exception):
    """Raised when an operation violates contract requirements."""

    def __init__(self, message: str, contract_id: str | None = None) -> None:
        super().__init__(message)
        self.contract_id = contract_id


class ContractValidationMiddleware(Middleware):
    """Middleware that validates active contracts before MCP operations.

    Checks that the requesting agent has an active, valid contract
    with this server before allowing tool calls, resource reads,
    or prompt renders.

    Follows the same security patterns as PolicyEnforcementMiddleware:
    - Fail-closed when no contract found
    - STDIO transport bypass (configurable)

    Args:
        broker: The ContextBroker managing contracts.
        bypass_stdio: If True (default), skip contract checks for STDIO transport.
        require_for_list: If True, require contracts for list operations too.
            Default is False (lists are unrestricted).
    """

    def __init__(
        self,
        broker: ContextBroker,
        *,
        bypass_stdio: bool = True,
        require_for_list: bool = False,
    ) -> None:
        self.broker = broker
        self.bypass_stdio = bypass_stdio
        self.require_for_list = require_for_list

    def _should_bypass(self) -> bool:
        """Check if contract checks should be skipped for current transport."""
        if not self.bypass_stdio:
            return False
        from fastmcp.server.context import _current_transport

        return _current_transport.get() == "stdio"

    def _get_agent_id(
        self,
        context: MiddlewareContext,
    ) -> str | None:
        """Extract agent ID from middleware context."""
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return None
        try:
            token = fastmcp_ctx.access_token
            if token is not None:
                return token.token[:8] + "..."
        except Exception:
            pass
        return None

    def _find_valid_contract(self, agent_id: str | None) -> Contract | None:
        """Find a valid active contract for the agent."""
        if agent_id is None:
            return None
        contracts = self.broker.get_active_contracts_for_agent(agent_id)
        return contracts[0] if contracts else None

    def _check_term_constraint(
        self,
        contract: Contract,
        action: str,
        resource_id: str,
    ) -> str | None:
        """Check if the action is permitted by contract terms.

        Returns None if allowed, or an error message if denied.
        """
        for term in contract.terms:
            constraint = term.constraint

            # Check action restrictions
            denied_actions = constraint.get("denied_actions", [])
            if action in denied_actions:
                return f"Contract term '{term.term_id}' denies action '{action}'"

            allowed_actions = constraint.get("allowed_actions")
            if allowed_actions is not None and action not in allowed_actions:
                return (
                    f"Contract term '{term.term_id}' does not allow action '{action}'"
                )

            # Check resource restrictions
            denied_resources = constraint.get("denied_resources", [])
            if resource_id in denied_resources:
                return (
                    f"Contract term '{term.term_id}' denies access to '{resource_id}'"
                )

            allowed_resources = constraint.get("allowed_resources")
            if allowed_resources is not None and resource_id not in allowed_resources:
                return (
                    f"Contract term '{term.term_id}' does not allow access "
                    f"to '{resource_id}'"
                )

            # Check read_only constraint
            if constraint.get("read_only") and action in ("call_tool",):
                return f"Contract is read-only; action '{action}' is not permitted"

        return None  # All checks passed

    # ── Tool operations ──────────────────────────────────────────────

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Validate contract before tool execution."""
        if self._should_bypass():
            return await call_next(context)

        tool_name = context.message.name
        agent_id = self._get_agent_id(context)

        contract = self._find_valid_contract(agent_id)
        if contract is None:
            raise ContractViolationError(
                f"No active contract for agent '{agent_id}' to call tool '{tool_name}'"
            )

        # Check term constraints
        violation = self._check_term_constraint(contract, "call_tool", tool_name)
        if violation:
            raise ContractViolationError(violation, contract_id=contract.contract_id)

        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Optionally filter tools list based on contract."""
        tools = await call_next(context)

        if self._should_bypass() or not self.require_for_list:
            return tools

        agent_id = self._get_agent_id(context)
        contract = self._find_valid_contract(agent_id)
        if contract is None:
            return []  # No contract = no visibility

        # Filter tools by contract constraints
        permitted: list[Tool] = []
        for tool in tools:
            violation = self._check_term_constraint(contract, "call_tool", tool.name)
            if violation is None:
                permitted.append(tool)

        return permitted

    # ── Resource operations ──────────────────────────────────────────

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Validate contract before resource read."""
        if self._should_bypass():
            return await call_next(context)

        uri = str(context.message.uri)
        agent_id = self._get_agent_id(context)

        contract = self._find_valid_contract(agent_id)
        if contract is None:
            raise ContractViolationError(
                f"No active contract for agent '{agent_id}' to read resource '{uri}'"
            )

        violation = self._check_term_constraint(contract, "read_resource", uri)
        if violation:
            raise ContractViolationError(violation, contract_id=contract.contract_id)

        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Optionally filter resources list based on contract."""
        resources = await call_next(context)

        if self._should_bypass() or not self.require_for_list:
            return resources

        agent_id = self._get_agent_id(context)
        contract = self._find_valid_contract(agent_id)
        if contract is None:
            return []

        permitted: list[Resource] = []
        for resource in resources:
            violation = self._check_term_constraint(
                contract, "read_resource", str(resource.uri)
            )
            if violation is None:
                permitted.append(resource)

        return permitted

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        """Pass through resource templates (contract doesn't restrict discovery)."""
        return await call_next(context)

    # ── Prompt operations ────────────────────────────────────────────

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        """Validate contract before prompt render."""
        if self._should_bypass():
            return await call_next(context)

        prompt_name = context.message.name
        agent_id = self._get_agent_id(context)

        contract = self._find_valid_contract(agent_id)
        if contract is None:
            raise ContractViolationError(
                f"No active contract for agent '{agent_id}' "
                f"to access prompt '{prompt_name}'"
            )

        violation = self._check_term_constraint(contract, "get_prompt", prompt_name)
        if violation:
            raise ContractViolationError(violation, contract_id=contract.contract_id)

        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        """Optionally filter prompts list based on contract."""
        prompts = await call_next(context)

        if self._should_bypass() or not self.require_for_list:
            return prompts

        agent_id = self._get_agent_id(context)
        contract = self._find_valid_contract(agent_id)
        if contract is None:
            return []

        permitted: list[Prompt] = []
        for prompt in prompts:
            violation = self._check_term_constraint(contract, "get_prompt", prompt.name)
            if violation is None:
                permitted.append(prompt)

        return permitted
