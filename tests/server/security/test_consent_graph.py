"""Tests for the ConsentGraph engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.consent.models import (
    ConsentCondition,
    ConsentNode,
    ConsentQuery,
    ConsentStatus,
    NodeType,
)


class TestConsentGraphNodes:
    def test_add_node(self):
        g = ConsentGraph()
        g.add_node(ConsentNode("a1", NodeType.AGENT, "Agent 1"))
        assert g.node_count == 1
        assert g.get_node("a1") is not None

    def test_get_node_not_found(self):
        g = ConsentGraph()
        assert g.get_node("nonexistent") is None

    def test_remove_node(self):
        g = ConsentGraph()
        g.add_node(ConsentNode("a1", NodeType.AGENT))
        assert g.remove_node("a1")
        assert g.node_count == 0

    def test_remove_node_not_found(self):
        g = ConsentGraph()
        assert not g.remove_node("nonexistent")

    def test_remove_node_removes_edges(self):
        g = ConsentGraph()
        g.add_node(ConsentNode("a1", NodeType.AGENT))
        g.add_node(ConsentNode("a2", NodeType.AGENT))
        g.grant("a1", "a2", {"read"})
        assert g.edge_count == 1
        g.remove_node("a1")
        assert g.edge_count == 0


class TestConsentGraphGrant:
    def test_grant_creates_edge(self):
        g = ConsentGraph()
        edge = g.grant("owner", "agent", {"read", "execute"})
        assert edge.source_id == "owner"
        assert edge.target_id == "agent"
        assert "read" in edge.scopes
        assert "execute" in edge.scopes
        assert g.edge_count == 1

    def test_grant_with_expiry(self):
        g = ConsentGraph()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        edge = g.grant("owner", "agent", {"read"}, expires_at=future)
        assert edge.expires_at == future

    def test_grant_with_conditions(self):
        g = ConsentGraph()
        cond = ConsentCondition(expression="role == 'admin'")
        edge = g.grant("owner", "agent", {"read"}, conditions=[cond])
        assert len(edge.conditions) == 1

    def test_grant_delegatable(self):
        g = ConsentGraph()
        edge = g.grant(
            "owner",
            "agent",
            {"read"},
            delegatable=True,
            max_delegation_depth=2,
        )
        assert edge.delegatable
        assert edge.max_delegation_depth == 2


class TestConsentGraphEvaluate:
    def test_direct_consent_granted(self):
        g = ConsentGraph()
        g.grant("owner", "agent", {"read", "execute"})
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
            )
        )
        assert decision.granted
        assert len(decision.path) == 1

    def test_no_consent_denied(self):
        g = ConsentGraph()
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
            )
        )
        assert not decision.granted

    def test_wrong_scope_denied(self):
        g = ConsentGraph()
        g.grant("owner", "agent", {"read"})
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="execute",
            )
        )
        assert not decision.granted

    def test_revoked_consent_denied(self):
        g = ConsentGraph()
        edge = g.grant("owner", "agent", {"read"})
        g.revoke(edge.edge_id)
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
            )
        )
        assert not decision.granted

    def test_expired_consent_denied(self):
        g = ConsentGraph()
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        g.grant("owner", "agent", {"read"}, expires_at=past)
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
            )
        )
        assert not decision.granted

    def test_condition_met_grants(self):
        g = ConsentGraph()
        cond = ConsentCondition(expression="role == 'admin'")
        g.grant("owner", "agent", {"read"}, conditions=[cond])
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
                context={"role": "admin"},
            )
        )
        assert decision.granted

    def test_condition_not_met_denies(self):
        g = ConsentGraph()
        cond = ConsentCondition(expression="role == 'admin'")
        g.grant("owner", "agent", {"read"}, conditions=[cond])
        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent",
                scope="read",
                context={"role": "user"},
            )
        )
        assert not decision.granted


class TestConsentGraphGroups:
    def test_group_consent(self):
        g = ConsentGraph()
        g.add_node(ConsentNode("group-1", NodeType.GROUP, "Trusted Agents"))
        g.add_to_group("group-1", "agent-1")
        g.grant("owner", "group-1", {"read"})

        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent-1",
                scope="read",
            )
        )
        assert decision.granted

    def test_source_group_consent(self):
        g = ConsentGraph()
        g.add_node(ConsentNode("owners-group", NodeType.GROUP))
        g.add_to_group("owners-group", "owner-1")
        g.grant("owners-group", "agent-1", {"read"})

        decision = g.evaluate(
            ConsentQuery(
                source_id="owner-1",
                target_id="agent-1",
                scope="read",
            )
        )
        assert decision.granted

    def test_group_members(self):
        g = ConsentGraph()
        g.add_to_group("g1", "a1")
        g.add_to_group("g1", "a2")
        members = g.get_group_members("g1")
        assert members == {"a1", "a2"}

    def test_groups_for_node(self):
        g = ConsentGraph()
        g.add_to_group("g1", "a1")
        g.add_to_group("g2", "a1")
        groups = g.get_groups_for_node("a1")
        assert groups == {"g1", "g2"}

    def test_remove_from_group(self):
        g = ConsentGraph()
        g.add_to_group("g1", "a1")
        g.remove_from_group("g1", "a1")
        assert g.get_group_members("g1") == set()


class TestConsentGraphDelegation:
    def test_delegate_consent(self):
        g = ConsentGraph()
        parent = g.grant(
            "owner",
            "agent-1",
            {"read", "execute"},
            delegatable=True,
            max_delegation_depth=2,
        )
        child = g.delegate(parent.edge_id, "agent-2")
        assert child is not None
        assert child.delegation_depth == 1
        assert child.scopes == {"read", "execute"}

        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="agent-2",
                scope="read",
            )
        )
        assert decision.granted

    def test_delegate_with_scope_restriction(self):
        g = ConsentGraph()
        parent = g.grant(
            "owner",
            "agent-1",
            {"read", "execute"},
            delegatable=True,
        )
        child = g.delegate(parent.edge_id, "agent-2", scopes={"read"})
        assert child is not None
        assert child.scopes == {"read"}

    def test_delegate_cannot_expand_scopes(self):
        g = ConsentGraph()
        parent = g.grant(
            "owner",
            "agent-1",
            {"read"},
            delegatable=True,
        )
        child = g.delegate(parent.edge_id, "agent-2", scopes={"read", "write"})
        assert child is None

    def test_delegate_not_delegatable(self):
        g = ConsentGraph()
        parent = g.grant("owner", "agent-1", {"read"}, delegatable=False)
        child = g.delegate(parent.edge_id, "agent-2")
        assert child is None

    def test_delegate_depth_limit(self):
        g = ConsentGraph()
        e1 = g.grant(
            "owner",
            "a1",
            {"read"},
            delegatable=True,
            max_delegation_depth=1,
        )
        e2 = g.delegate(e1.edge_id, "a2")
        assert e2 is not None
        # a2 is at depth 1 (which equals max), so can't delegate further
        e3 = g.delegate(e2.edge_id, "a3")
        assert e3 is None

    def test_delegate_chain_evaluation(self):
        g = ConsentGraph()
        e1 = g.grant(
            "owner",
            "a1",
            {"read"},
            delegatable=True,
            max_delegation_depth=3,
        )
        e2 = g.delegate(e1.edge_id, "a2")
        assert e2 is not None
        e3 = g.delegate(e2.edge_id, "a3")
        assert e3 is not None

        decision = g.evaluate(
            ConsentQuery(
                source_id="owner",
                target_id="a3",
                scope="read",
            )
        )
        assert decision.granted

    def test_delegate_revoked_parent(self):
        g = ConsentGraph()
        parent = g.grant(
            "owner",
            "a1",
            {"read"},
            delegatable=True,
        )
        g.revoke(parent.edge_id)
        child = g.delegate(parent.edge_id, "a2")
        assert child is None


class TestConsentGraphRevocation:
    def test_revoke_edge(self):
        g = ConsentGraph()
        edge = g.grant("owner", "agent", {"read"})
        assert g.revoke(edge.edge_id)
        assert g.get_edge(edge.edge_id).status == ConsentStatus.REVOKED

    def test_revoke_cascades_to_delegated(self):
        g = ConsentGraph()
        parent = g.grant(
            "owner",
            "a1",
            {"read"},
            delegatable=True,
        )
        child = g.delegate(parent.edge_id, "a2")
        assert child is not None

        g.revoke(parent.edge_id)
        assert g.get_edge(child.edge_id).status == ConsentStatus.REVOKED

    def test_revoke_nonexistent(self):
        g = ConsentGraph()
        assert not g.revoke("nonexistent")

    def test_revoke_all(self):
        g = ConsentGraph()
        g.grant("owner", "agent", {"read"})
        g.grant("owner", "agent", {"write"})
        count = g.revoke_all("owner", "agent")
        assert count == 2


class TestConsentGraphQueries:
    def test_get_consents_for(self):
        g = ConsentGraph()
        g.grant("owner1", "agent", {"read"})
        g.grant("owner2", "agent", {"write"})
        consents = g.get_consents_for("agent")
        assert len(consents) == 2

    def test_get_consents_from(self):
        g = ConsentGraph()
        g.grant("owner", "a1", {"read"})
        g.grant("owner", "a2", {"write"})
        consents = g.get_consents_from("owner")
        assert len(consents) == 2

    def test_audit_log(self):
        g = ConsentGraph()
        g.grant("owner", "agent", {"read"})
        log = g.get_audit_log()
        assert len(log) == 1
        assert log[0]["action"] == "grant"

    def test_audit_log_tracks_revoke(self):
        g = ConsentGraph()
        edge = g.grant("owner", "agent", {"read"})
        g.revoke(edge.edge_id)
        log = g.get_audit_log()
        assert len(log) == 2
        assert log[0]["action"] == "revoke"
        assert log[1]["action"] == "grant"
