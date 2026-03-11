"""Policy dry-run and simulation mode.

Test what a policy engine *would* decide without enforcing the result.
Useful for:

- Validating new policies before deploying them
- Auditing how a rule change would affect existing traffic
- Testing edge cases in a REPL or CI pipeline

Example::

    from fastmcp.server.security.policy.simulation import simulate, Scenario

    scenarios = [
        Scenario(resource_id="weather-lookup", action="call_tool", actor_id="agent-1"),
        Scenario(resource_id="admin-panel", action="call_tool", actor_id="agent-1"),
        Scenario(resource_id="admin-panel", action="call_tool", actor_id="admin"),
    ]

    report = await simulate(engine, scenarios)
    print(report.summary())
    for result in report.results:
        print(f"{result.scenario.resource_id}: {result.decision}")
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyProvider,
    PolicyResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scenario:
    """A single test scenario for policy simulation.

    Attributes:
        resource_id: The resource being accessed.
        action: The action being performed (default: "call_tool").
        actor_id: The actor making the request (default: "sim-actor").
        metadata: Additional context metadata.
        tags: Tags on the resource.
        label: Optional human-readable label for the scenario.
    """

    resource_id: str
    action: str = "call_tool"
    actor_id: str = "sim-actor"
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)
    label: str = ""

    def to_context(self) -> PolicyEvaluationContext:
        """Convert to a PolicyEvaluationContext."""
        return PolicyEvaluationContext(
            actor_id=self.actor_id,
            action=self.action,
            resource_id=self.resource_id,
            metadata=self.metadata,
            tags=self.tags,
        )


@dataclass(frozen=True)
class ScenarioResult:
    """Result of evaluating a single scenario.

    Attributes:
        scenario: The scenario that was evaluated.
        decision: The final decision (ALLOW/DENY/DEFER).
        reason: Human-readable reason for the decision.
        policy_id: ID of the policy that made the decision.
        constraints: Any constraints attached to an ALLOW.
        per_provider: Per-provider breakdown (if available).
        error: Error message if evaluation failed.
        elapsed_ms: Time taken for evaluation in milliseconds.
    """

    scenario: Scenario
    decision: PolicyDecision
    reason: str
    policy_id: str
    constraints: list[str] = field(default_factory=list)
    per_provider: list[ProviderResult] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class ProviderResult:
    """Result from a single provider within a simulation.

    Attributes:
        policy_id: The provider's policy ID.
        decision: The provider's decision.
        reason: The provider's reason.
    """

    policy_id: str
    decision: PolicyDecision
    reason: str


@dataclass
class SimulationReport:
    """Aggregated results from a simulation run.

    Attributes:
        results: Individual scenario results.
        total: Total number of scenarios.
        allowed: Number of ALLOW decisions.
        denied: Number of DENY decisions.
        deferred: Number of DEFER decisions.
        errors: Number of evaluation errors.
        created_at: When the simulation was run.
    """

    results: list[ScenarioResult] = field(default_factory=list)
    total: int = 0
    allowed: int = 0
    denied: int = 0
    deferred: int = 0
    errors: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        """Return a human-readable summary of the simulation."""
        lines = [
            f"Simulation Report ({self.total} scenarios)",
            f"  ALLOW:  {self.allowed}",
            f"  DENY:   {self.denied}",
            f"  DEFER:  {self.deferred}",
            f"  ERRORS: {self.errors}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Export as a JSON-serializable dict."""
        return {
            "total": self.total,
            "allowed": self.allowed,
            "denied": self.denied,
            "deferred": self.deferred,
            "errors": self.errors,
            "created_at": self.created_at.isoformat(),
            "results": [
                {
                    "resource_id": r.scenario.resource_id,
                    "action": r.scenario.action,
                    "actor_id": r.scenario.actor_id,
                    "label": r.scenario.label,
                    "decision": r.decision.value,
                    "reason": r.reason,
                    "policy_id": r.policy_id,
                    "constraints": r.constraints,
                    "error": r.error,
                    "elapsed_ms": r.elapsed_ms,
                    "per_provider": [
                        {
                            "policy_id": p.policy_id,
                            "decision": p.decision.value,
                            "reason": p.reason,
                        }
                        for p in r.per_provider
                    ],
                }
                for r in self.results
            ],
        }

    def filter_by_decision(self, decision: PolicyDecision) -> list[ScenarioResult]:
        """Return only results matching a specific decision."""
        return [r for r in self.results if r.decision == decision]


