"""Reflexive monitoring middleware for SecureMCP.

Observes MCP operation patterns, feeds them to the behavioral analyzer,
and triggers escalation when drift is detected.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
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
from fastmcp.server.security.reflexive.analyzer import (
    BehavioralAnalyzer,
    EscalationAction,
    EscalationEngine,
)
from fastmcp.server.security.reflexive.models import (
    DriftEvent,
)
from fastmcp.server.security.reflexive.profiles import ActorProfileManager
from fastmcp.tools.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class ReflexiveMiddleware(Middleware):
    """Middleware that monitors behavioral patterns and detects drift.

    Feeds operation metrics (call frequency, error rates, latency)
    to the BehavioralAnalyzer and triggers escalation via the
    EscalationEngine when anomalies are detected.

    If a SUSPEND_AGENT or SHUTDOWN escalation is triggered, subsequent
    requests from that actor are blocked.

    Args:
        analyzer: The behavioral analyzer for drift detection.
        escalation_engine: Engine for processing drift events.
        bypass_stdio: If True (default), skip monitoring for STDIO transport.
        profile_manager: Optional ActorProfileManager for scope tracking
            and threat scoring. If None, one is created automatically.
    """

    def __init__(
        self,
        analyzer: BehavioralAnalyzer,
        escalation_engine: EscalationEngine | None = None,
        *,
        bypass_stdio: bool = True,
        profile_manager: ActorProfileManager | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.escalation_engine = escalation_engine or EscalationEngine()
        self.bypass_stdio = bypass_stdio
        self.profile_manager = profile_manager or ActorProfileManager()
        self._suspended_actors: set[str] = set()
        self._call_timestamps: dict[str, list[float]] = defaultdict(list)

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

    def _check_suspended(self, actor_id: str) -> None:
        """Raise if the actor has been suspended."""
        if actor_id in self._suspended_actors:
            raise PermissionError(
                f"Agent '{actor_id}' has been suspended due to behavioral drift"
            )

    def _compute_call_rate(self, actor_id: str) -> float:
        """Compute calls per minute for the actor over the last 60 seconds."""
        now = time.monotonic()
        window = 60.0
        timestamps = self._call_timestamps[actor_id]

        # Prune old entries
        cutoff = now - window
        self._call_timestamps[actor_id] = [t for t in timestamps if t > cutoff]

        return len(self._call_timestamps[actor_id])

    def _record_call(self, actor_id: str) -> None:
        """Record a call timestamp for rate computation."""
        self._call_timestamps[actor_id].append(time.monotonic())

    def _process_drift(self, events: list[DriftEvent]) -> None:
        """Process drift events through escalation engine and profile manager."""
        for event in events:
            # Update actor threat score
            self.profile_manager.record_drift(event.actor_id, event)

            if self.escalation_engine is None:
                continue
            actions = self.escalation_engine.evaluate(event)
            for action, rule in actions:
                if action in (
                    EscalationAction.SUSPEND_AGENT,
                    EscalationAction.SHUTDOWN,
                ):
                    self._suspended_actors.add(event.actor_id)
                    logger.warning(
                        "Actor %s suspended due to escalation rule %s",
                        event.actor_id,
                        rule.rule_id,
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
        self._check_suspended(actor_id)

        # Track tool scope
        tool_name = context.params.name if context.params else "unknown"
        is_new_tool = self.profile_manager.record_tool_access(actor_id, tool_name)

        # Record call and compute rate
        self._record_call(actor_id)
        call_rate = self._compute_call_rate(actor_id)

        # Observe call frequency
        drift_events = self.analyzer.observe(actor_id, "calls_per_minute", call_rate)

        # Observe scope expansion
        if is_new_tool:
            scope_size = self.profile_manager.get_or_create(actor_id).scope_size
            drift_events.extend(
                self.analyzer.observe(
                    actor_id,
                    "scope_expansion",
                    float(scope_size),
                    metadata={"new_tool": tool_name},
                )
            )

        # Measure latency
        start = time.monotonic()
        try:
            result = await call_next(context)
            latency = (time.monotonic() - start) * 1000  # ms

            # Observe latency
            drift_events.extend(self.analyzer.observe(actor_id, "latency_ms", latency))

            # Observe success (0 = success for error rate)
            drift_events.extend(self.analyzer.observe(actor_id, "error_rate", 0.0))

            self._process_drift(drift_events)
            return result

        except Exception:
            latency = (time.monotonic() - start) * 1000
            drift_events.extend(self.analyzer.observe(actor_id, "latency_ms", latency))
            drift_events.extend(self.analyzer.observe(actor_id, "error_rate", 1.0))
            self._process_drift(drift_events)
            raise

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
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
        self._check_suspended(actor_id)

        # Track resource scope
        resource_uri = str(context.params.uri) if context.params else "unknown"
        is_new_resource = self.profile_manager.record_resource_access(
            actor_id, resource_uri
        )

        self._record_call(actor_id)
        call_rate = self._compute_call_rate(actor_id)
        drift_events = self.analyzer.observe(actor_id, "calls_per_minute", call_rate)

        # Observe scope expansion
        if is_new_resource:
            scope_size = self.profile_manager.get_or_create(actor_id).scope_size
            drift_events.extend(
                self.analyzer.observe(
                    actor_id,
                    "scope_expansion",
                    float(scope_size),
                    metadata={"new_resource": resource_uri},
                )
            )

        try:
            result = await call_next(context)
            self._process_drift(drift_events)
            return result
        except Exception:
            drift_events.extend(self.analyzer.observe(actor_id, "error_rate", 1.0))
            self._process_drift(drift_events)
            raise

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
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
        self._check_suspended(actor_id)

        # Track prompt scope
        prompt_name = context.params.name if context.params else "unknown"
        self.profile_manager.record_prompt_access(actor_id, prompt_name)

        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        return await call_next(context)
