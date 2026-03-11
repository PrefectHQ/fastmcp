"""Tests for Sandboxed Execution (Phase 17).

Covers manifest-to-policy conversion, permission checking,
resource access control, timeout enforcement, CRL blocking,
and the SandboxedRunner lifecycle.
"""

from __future__ import annotations

import time

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.alerts.handlers import BufferedHandler
from fastmcp.server.security.certification.manifest import (
    PermissionScope,
    ResourceAccessDeclaration,
    SecurityManifest,
)
from fastmcp.server.security.federation.crl import (
    CertificateRevocationList,
)
from fastmcp.server.security.sandbox.enforcer import (
    ExecutionContext,
    ManifestEnforcer,
    SandboxedRunner,
    SandboxViolation,
    ViolationAction,
)
from fastmcp.server.security.sandbox.policies import (
    ExecutionPolicy,
    ResourcePolicy,
    TimeoutPolicy,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _make_manifest(**kwargs) -> SecurityManifest:
    defaults = {
        "tool_name": "test-tool",
        "version": "1.0.0",
        "author": "acme",
    }
    defaults.update(kwargs)
    return SecurityManifest(**defaults)


# ── Resource Policy tests ───────────────────────────────────────────


class TestResourcePolicy:
    def test_allow_all_by_default(self):
        rp = ResourcePolicy()
        assert rp.is_allowed("file:///anything")

    def test_allowed_patterns(self):
        rp = ResourcePolicy(allowed_patterns=["file:///data/*"])
        assert rp.is_allowed("file:///data/input.csv")
        assert not rp.is_allowed("file:///secrets/key.pem")

    def test_blocked_patterns(self):
        rp = ResourcePolicy(blocked_patterns=["*.secret", "*.key"])
        assert rp.is_allowed("file:///data/input.csv")
        assert not rp.is_allowed("file:///data/db.secret")

    def test_blocked_takes_priority(self):
        rp = ResourcePolicy(
            allowed_patterns=["file:///data/*"],
            blocked_patterns=["file:///data/secret*"],
        )
        assert rp.is_allowed("file:///data/input.csv")
        assert not rp.is_allowed("file:///data/secret.key")


# ── Timeout Policy tests ───────────────────────────────────────────


class TestTimeoutPolicy:
    def test_defaults(self):
        tp = TimeoutPolicy()
        assert tp.max_execution_seconds == 30.0
        assert tp.warn_at_percent == 80.0

    def test_warn_at_seconds(self):
        tp = TimeoutPolicy(max_execution_seconds=100.0, warn_at_percent=75.0)
        assert tp.warn_at_seconds == 75.0


# ── Execution Policy tests ──────────────────────────────────────────


class TestExecutionPolicy:
    def test_defaults_deny_all(self):
        ep = ExecutionPolicy()
        assert not ep.allow_network
        assert not ep.allow_file_read
        assert not ep.allow_file_write
        assert not ep.allow_subprocess
        assert not ep.allow_env_read
        assert not ep.allow_cross_origin

    def test_to_dict(self):
        ep = ExecutionPolicy(allow_network=True)
        d = ep.to_dict()
        assert d["allow_network"] is True
        assert d["allow_file_write"] is False


# ── Manifest Enforcer tests ────────────────────────────────────────


class TestManifestEnforcer:
    def test_policy_from_minimal_manifest(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest()
        policy = enforcer.policy_from_manifest(manifest)
        # No permissions declared → everything denied
        assert not policy.allow_network
        assert not policy.allow_file_read
        assert not policy.allow_file_write

    def test_policy_from_network_manifest(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest(
            permissions={PermissionScope.NETWORK_ACCESS, PermissionScope.READ_RESOURCE}
        )
        policy = enforcer.policy_from_manifest(manifest)
        assert policy.allow_network
        assert policy.allow_file_read
        assert not policy.allow_file_write
        assert not policy.allow_subprocess

    def test_policy_from_full_permissions(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest(
            permissions={
                PermissionScope.NETWORK_ACCESS,
                PermissionScope.FILE_SYSTEM_READ,
                PermissionScope.FILE_SYSTEM_WRITE,
                PermissionScope.SUBPROCESS_EXEC,
                PermissionScope.ENVIRONMENT_READ,
                PermissionScope.CROSS_ORIGIN,
            }
        )
        policy = enforcer.policy_from_manifest(manifest)
        assert policy.allow_network
        assert policy.allow_file_read
        assert policy.allow_file_write
        assert policy.allow_subprocess
        assert policy.allow_env_read
        assert policy.allow_cross_origin

    def test_policy_timeout_from_manifest(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest(max_execution_time_seconds=60)
        policy = enforcer.policy_from_manifest(manifest)
        assert policy.timeout_policy.max_execution_seconds == 60

    def test_policy_resource_patterns(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest(
            resource_access=[
                ResourceAccessDeclaration(resource_pattern="file:///data/*"),
                ResourceAccessDeclaration(resource_pattern="http://api.example.com/*"),
            ]
        )
        policy = enforcer.policy_from_manifest(manifest)
        assert len(policy.resource_policy.allowed_patterns) == 2

    def test_policy_consent_required(self):
        enforcer = ManifestEnforcer()
        manifest = _make_manifest(requires_consent=True)
        policy = enforcer.policy_from_manifest(manifest)
        assert policy.require_consent


# ── Permission check tests ──────────────────────────────────────────


class TestPermissionChecks:
    def test_allowed_operation(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(allow_network=True)
        ctx = ExecutionContext(tool_name="test", policy=policy)
        violation = enforcer.check_permission(ctx, "network_access")
        assert violation is None

    def test_blocked_operation(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(allow_network=False)
        ctx = ExecutionContext(tool_name="test", policy=policy)
        violation = enforcer.check_permission(ctx, "network_access")
        assert violation is not None
        assert violation.action_taken == ViolationAction.BLOCK
        assert ctx.violation_count == 1

    def test_blocked_file_write(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(allow_file_read=True, allow_file_write=False)
        ctx = ExecutionContext(tool_name="test", policy=policy)
        assert enforcer.check_permission(ctx, "file_read") is None
        assert enforcer.check_permission(ctx, "file_write") is not None

    def test_blocked_subprocess(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy()
        ctx = ExecutionContext(tool_name="test", policy=policy)
        v = enforcer.check_permission(ctx, "subprocess_exec")
        assert v is not None

    def test_multiple_violations_tracked(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy()
        ctx = ExecutionContext(tool_name="test", policy=policy)
        enforcer.check_permission(ctx, "network_access")
        enforcer.check_permission(ctx, "file_write")
        enforcer.check_permission(ctx, "subprocess_exec")
        assert ctx.violation_count == 3


# ── Resource check tests ────────────────────────────────────────────


class TestResourceChecks:
    def test_allowed_resource(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(
            resource_policy=ResourcePolicy(allowed_patterns=["file:///data/*"])
        )
        ctx = ExecutionContext(tool_name="test", policy=policy)
        v = enforcer.check_resource(ctx, "file:///data/input.csv")
        assert v is None
        assert "file:///data/input.csv" in ctx.resources_accessed

    def test_blocked_resource(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(
            resource_policy=ResourcePolicy(allowed_patterns=["file:///data/*"])
        )
        ctx = ExecutionContext(tool_name="test", policy=policy)
        v = enforcer.check_resource(ctx, "file:///secrets/key.pem")
        assert v is not None
        assert ctx.violation_count == 1


# ── Timeout check tests ─────────────────────────────────────────────


class TestTimeoutChecks:
    def test_within_time(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(
            timeout_policy=TimeoutPolicy(max_execution_seconds=30.0)
        )
        ctx = ExecutionContext(tool_name="test", policy=policy)
        v = enforcer.check_timeout(ctx)
        assert v is None

    def test_timeout_exceeded(self):
        enforcer = ManifestEnforcer()
        policy = ExecutionPolicy(
            timeout_policy=TimeoutPolicy(max_execution_seconds=0.001)
        )
        ctx = ExecutionContext(tool_name="test", policy=policy)
        time.sleep(0.01)  # Exceed timeout
        v = enforcer.check_timeout(ctx)
        assert v is not None
        assert "exceeded" in v.details


# ── Execution Context tests ──────────────────────────────────────────


class TestExecutionContext:
    def test_default_context(self):
        ctx = ExecutionContext()
        assert ctx.context_id
        assert not ctx.blocked
        assert ctx.violation_count == 0

    def test_elapsed_time(self):
        ctx = ExecutionContext()
        time.sleep(0.01)
        assert ctx.elapsed_seconds > 0

    def test_finish(self):
        ctx = ExecutionContext()
        ctx.finish()
        assert ctx.ended_at is not None

    def test_to_dict(self):
        ctx = ExecutionContext(tool_name="test", actor_id="user-1")
        d = ctx.to_dict()
        assert d["tool_name"] == "test"
        assert d["actor_id"] == "user-1"


# ── SandboxedRunner tests ───────────────────────────────────────────


class TestSandboxedRunner:
    def test_start_creates_context(self):
        runner = SandboxedRunner()
        manifest = _make_manifest()
        ctx = runner.start(manifest, actor_id="user-1")
        assert ctx.tool_name == "test-tool"
        assert ctx.actor_id == "user-1"
        assert not ctx.blocked
        assert runner.active_count == 1

    def test_start_blocked_by_crl(self):
        crl = CertificateRevocationList()
        crl.revoke("test-tool")
        runner = SandboxedRunner(crl=crl)
        ctx = runner.start(_make_manifest(), actor_id="user-1")
        assert ctx.blocked
        assert "revoked" in ctx.block_reason
        assert runner.active_count == 0
        assert runner.completed_count == 1

    def test_check_operation(self):
        runner = SandboxedRunner()
        manifest = _make_manifest(permissions={PermissionScope.NETWORK_ACCESS})
        ctx = runner.start(manifest)
        assert runner.check(ctx, "network_access") is None
        assert runner.check(ctx, "file_write") is not None

    def test_check_resource(self):
        runner = SandboxedRunner()
        manifest = _make_manifest(
            resource_access=[
                ResourceAccessDeclaration(resource_pattern="file:///data/*")
            ]
        )
        ctx = runner.start(manifest)
        assert runner.check_resource(ctx, "file:///data/in.csv") is None
        assert runner.check_resource(ctx, "file:///etc/passwd") is not None

    def test_finish(self):
        runner = SandboxedRunner()
        ctx = runner.start(_make_manifest())
        runner.finish(ctx)
        assert runner.active_count == 0
        assert runner.completed_count == 1

    def test_get_violations(self):
        runner = SandboxedRunner()
        manifest = _make_manifest()
        ctx = runner.start(manifest)
        runner.check(ctx, "network_access")
        runner.check(ctx, "file_write")
        runner.finish(ctx)
        violations = runner.get_violations()
        assert len(violations) == 2

    def test_get_active_contexts(self):
        runner = SandboxedRunner()
        runner.start(_make_manifest(tool_name="a"))
        runner.start(_make_manifest(tool_name="b"))
        assert len(runner.get_active_contexts()) == 2


# ── Event bus integration ────────────────────────────────────────────


class TestEventBusIntegration:
    def test_violation_emits_event(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=50)
        bus.subscribe(handler)
        enforcer = ManifestEnforcer(event_bus=bus)
        policy = ExecutionPolicy()
        ctx = ExecutionContext(tool_name="test", policy=policy)
        enforcer.check_permission(ctx, "network_access")
        assert len(handler.events) == 1
        assert handler.events[0].layer == "sandbox"


# ── Violation model tests ───────────────────────────────────────────


class TestSandboxViolation:
    def test_default_violation(self):
        v = SandboxViolation()
        assert v.violation_id
        assert v.action_taken == ViolationAction.BLOCK

    def test_to_dict(self):
        v = SandboxViolation(
            tool_name="test",
            operation="network_access",
            action_taken=ViolationAction.BLOCK_AND_ALERT,
        )
        d = v.to_dict()
        assert d["tool_name"] == "test"
        assert d["action_taken"] == "block_and_alert"


# ── Import tests ────────────────────────────────────────────────────


class TestImports:
    def test_sandbox_imports(self):
        from fastmcp.server.security.sandbox import (
            SandboxedRunner,
        )

        assert SandboxedRunner is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            SandboxedRunner,
        )

        assert SandboxedRunner is not None
