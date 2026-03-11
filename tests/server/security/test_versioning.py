"""Tests for policy versioning (models, manager, persistence)."""

import pytest

from fastmcp.server.security.policy.versioning.manager import PolicyVersionManager
from fastmcp.server.security.policy.versioning.models import (
    PolicyVersion,
    PolicyVersionHistory,
    policy_version_from_dict,
    policy_version_to_dict,
)
from fastmcp.server.security.storage.memory import MemoryBackend
from fastmcp.server.security.storage.sqlite import SQLiteBackend

# ── PolicyVersion Tests ────────────────────────────────────────


class TestPolicyVersion:
    def test_create_default(self):
        v = PolicyVersion()
        assert v.version_number == 0
        assert v.policy_data == {}
        assert v.author == ""
        assert v.version_id  # should be a UUID string

    def test_create_with_data(self):
        v = PolicyVersion(
            policy_set_id="prod",
            version_number=1,
            policy_data={"roles": {"admin": ["*"]}},
            author="security-team",
            description="Initial setup",
            tags=frozenset({"production", "rbac"}),
        )
        assert v.policy_set_id == "prod"
        assert v.version_number == 1
        assert v.policy_data["roles"]["admin"] == ["*"]
        assert "production" in v.tags

    def test_frozen(self):
        v = PolicyVersion()
        with pytest.raises(AttributeError):
            v.version_number = 5


class TestPolicyVersionSerialization:
    def test_round_trip(self):
        v = PolicyVersion(
            policy_set_id="test",
            version_number=3,
            policy_data={"key": "value"},
            author="me",
            description="test version",
            tags=frozenset({"a", "b"}),
        )
        data = policy_version_to_dict(v)
        restored = policy_version_from_dict(data)
        assert restored.version_id == v.version_id
        assert restored.policy_set_id == v.policy_set_id
        assert restored.version_number == v.version_number
        assert restored.policy_data == v.policy_data
        assert restored.author == v.author
        assert restored.description == v.description
        assert restored.tags == v.tags


# ── PolicyVersionHistory Tests ─────────────────────────────────


class TestPolicyVersionHistory:
    def test_empty_history(self):
        h = PolicyVersionHistory(policy_set_id="test")
        assert h.current_version is None
        assert len(h.versions) == 0

    def test_add_version(self):
        h = PolicyVersionHistory(policy_set_id="test")
        v = h.add_version(
            policy_data={"roles": {"admin": ["*"]}},
            author="me",
            description="v1",
        )
        assert v.version_number == 1
        assert h.current_version == v
        assert h.current_version_index == 0

    def test_add_multiple_versions(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"v": 1})
        v2 = h.add_version(policy_data={"v": 2})
        assert len(h.versions) == 2
        assert h.current_version == v2
        assert h.current_version_index == 1

    def test_rollback(self):
        h = PolicyVersionHistory(policy_set_id="test")
        v1 = h.add_version(policy_data={"v": 1})
        h.add_version(policy_data={"v": 2})
        rolled = h.rollback(version_number=1)
        assert rolled == v1
        assert h.current_version == v1

    def test_rollback_invalid_version(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"v": 1})
        with pytest.raises(ValueError, match="Invalid version number"):
            h.rollback(version_number=5)

    def test_rollback_zero_raises(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"v": 1})
        with pytest.raises(ValueError):
            h.rollback(version_number=0)

    def test_diff_added(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"a": 1})
        h.add_version(policy_data={"a": 1, "b": 2})
        d = h.diff(1, 2)
        assert "b" in d["added"]
        assert d["added"]["b"] == 2
        assert len(d["removed"]) == 0
        assert len(d["changed"]) == 0

    def test_diff_removed(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"a": 1, "b": 2})
        h.add_version(policy_data={"a": 1})
        d = h.diff(1, 2)
        assert "b" in d["removed"]

    def test_diff_changed(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"a": 1})
        h.add_version(policy_data={"a": 2})
        d = h.diff(1, 2)
        assert "a" in d["changed"]
        assert d["changed"]["a"] == {"from": 1, "to": 2}

    def test_diff_invalid_version(self):
        h = PolicyVersionHistory(policy_set_id="test")
        h.add_version(policy_data={"v": 1})
        with pytest.raises(ValueError):
            h.diff(1, 5)


