"""Tests for declarative YAML/JSON policy loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmcp.server.security.policy.declarative import (
    dump_policy_schema,
    load_policy,
)
from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
)


def _ctx(
    resource_id: str = "test-tool",
    action: str = "call_tool",
    actor_id: str = "test-actor",
    metadata: dict | None = None,
    tags: frozenset[str] | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id=actor_id,
        action=action,
        resource_id=resource_id,
        metadata=metadata or {},
        tags=tags or frozenset(),
    )


# ── Loading from dict ──────────────────────────────────────────────


class TestLoadFromDict:
    @pytest.mark.anyio
    async def test_simple_allowlist(self):
        policy = load_policy({"type": "allowlist", "allowed": ["tool-a", "tool-b"]})
        result = await policy.evaluate(_ctx(resource_id="tool-a"))
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(_ctx(resource_id="tool-c"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_simple_denylist(self):
        policy = load_policy({"type": "denylist", "denied": ["bad-*"]})
        result = await policy.evaluate(_ctx(resource_id="bad-tool"))
        assert result.decision == PolicyDecision.DENY

        result = await policy.evaluate(_ctx(resource_id="good-tool"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_rbac(self):
        policy = load_policy(
            {
                "type": "rbac",
                "role_mappings": {
                    "admin": ["*"],
                    "viewer": ["call_tool"],
                },
            }
        )
        # Admin can do anything
        result = await policy.evaluate(
            _ctx(action="delete_tool", metadata={"role": "admin"})
        )
        assert result.decision == PolicyDecision.ALLOW

        # Viewer can only call_tool
        result = await policy.evaluate(
            _ctx(action="call_tool", metadata={"role": "viewer"})
        )
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(
            _ctx(action="delete_tool", metadata={"role": "viewer"})
        )
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_rate_limit(self):
        policy = load_policy(
            {
                "type": "rate_limit",
                "max_requests": 2,
                "window_seconds": 60,
            }
        )
        ctx = _ctx()
        assert (await policy.evaluate(ctx)).decision == PolicyDecision.ALLOW
        assert (await policy.evaluate(ctx)).decision == PolicyDecision.ALLOW
        assert (await policy.evaluate(ctx)).decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_time_based(self):
        policy = load_policy(
            {
                "type": "time_based",
                "allowed_days": [0, 1, 2, 3, 4],
                "start_hour": 9,
                "end_hour": 17,
            }
        )
        # Just verify it builds and evaluates without error
        result = await policy.evaluate(_ctx())
        assert result.decision in (PolicyDecision.ALLOW, PolicyDecision.DENY)

    @pytest.mark.anyio
    async def test_abac_metadata_conditions(self):
        policy = load_policy(
            {
                "type": "abac",
                "metadata_conditions": {"department": "engineering"},
            }
        )
        result = await policy.evaluate(_ctx(metadata={"department": "engineering"}))
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(_ctx(metadata={"department": "sales"}))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_allow_all(self):
        policy = load_policy({"type": "allow_all"})
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_deny_all(self):
        policy = load_policy({"type": "deny_all"})
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_resource_scoped(self):
        policy = load_policy(
            {
                "type": "resource_scoped",
                "resource_rules": {
                    "admin-panel": {"type": "deny_all"},
                    "public-api": {"type": "allow_all"},
                },
                "default": {"type": "deny_all"},
            }
        )
        result = await policy.evaluate(_ctx(resource_id="admin-panel"))
        assert result.decision == PolicyDecision.DENY

        result = await policy.evaluate(_ctx(resource_id="public-api"))
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(_ctx(resource_id="unknown"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_resource_scoped_prefix_match(self):
        policy = load_policy(
            {
                "type": "resource_scoped",
                "prefix_match": True,
                "resource_rules": {
                    "admin": {"type": "deny_all"},
                },
            }
        )
        result = await policy.evaluate(_ctx(resource_id="admin-panel"))
        assert result.decision == PolicyDecision.DENY

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown policy type"):
            load_policy({"type": "nonexistent"})

    def test_type_aliases(self):
        # role_based is alias for rbac
        policy = load_policy(
            {
                "type": "role_based",
                "role_mappings": {"admin": ["*"]},
            }
        )
        assert policy is not None

        # temporal is alias for time_based
        policy = load_policy({"type": "temporal"})
        assert policy is not None

        # attribute_based is alias for abac
        policy = load_policy({"type": "attribute_based"})
        assert policy is not None


# ── Composition ────────────────────────────────────────────────────


class TestComposition:
    @pytest.mark.anyio
    async def test_all_of(self):
        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [
                    {"type": "allowlist", "allowed": ["tool-a"]},
                    {"type": "denylist", "denied": ["tool-b"]},
                ],
            }
        )
        # tool-a: in allowlist, not in denylist → ALLOW
        result = await policy.evaluate(_ctx(resource_id="tool-a"))
        assert result.decision == PolicyDecision.ALLOW

        # tool-b: not in allowlist → DENY (from allowlist)
        result = await policy.evaluate(_ctx(resource_id="tool-b"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_any_of(self):
        policy = load_policy(
            {
                "composition": "any_of",
                "policies": [
                    {"type": "allowlist", "allowed": ["tool-a"]},
                    {"type": "allowlist", "allowed": ["tool-b"]},
                ],
            }
        )
        result = await policy.evaluate(_ctx(resource_id="tool-a"))
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(_ctx(resource_id="tool-b"))
        assert result.decision == PolicyDecision.ALLOW

        result = await policy.evaluate(_ctx(resource_id="tool-c"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_any_of_with_require_minimum(self):
        policy = load_policy(
            {
                "composition": "any_of",
                "require_minimum": 2,
                "policies": [
                    {"type": "allowlist", "allowed": ["tool-a", "tool-b"]},
                    {"type": "allowlist", "allowed": ["tool-b", "tool-c"]},
                    {"type": "allowlist", "allowed": ["tool-c", "tool-a"]},
                ],
            }
        )
        # tool-a: allowed by policy 1 and 3 → 2 allows → meets minimum
        result = await policy.evaluate(_ctx(resource_id="tool-a"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_first_match(self):
        policy = load_policy(
            {
                "composition": "first_match",
                "default_decision": "deny",
                "policies": [
                    {"type": "denylist", "denied": ["blocked"]},
                    {"type": "allow_all"},
                ],
            }
        )
        # "blocked" → denied by first policy
        result = await policy.evaluate(_ctx(resource_id="blocked"))
        assert result.decision == PolicyDecision.DENY

        # other → allowed by denylist (not denied), so it returns ALLOW
        result = await policy.evaluate(_ctx(resource_id="other"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_not_composition(self):
        policy = load_policy(
            {
                "composition": "not",
                "policies": [
                    {"type": "denylist", "denied": ["blocked"]},
                ],
            }
        )
        # "blocked" → denied → inverted to ALLOW
        result = await policy.evaluate(_ctx(resource_id="blocked"))
        assert result.decision == PolicyDecision.ALLOW

        # "other" → allowed → inverted to DENY
        result = await policy.evaluate(_ctx(resource_id="other"))
        assert result.decision == PolicyDecision.DENY

    def test_not_requires_one_child(self):
        with pytest.raises(ValueError, match="exactly one child"):
            load_policy(
                {
                    "composition": "not",
                    "policies": [
                        {"type": "allow_all"},
                        {"type": "deny_all"},
                    ],
                }
            )

    def test_unknown_composition_raises(self):
        with pytest.raises(ValueError, match="Unknown composition"):
            load_policy(
                {
                    "composition": "invalid_comp",
                    "policies": [{"type": "allow_all"}],
                }
            )

    @pytest.mark.anyio
    async def test_nested_composition(self):
        """Compositions can nest arbitrarily."""
        policy = load_policy(
            {
                "composition": "all_of",
                "policy_id": "outer",
                "policies": [
                    {"type": "denylist", "denied": ["admin-*"]},
                    {
                        "composition": "any_of",
                        "policy_id": "inner",
                        "policies": [
                            {"type": "allowlist", "allowed": ["tool-a"]},
                            {"type": "allowlist", "allowed": ["tool-b"]},
                        ],
                    },
                ],
            }
        )
        # tool-a: not denied + in inner any_of → ALLOW
        result = await policy.evaluate(_ctx(resource_id="tool-a"))
        assert result.decision == PolicyDecision.ALLOW

        # admin-tool: denied by outer denylist → DENY
        result = await policy.evaluate(_ctx(resource_id="admin-tool"))
        assert result.decision == PolicyDecision.DENY

        # tool-c: not denied but not in either inner allowlist → DENY
        result = await policy.evaluate(_ctx(resource_id="tool-c"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_composition_aliases(self):
        """All composition aliases should work."""
        for alias in ("all_of", "allof", "all"):
            policy = load_policy(
                {
                    "composition": alias,
                    "policies": [{"type": "allow_all"}],
                }
            )
            result = await policy.evaluate(_ctx())
            assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_custom_policy_id_and_version(self):
        policy = load_policy(
            {
                "composition": "all_of",
                "policy_id": "my-custom-id",
                "version": "3.0",
                "policies": [{"type": "allow_all"}],
            }
        )
        assert await policy.get_policy_id() == "my-custom-id"
        assert await policy.get_policy_version() == "3.0"


# ── File loading ───────────────────────────────────────────────────


class TestFileLoading:
    def test_load_json_file(self, tmp_path: Path):
        config = {
            "type": "allowlist",
            "allowed": ["tool-a"],
        }
        json_file = tmp_path / "policy.json"
        json_file.write_text(json.dumps(config))

        policy = load_policy(json_file)
        assert policy is not None

    @pytest.mark.anyio
    async def test_load_json_file_evaluates(self, tmp_path: Path):
        config = {
            "type": "allowlist",
            "allowed": ["safe-*"],
        }
        json_file = tmp_path / "policy.json"
        json_file.write_text(json.dumps(config))

        policy = load_policy(json_file)
        result = await policy.evaluate(_ctx(resource_id="safe-tool"))
        assert result.decision == PolicyDecision.ALLOW

    def test_load_json_composite(self, tmp_path: Path):
        config = {
            "composition": "all_of",
            "policies": [
                {"type": "allowlist", "allowed": ["*"]},
                {"type": "denylist", "denied": ["admin-*"]},
            ],
        }
        json_file = tmp_path / "composite.json"
        json_file.write_text(json.dumps(config))

        policy = load_policy(json_file)
        assert policy is not None

    def test_load_from_string_path(self, tmp_path: Path):
        config = {"type": "allow_all"}
        json_file = tmp_path / "policy.json"
        json_file.write_text(json.dumps(config))

        # Pass string instead of Path
        policy = load_policy(str(json_file))
        assert policy is not None

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_policy("/nonexistent/path/policy.json")

    def test_unsupported_extension(self, tmp_path: Path):
        xml_file = tmp_path / "policy.xml"
        xml_file.write_text("<policy/>")

        with pytest.raises(ValueError, match="Unsupported file format"):
            load_policy(xml_file)

    def test_non_dict_content_raises(self, tmp_path: Path):
        json_file = tmp_path / "bad.json"
        json_file.write_text(json.dumps(["a", "b"]))

        with pytest.raises(ValueError, match="must contain a mapping"):
            load_policy(json_file)

    def test_yaml_import_error_when_not_installed(self, tmp_path: Path, monkeypatch):
        """Test that helpful error is raised if PyYAML not installed."""
        yaml_file = tmp_path / "policy.yaml"
        yaml_file.write_text("type: allow_all\n")

        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(ImportError, match="PyYAML"):
            load_policy(yaml_file)


# ── YAML loading (optional — only if pyyaml available) ─────────────


class TestYAMLLoading:
    @pytest.fixture(autouse=True)
    def _skip_without_yaml(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

    def test_load_yaml_file(self, tmp_path: Path):
        import yaml

        config = {"type": "allowlist", "allowed": ["tool-a"]}
        yaml_file = tmp_path / "policy.yaml"
        yaml_file.write_text(yaml.dump(config))

        policy = load_policy(yaml_file)
        assert policy is not None

    def test_load_yml_extension(self, tmp_path: Path):
        import yaml

        config = {"type": "allow_all"}
        yml_file = tmp_path / "policy.yml"
        yml_file.write_text(yaml.dump(config))

        policy = load_policy(yml_file)
        assert policy is not None

    @pytest.mark.anyio
    async def test_full_yaml_composite(self, tmp_path: Path):
        yaml_content = """\
