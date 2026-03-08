"""Policy engine for SecureMCP.

The PolicyEngine is the core evaluation component that coordinates policy
providers, supports hot-swapping, and maintains an audit trail of decisions.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyProvider,
    PolicyResult,
)

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus
    from fastmcp.server.security.policy.audit import PolicyAuditLog
    from fastmcp.server.security.policy.versioning.manager import PolicyVersionManager

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = [
    "PolicyDecision",
    "PolicyEngine",
    "PolicyEvaluationContext",
    "PolicyResult",
]


@dataclass
class PolicySwapRecord:
    """Record of a policy hot-swap event."""

    old_policy_id: str
    old_version: str
    new_policy_id: str
    new_version: str
    swapped_at: datetime
    reason: str


class PolicyViolationError(Exception):
    """Raised when a policy evaluation results in DENY."""

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        super().__init__(
            f"Policy violation ({result.policy_id}): {result.reason}"
        )


class PolicyEngine:
    """Core policy evaluation engine with hot-swap and audit support.

    The engine evaluates requests against one or more policy providers.
    When multiple providers are configured, ALL must allow the request
    (AND logic, fail-closed).

    Args:
        providers: One or more policy providers to evaluate against.
        fail_closed: If True (default), deny access when evaluation fails
            or raises an exception.
        allow_hot_swap: If True (default), permit runtime policy replacement.

    Example::

        from fastmcp.server.security.policy import PolicyEngine, AllowAllPolicy

        engine = PolicyEngine(providers=[AllowAllPolicy()])
        result = await engine.evaluate(context)
    """

    def __init__(
        self,
        providers: list[PolicyProvider] | PolicyProvider | None = None,
        *,
        fail_closed: bool = True,
        allow_hot_swap: bool = True,
        event_bus: SecurityEventBus | None = None,
        audit_log: PolicyAuditLog | None = None,
        version_manager: PolicyVersionManager | None = None,
    ) -> None:
        if providers is None:
            self._providers: list[PolicyProvider] = [AllowAllPolicy()]
        elif isinstance(providers, list):
            self._providers = list(providers)
        else:
            self._providers = [providers]

        self.fail_closed = fail_closed
        self.allow_hot_swap = allow_hot_swap
        self._event_bus = event_bus
        self._audit_log = audit_log
        self._version_manager = version_manager
        self._swap_lock = asyncio.Lock()
        self._swap_history: list[PolicySwapRecord] = []
        self._evaluation_count: int = 0
        self._deny_count: int = 0

    @property
    def providers(self) -> list[PolicyProvider]:
        """Current active policy providers (read-only copy)."""
        return list(self._providers)

    @property
    def evaluation_count(self) -> int:
        """Total number of policy evaluations performed."""
        return self._evaluation_count

    @property
    def deny_count(self) -> int:
        """Total number of DENY decisions."""
        return self._deny_count

    @property
    def audit_log(self) -> PolicyAuditLog | None:
        """The attached audit log, if any."""
        return self._audit_log

    @property
    def swap_history(self) -> list[PolicySwapRecord]:
        """History of policy hot-swaps (read-only copy)."""
        return list(self._swap_history)

    @property
    def version_manager(self) -> PolicyVersionManager | None:
        """The attached version manager, if any."""
        return self._version_manager

    async def evaluate(
        self, context: PolicyEvaluationContext
    ) -> PolicyResult:
        """Evaluate a request against all configured policy providers.

        All providers must return ALLOW for the overall result to be ALLOW.
        Any DENY immediately short-circuits. DEFER is treated as ALLOW
        unless all providers defer (then fail_closed determines outcome).

        Args:
            context: The evaluation context with actor, action, resource details.

        Returns:
            The aggregate PolicyResult.
        """
        self._evaluation_count += 1
        start_time = datetime.now(timezone.utc)

        results: list[PolicyResult] = []
        for provider in self._providers:
            try:
                result = provider.evaluate(context)
                if inspect.isawaitable(result):
                    result = await result
                results.append(result)

                if result.decision == PolicyDecision.DENY:
                    self._deny_count += 1
                    if self._event_bus is not None:
                        from fastmcp.server.security.alerts.models import (
                            AlertSeverity,
                            SecurityEvent,
                            SecurityEventType,
                        )

                        self._event_bus.emit(
                            SecurityEvent(
                                event_type=SecurityEventType.POLICY_DENIED,
                                severity=AlertSeverity.WARNING,
                                layer="policy",
                                message=f"Policy denied: {result.reason}",
                                actor_id=context.actor_id,
                                resource_id=context.resource_id,
                                data={
                                    "policy_id": result.policy_id,
                                    "action": context.action,
                                },
                            )
                        )
                    logger.info(
                        "Policy DENY from %s: %s (action=%s, resource=%s)",
                        result.policy_id,
                        result.reason,
                        context.action,
                        context.resource_id,
                    )
                    self._record_audit(context, result, start_time)
                    return result

            except Exception:
                logger.warning(
                    "Policy provider raised exception during evaluation",
                    exc_info=True,
                )
                if self.fail_closed:
                    self._deny_count += 1
                    fail_result = PolicyResult(
                        decision=PolicyDecision.DENY,
                        reason="Policy evaluation failed (fail-closed)",
                        policy_id="engine-error",
                    )
                    self._record_audit(context, fail_result, start_time)
                    return fail_result

        # All providers evaluated without DENY
        if not results:
            if self.fail_closed:
                self._deny_count += 1
                no_prov_result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="No policy providers configured (fail-closed)",
                    policy_id="engine-no-providers",
                )
                self._record_audit(context, no_prov_result, start_time)
                return no_prov_result
            no_prov_allow = PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="No policy providers configured",
                policy_id="engine-no-providers",
            )
            self._record_audit(context, no_prov_allow, start_time)
            return no_prov_allow

        # Check if all deferred
        all_deferred = all(r.decision == PolicyDecision.DEFER for r in results)
        if all_deferred:
            if self.fail_closed:
                self._deny_count += 1
                defer_result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="All providers deferred (fail-closed)",
                    policy_id="engine-all-deferred",
                )
                self._record_audit(context, defer_result, start_time)
                return defer_result
            defer_allow = PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="All providers deferred (fail-open)",
                policy_id="engine-all-deferred",
            )
            self._record_audit(context, defer_allow, start_time)
            return defer_allow

        # At least one ALLOW, no DENY → aggregate ALLOW
        allow_result = next(r for r in results if r.decision == PolicyDecision.ALLOW)
        # Merge constraints from all results
        all_constraints = []
        for r in results:
            all_constraints.extend(r.constraints)

        final_result = PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=allow_result.reason,
            policy_id=allow_result.policy_id,
            constraints=all_constraints,
        )
        self._record_audit(context, final_result, start_time)
        return final_result

    def _record_audit(
        self,
        context: PolicyEvaluationContext,
        result: PolicyResult,
        start_time: datetime,
    ) -> None:
        """Record an evaluation to the audit log if one is attached."""
        if self._audit_log is None:
            return
        elapsed_ms = (
            datetime.now(timezone.utc) - start_time
        ).total_seconds() * 1000
        try:
            self._audit_log.record(context, result, elapsed_ms=elapsed_ms)
        except Exception:
            logger.warning("Failed to record audit entry", exc_info=True)

    async def hot_swap(
        self,
        index: int,
        new_provider: PolicyProvider,
        *,
        reason: str = "Manual hot-swap",
    ) -> PolicySwapRecord:
        """Atomically replace a policy provider at runtime.

        Args:
            index: Index of the provider to replace.
            new_provider: The new policy provider.
            reason: Human-readable reason for the swap.

        Returns:
            A record of the swap event.

        Raises:
            RuntimeError: If hot-swapping is disabled.
            IndexError: If the index is out of range.
        """
        if not self.allow_hot_swap:
            raise RuntimeError("Hot-swapping is disabled for this engine")

        if index < 0 or index >= len(self._providers):
            raise IndexError(
                f"Provider index {index} out of range (0-{len(self._providers) - 1})"
            )

        async with self._swap_lock:
            old_provider = self._providers[index]
            old_id = await self._resolve_async(old_provider.get_policy_id())
            old_version = await self._resolve_async(old_provider.get_policy_version())
            new_id = await self._resolve_async(new_provider.get_policy_id())
            new_version = await self._resolve_async(new_provider.get_policy_version())

            # Snapshot the pre-swap state if versioning is enabled
            if self._version_manager is not None:
                try:
                    self._version_manager.create_version(
                        policy_data={
                            "swapped_index": index,
                            "old_policy_id": old_id,
                            "old_version": old_version,
                            "new_policy_id": new_id,
                            "new_version": new_version,
                            "provider_count": len(self._providers),
                        },
                        author="policy-engine",
                        description=f"Hot-swap: {old_id}@{old_version} → {new_id}@{new_version} ({reason})",
                    )
                except Exception:
                    logger.warning(
                        "Failed to create version snapshot for hot-swap",
                        exc_info=True,
                    )

            self._providers[index] = new_provider

            record = PolicySwapRecord(
                old_policy_id=old_id,
                old_version=old_version,
                new_policy_id=new_id,
                new_version=new_version,
                swapped_at=datetime.now(timezone.utc),
                reason=reason,
            )
            self._swap_history.append(record)

            if self._event_bus is not None:
                from fastmcp.server.security.alerts.models import (
                    AlertSeverity,
                    SecurityEvent,
                    SecurityEventType,
                )

                self._event_bus.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.POLICY_SWAPPED,
                        severity=AlertSeverity.INFO,
                        layer="policy",
                        message=f"Policy swapped: {old_id}@{old_version} → {new_id}@{new_version}",
                        data={
                            "old_policy_id": old_id,
                            "old_version": old_version,
                            "new_policy_id": new_id,
                            "new_version": new_version,
                            "reason": reason,
                        },
                    )
                )

            logger.info(
                "Policy hot-swap: %s@%s -> %s@%s (%s)",
                old_id,
                old_version,
                new_id,
                new_version,
                reason,
            )
            return record

    async def add_provider(self, provider: PolicyProvider) -> None:
        """Add a new policy provider to the evaluation chain."""
        async with self._swap_lock:
            self._providers.append(provider)
            pid = await self._resolve_async(provider.get_policy_id())
            logger.info("Added policy provider: %s", pid)

    async def remove_provider(self, index: int) -> PolicyProvider:
        """Remove a policy provider by index."""
        async with self._swap_lock:
            if index < 0 or index >= len(self._providers):
                raise IndexError(
                    f"Provider index {index} out of range (0-{len(self._providers) - 1})"
                )
            removed = self._providers.pop(index)
            pid = await self._resolve_async(removed.get_policy_id())
            logger.info("Removed policy provider: %s", pid)
            return removed

    @staticmethod
    async def _resolve_async(value: Any) -> Any:
        """Resolve a value that might be a coroutine."""
        if inspect.isawaitable(value):
            return await value
        return value