# ── PolicyVersionManager + MemoryBackend Tests ─────────────────


class TestPolicyVersionManagerMemory:
    def test_create_version(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="test", backend=backend)
        v = mgr.create_version(
            policy_data={"roles": {"admin": ["*"]}},
            author="me",
        )
        assert v.version_number == 1
        assert mgr.version_count == 1

    def test_current_version(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="test", backend=backend)
        mgr.create_version(policy_data={"v": 1})
        v2 = mgr.create_version(policy_data={"v": 2})
        assert mgr.current_version == v2

    def test_rollback(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="test", backend=backend)
        v1 = mgr.create_version(policy_data={"v": 1})
        mgr.create_version(policy_data={"v": 2})
        rolled = mgr.rollback_to(1)
        assert rolled.version_number == v1.version_number
        assert mgr.current_version.policy_data == {"v": 1}

    def test_list_versions(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="test", backend=backend)
        mgr.create_version(policy_data={"v": 1})
        mgr.create_version(policy_data={"v": 2})
        mgr.create_version(policy_data={"v": 3})
        assert len(mgr.list_versions()) == 3

    def test_diff(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="test", backend=backend)
        mgr.create_version(policy_data={"a": 1})
        mgr.create_version(policy_data={"a": 2, "b": 3})
        d = mgr.diff(1, 2)
        assert "a" in d["changed"]
        assert "b" in d["added"]

    def test_persistence_across_managers(self):
        backend = MemoryBackend()
        mgr1 = PolicyVersionManager(policy_set_id="test", backend=backend)
        mgr1.create_version(policy_data={"v": 1}, author="alice")
        mgr1.create_version(policy_data={"v": 2}, author="bob")

        # Create new manager with same backend
        mgr2 = PolicyVersionManager(policy_set_id="test", backend=backend)
        assert mgr2.version_count == 2
        assert mgr2.current_version.policy_data == {"v": 2}

    def test_empty_on_new_set(self):
        backend = MemoryBackend()
        mgr = PolicyVersionManager(policy_set_id="brand-new", backend=backend)
        assert mgr.current_version is None
        assert mgr.version_count == 0


# ── PolicyVersionManager + SQLiteBackend Tests ─────────────────


class TestPolicyVersionManagerSQLite:
    def test_persistence_across_connections(self, tmp_path):
        db = str(tmp_path / "test.db")

        b1 = SQLiteBackend(db)
        mgr1 = PolicyVersionManager(policy_set_id="prod", backend=b1)
        mgr1.create_version(
            policy_data={"roles": {"admin": ["*"]}},
            author="security-team",
            description="Initial RBAC",
        )
        mgr1.create_version(
            policy_data={"roles": {"admin": ["*"], "viewer": ["read"]}},
            author="security-team",
            description="Added viewer role",
        )
        b1.close()

        b2 = SQLiteBackend(db)
        mgr2 = PolicyVersionManager(policy_set_id="prod", backend=b2)
        assert mgr2.version_count == 2
        assert mgr2.current_version.description == "Added viewer role"
        assert mgr2.current_version.policy_data["roles"]["viewer"] == ["read"]
        b2.close()

    def test_rollback_persists(self, tmp_path):
        db = str(tmp_path / "test.db")

        b1 = SQLiteBackend(db)
        mgr1 = PolicyVersionManager(policy_set_id="prod", backend=b1)
        mgr1.create_version(policy_data={"v": 1})
        mgr1.create_version(policy_data={"v": 2})
        mgr1.rollback_to(1)
        b1.close()

        b2 = SQLiteBackend(db)
        mgr2 = PolicyVersionManager(policy_set_id="prod", backend=b2)
        assert mgr2.current_version.policy_data == {"v": 1}
        b2.close()

    def test_multiple_policy_sets(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend = SQLiteBackend(db)

        mgr_a = PolicyVersionManager(policy_set_id="set-a", backend=backend)
        mgr_b = PolicyVersionManager(policy_set_id="set-b", backend=backend)

        mgr_a.create_version(policy_data={"set": "a"})
        mgr_b.create_version(policy_data={"set": "b"})

        assert mgr_a.current_version.policy_data == {"set": "a"}
        assert mgr_b.current_version.policy_data == {"set": "b"}
        backend.close()