policy_id: production-policy
version: "2.0"
composition: all_of
policies:
  - type: allowlist
    allowed:
      - "weather-*"
      - "translate"
  - type: denylist
    denied:
      - "admin-*"
      - "debug-*"
  - type: rate_limit
    max_requests: 100
    window_seconds: 3600
"""
        yaml_file = tmp_path / "production.yaml"
        yaml_file.write_text(yaml_content)

        policy = load_policy(yaml_file)
        assert await policy.get_policy_id() == "production-policy"
        assert await policy.get_policy_version() == "2.0"

        # weather-lookup: allowed, not denied
        result = await policy.evaluate(_ctx(resource_id="weather-lookup"))
        assert result.decision == PolicyDecision.ALLOW

        # admin-panel: denied by denylist
        result = await policy.evaluate(_ctx(resource_id="admin-panel"))
        assert result.decision == PolicyDecision.DENY

        # translate: allowed, not denied
        result = await policy.evaluate(_ctx(resource_id="translate"))
        assert result.decision == PolicyDecision.ALLOW

        # random-tool: not in allowlist → DENY
        result = await policy.evaluate(_ctx(resource_id="random-tool"))
        assert result.decision == PolicyDecision.DENY


# ── Integration with PolicyEngine ──────────────────────────────────


class TestEngineIntegration:
    @pytest.mark.anyio
    async def test_loaded_policy_in_engine(self):
        from fastmcp.server.security.policy.engine import PolicyEngine

        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [
                    {"type": "allowlist", "allowed": ["safe-*"]},
                    {"type": "denylist", "denied": ["safe-but-blocked"]},
                ],
            }
        )

        engine = PolicyEngine(providers=[policy])
        result = await engine.evaluate(_ctx(resource_id="safe-tool"))
        assert result.decision == PolicyDecision.ALLOW

        result = await engine.evaluate(_ctx(resource_id="safe-but-blocked"))
        assert result.decision == PolicyDecision.DENY

        result = await engine.evaluate(_ctx(resource_id="unknown"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_multiple_loaded_policies_in_engine(self):
        from fastmcp.server.security.policy.engine import PolicyEngine

        p1 = load_policy({"type": "denylist", "denied": ["blocked"]})
        p2 = load_policy({"type": "allowlist", "allowed": ["*"]})

        engine = PolicyEngine(providers=[p1, p2])
        result = await engine.evaluate(_ctx(resource_id="blocked"))
        assert result.decision == PolicyDecision.DENY

        result = await engine.evaluate(_ctx(resource_id="anything-else"))
        assert result.decision == PolicyDecision.ALLOW


# ── Schema export ──────────────────────────────────────────────────


class TestSchema:
    def test_dump_schema_returns_dict(self):
        schema = dump_policy_schema()
        assert isinstance(schema, dict)
        assert "policy_types" in schema
        assert "compositions" in schema
        assert "common_fields" in schema

    def test_schema_includes_all_types(self):
        schema = dump_policy_schema()
        types = schema["policy_types"]
        expected = {
            "allowlist",
            "denylist",
            "rbac",
            "rate_limit",
            "time_based",
            "abac",
            "resource_scoped",
            "allow_all",
            "deny_all",
        }
        assert set(types.keys()) == expected

    def test_schema_includes_compositions(self):
        schema = dump_policy_schema()
        comps = schema["compositions"]
        assert set(comps.keys()) == {"all_of", "any_of", "first_match", "not"}

    def test_schema_is_json_serializable(self):
        schema = dump_policy_schema()
        # Should not raise
        serialized = json.dumps(schema)
        assert len(serialized) > 0


# ── Edge cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.anyio
    async def test_empty_policies_list_in_all_of(self):
        """AllOf with no children should still work."""
        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [],
            }
        )
        result = await policy.evaluate(_ctx())
        # AllOf with no children → depends on implementation
        assert result.decision in (
            PolicyDecision.ALLOW,
            PolicyDecision.DENY,
            PolicyDecision.DEFER,
        )

    @pytest.mark.anyio
    async def test_single_policy_in_all_of(self):
        """AllOf with single child."""
        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [{"type": "allow_all"}],
            }
        )
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_custom_policy_id_on_leaf(self):
        policy = load_policy(
            {
                "type": "allowlist",
                "allowed": ["tool-a"],
                "policy_id": "my-leaf-policy",
                "version": "5.0",
            }
        )
        assert await policy.get_policy_id() == "my-leaf-policy"
        assert await policy.get_policy_version() == "5.0"

    @pytest.mark.anyio
    async def test_abac_require_all_false(self):
        """ABAC with OR logic."""
        policy = load_policy(
            {
                "type": "abac",
                "require_all": False,
                "metadata_conditions": {
                    "department": "engineering",
                    "clearance": "top-secret",
                },
            }
        )
        # Only one condition met → still passes with require_all=False
        result = await policy.evaluate(_ctx(metadata={"department": "engineering"}))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_rbac_default_decision_allow(self):
        policy = load_policy(
            {
                "type": "rbac",
                "role_mappings": {"admin": ["*"]},
                "default_decision": "allow",
            }
        )
        # No role in metadata → default_decision = allow
        result = await policy.evaluate(_ctx(metadata={}))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_deeply_nested_composition(self):
        """Three levels of nesting."""
        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [
                    {
                        "composition": "any_of",
                        "policies": [
                            {
                                "composition": "not",
                                "policies": [
                                    {"type": "denylist", "denied": ["blocked"]},
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        # blocked → denied → not → ALLOW → any_of ALLOW → all_of ALLOW
        result = await policy.evaluate(_ctx(resource_id="blocked"))
        assert result.decision == PolicyDecision.ALLOW
