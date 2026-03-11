"""Sandbox enforcement engine.

Converts SecurityManifests into ExecutionPolicies, tracks execution
context, and blocks operations that violate declared permissions.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.certification.manifest import (
    PermissionScope,
    SecurityManifest,
)
from fastmcp.server.security.sandbox.policies import (
    ExecutionPolicy,
    ResourcePolicy,
    TimeoutPolicy,
)

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus
    from fastmcp.server.security.federation.crl import CertificateRevocationList

logger = logging.getLogger(__name__)


class ViolationAction(Enum):
    """What to do when a sandbox violation occurs."""

    BLOCK = "block"
    WARN = "warn"
    LOG = "log"
    BLOCK_AND_ALERT = "block_and_alert"


@dataclass
class SandboxViolation:
    """A sandbox policy violation.

    Attributes:
        violation_id: Unique identifier.
        tool_name: Tool that violated the policy.
        operation: The blocked operation.
        permission_required: What permission was needed.
        action_taken: What enforcement action was taken.
        timestamp: When the violation occurred.
        details: Additional context.
    """

    violation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_name: str = ""
    operation: str = ""
    permission_required: str = ""
    action_taken: ViolationAction = ViolationAction.BLOCK
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "violation_id": self.violation_id,
            "tool_name": self.tool_name,
            "operation": self.operation,
            "permission_required": self.permission_required,
            "action_taken": self.action_taken.value,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }


@dataclass
class ExecutionContext:
    """Runtime context for a tool execution.

    Tracks execution state, resource accesses, and violations
    during a single tool invocation.

    Attributes:
        context_id: Unique execution identifier.
        tool_name: Tool being executed.
        actor_id: Who initiated the execution.
        policy: The enforced execution policy.
        started_at: Execution start time.
        ended_at: Execution end time.
        violations: Policy violations detected.
        resources_accessed: Resources accessed during execution.
        blocked: Whether execution was blocked.
        block_reason: Why execution was blocked.
    """

    context_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_name: str = ""
    actor_id: str = ""
    policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    violations: list[SandboxViolation] = field(default_factory=list)
    resources_accessed: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since execution started."""
        end = self.ended_at or time.monotonic()
        return end - self.started_at

    @property
    def is_timed_out(self) -> bool:
        """Whether execution has exceeded its time limit."""
        return self.elapsed_seconds > self.policy.timeout_policy.max_execution_seconds

    @property
    def violation_count(self) -> int:
        """Number of violations recorded."""
        return len(self.violations)

    def finish(self) -> None:
        """Mark execution as finished."""
        self.ended_at = time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "context_id": self.context_id,
            "tool_name": self.tool_name,
            "actor_id": self.actor_id,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "violation_count": self.violation_count,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "resources_accessed": self.resources_accessed,
        }


# ── Permission → policy mapping ──────────────────────────────────

_PERMISSION_TO_POLICY: dict[PermissionScope, str] = {
    PermissionScope.READ_RESOURCE: "allow_file_read",
    PermissionScope.WRITE_RESOURCE: "allow_file_write",
    PermissionScope.NETWORK_ACCESS: "allow_network",
    PermissionScope.FILE_SYSTEM_READ: "allow_file_read",
    PermissionScope.FILE_SYSTEM_WRITE: "allow_file_write",
    PermissionScope.ENVIRONMENT_READ: "allow_env_read",
    PermissionScope.SUBPROCESS_EXEC: "allow_subprocess",
    PermissionScope.CROSS_ORIGIN: "allow_cross_origin",
}