async def _evaluate_provider(
    provider: PolicyProvider,
    context: PolicyEvaluationContext,
) -> ProviderResult:
    """Evaluate a single provider and return a ProviderResult."""
    result = provider.evaluate(context)
    if inspect.isawaitable(result):
        result = await result

    pid = provider.get_policy_id()
    if inspect.isawaitable(pid):
        pid = await pid

    return ProviderResult(
        policy_id=pid,
        decision=result.decision,
        reason=result.reason,
    )


async def simulate(
    target: Any,
    scenarios: list[Scenario],
    *,
    fail_closed: bool = True,
) -> SimulationReport:
    """Run a dry-run simulation against a policy engine or provider list.

    Evaluates each scenario without any side effects: no event bus
    emissions, no counter increments, no rate-limit state changes.

    Args:
        target: A ``PolicyEngine``, a single ``PolicyProvider``, or
            a list of ``PolicyProvider`` instances.
        scenarios: List of scenarios to evaluate.
        fail_closed: Whether to deny on evaluation errors (default: True).

    Returns:
        A SimulationReport with per-scenario results and aggregate stats.

    Example::

        report = await simulate(engine, [
            Scenario(resource_id="tool-a"),
            Scenario(resource_id="tool-b", actor_id="admin"),
        ])
        assert report.allowed == 2
    """
    # Normalize target to a list of providers
    providers: list[PolicyProvider]

    # Import here to avoid circular imports
    from fastmcp.server.security.policy.engine import PolicyEngine

    if isinstance(target, PolicyEngine):
        providers = target.providers  # Gets a copy
    elif isinstance(target, list):
        providers = list(target)
    else:
        # Single provider
        providers = [target]

    report = SimulationReport(total=len(scenarios))

    for scenario in scenarios:
        context = scenario.to_context()
        start = datetime.now(timezone.utc)
        per_provider: list[ProviderResult] = []
        final_result: PolicyResult | None = None
        error: str | None = None

        try:
            for provider in providers:
                try:
                    pr = await _evaluate_provider(provider, context)
                    per_provider.append(pr)

                    if pr.decision == PolicyDecision.DENY:
                        final_result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=pr.reason,
                            policy_id=pr.policy_id,
                        )
                        break

                except Exception as exc:
                    error = f"Provider error: {exc}"
                    if fail_closed:
                        final_result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason="Evaluation failed (fail-closed)",
                            policy_id="simulation-error",
                        )
                        break

            if final_result is None:
                # No DENY found — check for all DEFER
                if not per_provider:
                    if fail_closed:
                        final_result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason="No providers (fail-closed)",
                            policy_id="simulation-no-providers",
                        )
                    else:
                        final_result = PolicyResult(
                            decision=PolicyDecision.ALLOW,
                            reason="No providers",
                            policy_id="simulation-no-providers",
                        )
                elif all(p.decision == PolicyDecision.DEFER for p in per_provider):
                    if fail_closed:
                        final_result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason="All deferred (fail-closed)",
                            policy_id="simulation-all-deferred",
                        )
                    else:
                        final_result = PolicyResult(
                            decision=PolicyDecision.ALLOW,
                            reason="All deferred (fail-open)",
                            policy_id="simulation-all-deferred",
                        )
                else:
                    # At least one ALLOW, no DENY
                    allow_pr = next(
                        p for p in per_provider if p.decision == PolicyDecision.ALLOW
                    )
                    all_constraints: list[str] = []
                    final_result = PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason=allow_pr.reason,
                        policy_id=allow_pr.policy_id,
                        constraints=all_constraints,
                    )

        except Exception as exc:
            error = f"Simulation error: {exc}"
            final_result = PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Simulation error: {exc}",
                policy_id="simulation-error",
            )

        elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        sr = ScenarioResult(
            scenario=scenario,
            decision=final_result.decision,
            reason=final_result.reason,
            policy_id=final_result.policy_id,
            constraints=list(final_result.constraints),
            per_provider=per_provider,
            error=error,
            elapsed_ms=elapsed,
        )
        report.results.append(sr)

        if sr.decision == PolicyDecision.ALLOW:
            report.allowed += 1
        elif sr.decision == PolicyDecision.DENY:
            report.denied += 1
        else:
            report.deferred += 1

        if error:
            report.errors += 1

    return report