class ManifestEnforcer:
    """Converts SecurityManifests into enforceable policies.

    Translates declared permissions into an ExecutionPolicy and
    provides permission-check methods for runtime enforcement.

    Example::

        enforcer = ManifestEnforcer()

        # Create policy from manifest
        policy = enforcer.policy_from_manifest(manifest)

        # Check individual operations
        enforcer.check_permission(context, "network_access")
        enforcer.check_resource(context, "file:///data/input.csv")
    """

    def __init__(
        self,
        *,
        default_action: ViolationAction = ViolationAction.BLOCK,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self._default_action = default_action
        self._event_bus = event_bus

    def policy_from_manifest(self, manifest: SecurityManifest) -> ExecutionPolicy:
        """Create an ExecutionPolicy from a SecurityManifest.

        Maps declared permissions to policy flags. Permissions NOT
        declared in the manifest are blocked by default (deny-by-default).

        Args:
            manifest: The security manifest.

        Returns:
            ExecutionPolicy enforcing the manifest's permissions.
        """
        policy = ExecutionPolicy(
            allow_network=PermissionScope.NETWORK_ACCESS in manifest.permissions,
            allow_file_read=(
                PermissionScope.FILE_SYSTEM_READ in manifest.permissions
                or PermissionScope.READ_RESOURCE in manifest.permissions
            ),
            allow_file_write=(
                PermissionScope.FILE_SYSTEM_WRITE in manifest.permissions
                or PermissionScope.WRITE_RESOURCE in manifest.permissions
            ),
            allow_subprocess=PermissionScope.SUBPROCESS_EXEC in manifest.permissions,
            allow_env_read=PermissionScope.ENVIRONMENT_READ in manifest.permissions,
            allow_cross_origin=PermissionScope.CROSS_ORIGIN in manifest.permissions,
            require_consent=manifest.requires_consent,
        )

        # Build resource patterns from manifest declarations
        if manifest.resource_access:
            allowed: list[str] = []
            for ra in manifest.resource_access:
                allowed.append(ra.resource_pattern)
            policy.resource_policy = ResourcePolicy(allowed_patterns=allowed)

        # Set timeout from manifest
        if manifest.max_execution_time_seconds is not None:
            policy.timeout_policy = TimeoutPolicy(
                max_execution_seconds=manifest.max_execution_time_seconds,
            )

        return policy

    def check_permission(
        self,
        context: ExecutionContext,
        operation: str,
    ) -> SandboxViolation | None:
        """Check if an operation is allowed by the execution policy.

        Args:
            context: The current execution context.
            operation: The operation to check (e.g., "network_access",
                      "file_write", "subprocess_exec").

        Returns:
            A SandboxViolation if blocked, None if allowed.
        """
        policy = context.policy
        allowed = True

        op_lower = operation.lower()
        if (
            ("network" in op_lower and not policy.allow_network)
            or ("file_read" in op_lower and not policy.allow_file_read)
            or ("file_write" in op_lower and not policy.allow_file_write)
            or ("subprocess" in op_lower and not policy.allow_subprocess)
            or ("env" in op_lower and not policy.allow_env_read)
            or ("cross_origin" in op_lower and not policy.allow_cross_origin)
        ):
            allowed = False

        if allowed:
            return None

        violation = SandboxViolation(
            tool_name=context.tool_name,
            operation=operation,
            permission_required=operation,
            action_taken=self._default_action,
            details=f"Operation '{operation}' not permitted by manifest",
        )
        context.violations.append(violation)

        self._emit_violation_event(violation)

        return violation

    def check_resource(
        self,
        context: ExecutionContext,
        resource_uri: str,
    ) -> SandboxViolation | None:
        """Check if a resource access is allowed.

        Args:
            context: The current execution context.
            resource_uri: The resource URI being accessed.

        Returns:
            A SandboxViolation if blocked, None if allowed.
        """
        if context.policy.resource_policy.is_allowed(resource_uri):
            context.resources_accessed.append(resource_uri)
            return None

        violation = SandboxViolation(
            tool_name=context.tool_name,
            operation=f"resource_access:{resource_uri}",
            permission_required="resource_access",
            action_taken=self._default_action,
            details=f"Resource '{resource_uri}' not in allowed patterns",
        )
        context.violations.append(violation)

        self._emit_violation_event(violation)

        return violation

    def check_timeout(self, context: ExecutionContext) -> SandboxViolation | None:
        """Check if execution has exceeded its time limit.

        Returns:
            A SandboxViolation if timed out, None if within limits.
        """
        if not context.is_timed_out:
            return None

        violation = SandboxViolation(
            tool_name=context.tool_name,
            operation="timeout",
            permission_required="execution_time",
            action_taken=ViolationAction.BLOCK,
            details=(
                f"Execution exceeded {context.policy.timeout_policy.max_execution_seconds}s "
                f"limit (elapsed: {context.elapsed_seconds:.1f}s)"
            ),
        )
        context.violations.append(violation)

        self._emit_violation_event(violation)

        return violation

    def _emit_violation_event(self, violation: SandboxViolation) -> None:
        """Emit a security event for a violation."""
        if self._event_bus is None:
            return

        from fastmcp.server.security.alerts.models import (
            AlertSeverity,
            SecurityEvent,
            SecurityEventType,
        )

        self._event_bus.emit(
            SecurityEvent(
                event_type=SecurityEventType.POLICY_DENIED,
                severity=AlertSeverity.WARNING,
                layer="sandbox",
                message=f"Sandbox violation: {violation.tool_name} — {violation.operation}",
                resource_id=violation.tool_name,
                data=violation.to_dict(),
            )
        )


class SandboxedRunner:
    """Runs tools in a sandboxed execution context.

    Creates an execution context from a manifest, checks CRL for
    revocations, and provides pre/post execution hooks.

    Example::

        runner = SandboxedRunner(
            enforcer=ManifestEnforcer(),
            crl=my_crl,
        )

        # Start sandboxed execution
        context = runner.start(manifest, actor_id="user-1")

        # Check operations during execution
        runner.check(context, "network_access")
        runner.check_resource(context, "file:///data/input.csv")

        # Finish
        runner.finish(context)
    """

    def __init__(
        self,
        *,
        enforcer: ManifestEnforcer | None = None,
        crl: CertificateRevocationList | None = None,
    ) -> None:
        self._enforcer = enforcer or ManifestEnforcer()
        self._crl = crl
        self._active_contexts: dict[str, ExecutionContext] = {}
        self._completed_contexts: list[ExecutionContext] = []

    def start(
        self,
        manifest: SecurityManifest,
        *,
        actor_id: str = "",
    ) -> ExecutionContext:
        """Start a sandboxed execution.

        Creates an ExecutionContext from the manifest, checks the CRL,
        and returns the context for runtime enforcement.

        Args:
            manifest: The tool's security manifest.
            actor_id: Who is invoking the tool.

        Returns:
            ExecutionContext for tracking execution state.
        """
        policy = self._enforcer.policy_from_manifest(manifest)

        context = ExecutionContext(
            tool_name=manifest.tool_name,
            actor_id=actor_id,
            policy=policy,
        )

        # Check CRL
        if self._crl is not None and self._crl.is_revoked(manifest.tool_name):
            context.blocked = True
            context.block_reason = f"Tool '{manifest.tool_name}' has been revoked"
            context.finish()
            self._completed_contexts.append(context)
            return context

        self._active_contexts[context.context_id] = context
        return context

    def check(
        self,
        context: ExecutionContext,
        operation: str,
    ) -> SandboxViolation | None:
        """Check if an operation is allowed.

        Args:
            context: The execution context.
            operation: Operation to check.

        Returns:
            SandboxViolation if blocked, None if allowed.
        """
        return self._enforcer.check_permission(context, operation)

    def check_resource(
        self,
        context: ExecutionContext,
        resource_uri: str,
    ) -> SandboxViolation | None:
        """Check if a resource access is allowed.

        Args:
            context: The execution context.
            resource_uri: Resource being accessed.

        Returns:
            SandboxViolation if blocked, None if allowed.
        """
        return self._enforcer.check_resource(context, resource_uri)

    def finish(self, context: ExecutionContext) -> None:
        """Mark execution as finished.

        Args:
            context: The execution context to finish.
        """
        context.finish()
        self._active_contexts.pop(context.context_id, None)
        self._completed_contexts.append(context)

    @property
    def active_count(self) -> int:
        """Number of active executions."""
        return len(self._active_contexts)

    @property
    def completed_count(self) -> int:
        """Number of completed executions."""
        return len(self._completed_contexts)

    def get_active_contexts(self) -> list[ExecutionContext]:
        """Get all active execution contexts."""
        return list(self._active_contexts.values())

    def get_violations(self) -> list[SandboxViolation]:
        """Get all violations across all executions."""
        violations: list[SandboxViolation] = []
        for ctx in self._completed_contexts:
            violations.extend(ctx.violations)
        for ctx in self._active_contexts.values():
            violations.extend(ctx.violations)
        return violations
